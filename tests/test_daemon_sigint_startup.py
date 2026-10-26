"""SIGINT must be deterministic at startup; never retry a failed assertion."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import personal_ai_orchestrator.daemon as daemon
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


@pytest.mark.parametrize("mode", ["full", "control-only"])
@pytest.mark.parametrize(
    "gate",
    [
        "socket_bound",
        "control_constructed",
        "control_started",
        "supervisor_before",
        "supervisor_after",
    ],
)
def test_sigint_during_startup_cleans_all_resources(gate, mode):
    _assert_clean_sigint(gate, mode)


@pytest.mark.parametrize("mode", ["full", "control-only"])
def test_sigint_after_startup_still_shuts_down(mode):
    _assert_clean_sigint("active_full" if mode == "full" else "active_control_only", mode)


def _assert_clean_sigint(gate: str, mode: str) -> None:
    root, result = _run_probe(gate, mode)
    evidence = f"first evidence: {root}\n{result.stderr.decode(errors='replace')}"
    assert result.returncode == 0, evidence
    assert b"Traceback" not in result.stdout + result.stderr, evidence
    receipt = json.loads((root / "probe-result.json").read_text())
    assert receipt["fired"] and receipt["result"] == 0, evidence
    _assert_resources_closed(root, receipt, evidence)


@pytest.mark.parametrize(
    "gate",
    [
        "error_control_before",
        "error_control_after",
        "error_supervisor_before",
        "error_supervisor_after",
    ],
)
def test_non_signal_startup_failure_is_not_swallowed_and_still_cleans_up(gate):
    root, result = _run_probe(gate, "full")
    evidence = f"first evidence: {root}\n{result.stderr.decode(errors='replace')}"
    assert result.returncode != 0 and b"startup_probe_failure" in result.stderr, evidence
    receipt = json.loads((root / "probe-result.json").read_text())
    _assert_resources_closed(root, receipt, evidence)


def _assert_resources_closed(root, receipt, evidence):
    assert receipt["signal_handler_restored"] and not receipt["threads_alive"], evidence
    assert not receipt["socket_remains"], evidence
    assert receipt["events"].count("routing:closed") == 1, evidence
    assert receipt["events"].count("control:closed") == 1, evidence
    with sqlite3.connect(root / "state.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _run_probe(gate: str, mode: str):
    # Shallow paths respect Darwin's AF_UNIX limit. Unique folders retain the
    # first exit's stdout/stderr and receipt even when an assertion fails.
    root = Path(tempfile.mkdtemp(prefix="pao-sigint-gate-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    (root / "runtime.json").write_text(config.model_dump_json(), encoding="utf-8")
    project = Path(daemon.__file__).resolve().parents[2]
    fixture = Path(__file__).parent / "fixtures" / "sigint_startup_probe.py"
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}}
    env.update(HOME=str(root), TMPDIR="/private/tmp", PYTHONPATH=str(project / "src"))
    try:
        result = subprocess.run(
            [sys.executable, str(fixture), gate, mode, str(root)],
            cwd=project,
            env=env,
            capture_output=True,
            timeout=12,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        (root / "first-exit.stdout").write_bytes(error.stdout or b"")
        (root / "first-exit.stderr").write_bytes(error.stderr or b"")
        raise AssertionError(f"SIGINT shutdown timed out; first evidence: {root}") from error
    (root / "first-exit.stdout").write_bytes(result.stdout)
    (root / "first-exit.stderr").write_bytes(result.stderr)
    return root, result
