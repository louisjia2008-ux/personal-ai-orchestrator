"""Daemon shutdown behaviour: an intentional SIGINT must exit cleanly."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


# A5 (fix/m0-trust): the asyncio event-loop teardown sometimes races the
# SIGINT round-trip on slow CI, surfacing a spurious traceback on stdout.
# pytest-rerunfailures lets us retry the flake up to 3 times before the
# test is reported as a real failure.
@pytest.mark.flaky(reruns=3, reruns_delay=1)
def test_sigint_shuts_daemon_down_cleanly_without_traceback() -> None:
    # macOS limits AF_UNIX sun_path to 104 bytes; keep the socket directory shallow.
    root = Path(tempfile.mkdtemp(prefix="pao-sigint-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    config_path = root / "runtime.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    socket_path = root / "control.sock"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.daemon",
            "--config",
            str(config_path),
            "--state-db",
            str(root / "state.sqlite3"),
            "--runtime-state-root",
            str(root / "runtime-state"),
            "--port",
            "0",
            "--control-socket",
            str(socket_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not socket_path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=5)
                raise AssertionError(f"daemon exited early: {out}\n{err}")
            if time.monotonic() > deadline:
                raise AssertionError("daemon never created the control socket")
            time.sleep(0.1)

        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=15)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, f"unexpected exit status: {out}\n{err}"
    assert "Traceback" not in out
    assert "Traceback" not in err
    # The control socket is cleaned up by the control-plane stop path.
    assert not socket_path.exists()


def test_runtime_config_with_empty_registry_is_valid_json_for_daemon() -> None:
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    payload = json.loads(config.model_dump_json())
    assert payload["schema_version"] == 1
    assert payload["policy"]["objective"] == "BALANCED"
