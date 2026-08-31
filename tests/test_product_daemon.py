import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.model_registry import Account, ModelRegistry, Provider
from personal_ai_orchestrator.product_daemon import build_daemon_argv, default_product_config
from personal_ai_orchestrator.runtime_config import (
    RuntimeConfig,
    default_application_support_layout,
)


def _short_home(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def test_default_product_config_is_credential_free_setup_required() -> None:
    config = default_product_config()
    assert config.registry.providers == {}
    assert config.registry.accounts == {}
    assert config.catalog_snapshot_id == "product-bootstrap-empty-registry-v1"


def test_product_daemon_builds_control_only_daemon_argv() -> None:
    layout = default_application_support_layout(Path("/Users/example"))
    argv = build_daemon_argv(layout, host="127.0.0.1", port=8765)
    assert argv == [
        "--config",
        str(layout.runtime_config),
        "--state-db",
        str(layout.state_db),
        "--runtime-state-root",
        str(layout.runtime_state_root),
        "--control-socket",
        str(layout.socket_path),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--control-only",
    ]


def test_product_daemon_bootstraps_runtime_and_serves_control_plane() -> None:
    home = _short_home("pao-product-")
    layout = default_application_support_layout(home)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.product_daemon",
            "--home",
            str(home),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not layout.socket_path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=5)
                raise AssertionError(f"product daemon exited early: {out}\n{err}")
            if time.monotonic() > deadline:
                raise AssertionError("product daemon never created the control socket")
            time.sleep(0.1)

        client = ControlPlaneClient(layout.socket_path)
        health = client.health()
        assert health.status == "ok"
        assert health.api_version == "v1"
        task = client.submit(
            task_id="product-smoke",
            request_id="product-smoke-req",
            intent="product daemon smoke",
        )
        assert task.state == "SUBMITTED"
        assert layout.runtime_config.exists()
        assert layout.state_db.exists()
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=15)
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0
    assert not layout.socket_path.exists()


def test_product_daemon_rejects_existing_runtime_config_with_credential_ref() -> None:
    home = _short_home("pao-product-bad-")
    layout = default_application_support_layout(home)
    layout.runtime_config.parent.mkdir(parents=True)
    config = RuntimeConfig(
        catalog_snapshot_id="catalog-bad",
        registry=ModelRegistry(
            providers={"p": Provider(id="p", display_name="Provider")},
            accounts={
                "a": Account(
                    id="a",
                    provider_id="p",
                    label="account",
                    credential_ref="secret-ref",
                )
            },
        ),
    )
    layout.runtime_config.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.product_daemon",
            "--home",
            str(home),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 78
    assert result.stderr.strip() == "pao-daemon: runtime_config_invalid"
    assert "secret-ref" not in result.stderr
