import json
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.model_registry import Account, ModelRegistry, Provider
from personal_ai_orchestrator.product_daemon import build_daemon_argv, default_product_config
from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
)
from personal_ai_orchestrator.provider_registry_store import (
    RegistryLoadStatus,
    registry_path,
    save,
)
from personal_ai_orchestrator.runtime_config import (
    RuntimeConfig,
    default_application_support_layout,
)


def _short_home(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "product@example.invalid")
    _git(path, "config", "user.name", "Product")
    (path / "README.md").write_text("# product\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _result(*, providers: tuple[ProviderDiscovery, ...] | None = None) -> DiscoveryResult:
    records = providers
    if records is None:
        records = (
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=(),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_region_verified=True,
                credential_plan_surface_verified=True,
                credential_scope_verified=True,
                execution_verified=False,
            ),
        )
    return DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="fixture",
        source_method="fixture",
        providers=records,
        state=DiscoveryState.DISCOVERED if records else DiscoveryState.EMPTY,
        configured_family_count=5,
        catalog_discovered_provider_count=sum(1 for p in records if p.catalog_discovered),
        credential_evidence_provider_count=sum(1 for p in records if p.credential_evidence_present),
    )


def _run_main_without_server(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    captured = {}

    def _fake_daemon_main(argv, *, provider_registry_manager=None):
        captured["argv"] = argv
        captured["manager"] = provider_registry_manager
        return 0

    monkeypatch.setattr("personal_ai_orchestrator.product_daemon.daemon_main", _fake_daemon_main)
    import personal_ai_orchestrator.product_daemon as product_daemon

    assert product_daemon.main(["--home", str(home), "--port", "0"]) == 0
    return captured["manager"]


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


def test_product_daemon_first_boot_success_discovers_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-success-")
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        return DiscoveryCycleOutcome(result=_result(), error_code=None, error_message=None)

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)

    assert calls[0] == 1
    assert manager.discovery_cycle_count() == 1
    assert manager.status().discovery_state == "DISCOVERED"


def test_product_daemon_first_boot_failure_discovers_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-failure-")
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        return DiscoveryCycleOutcome(
            result=None,
            error_code="OPENCODE_PROVIDERS_LIST_FAILED",
            error_message="fixture failure",
        )

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)

    assert calls[0] == 1
    assert manager.discovery_cycle_count() == 1
    assert manager.status().discovery_state == "FAILED"
    assert manager.status().last_error_code == "OPENCODE_PROVIDERS_LIST_FAILED"


def test_product_daemon_subsequent_valid_boot_discovers_zero_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-subsequent-")
    layout = default_application_support_layout(home)
    save(_result(), runtime_state_root=layout.runtime_state_root)
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        raise AssertionError("discover must not run for a valid persisted boot")

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)

    assert calls[0] == 0
    assert manager.discovery_cycle_count() == 0
    assert manager.load_status() is RegistryLoadStatus.LOADED
    assert manager.status().discovery_state == "DISCOVERED"


def test_product_daemon_corrupt_snapshot_does_not_auto_discover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-corrupt-")
    layout = default_application_support_layout(home)
    layout.runtime_state_root.mkdir(parents=True, exist_ok=True)
    registry_path(layout.runtime_state_root).write_text("not json", encoding="utf-8")
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        raise AssertionError("discover must not run for a corrupt persisted snapshot")

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)

    assert calls[0] == 0
    assert manager.discovery_cycle_count() == 0
    assert manager.load_status() is RegistryLoadStatus.CORRUPT
    assert manager.status().discovery_state == "FAILED"
    assert manager.status().last_error_code == "PERSISTED_REGISTRY_CORRUPT"


def test_product_daemon_unsupported_schema_does_not_auto_discover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-schema-")
    layout = default_application_support_layout(home)
    layout.runtime_state_root.mkdir(parents=True, exist_ok=True)
    registry_path(layout.runtime_state_root).write_text(
        json.dumps({"schema_version": 999, "providers": []}),
        encoding="utf-8",
    )
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        raise AssertionError("discover must not run for an unsupported persisted snapshot")

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)

    assert calls[0] == 0
    assert manager.discovery_cycle_count() == 0
    assert manager.load_status() is RegistryLoadStatus.UNSUPPORTED_SCHEMA
    assert manager.status().discovery_state == "FAILED"
    assert manager.status().last_error_code == "PERSISTED_REGISTRY_UNSUPPORTED_SCHEMA"


def test_product_daemon_explicit_refresh_discovers_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _short_home("pao-product-refresh-")
    layout = default_application_support_layout(home)
    save(_result(), runtime_state_root=layout.runtime_state_root)
    calls = [0]

    def _discover(**_kwargs):
        calls[0] += 1
        return DiscoveryCycleOutcome(result=_result(), error_code=None, error_message=None)

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", _discover)
    manager = _run_main_without_server(home, monkeypatch)
    assert calls[0] == 0

    manager.refresh()
    assert calls[0] == 1
    assert manager.discovery_cycle_count() == 1


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
        repo = _make_repo(home / "project")
        project = client.register_project(path=str(repo), display_name="Product Smoke")
        task = client.submit(
            task_id="product-smoke",
            request_id="product-smoke-req",
            project_id=project.project_id,
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
