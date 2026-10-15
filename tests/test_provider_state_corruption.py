"""Corrupt local provider state must fail closed without breaking product boot."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from personal_ai_orchestrator import (
    pi_provider_registry_store as pi_store,
)
from personal_ai_orchestrator import (
    product_daemon,
)
from personal_ai_orchestrator import (
    provider_connections as connections,
)
from personal_ai_orchestrator import (
    provider_registry_store as provider_store,
)
from personal_ai_orchestrator.pi_provider_registry_manager import PiProviderRegistryManager
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.runtime_config import default_application_support_layout
from tests.test_pi_product_wiring import _pi_result
from tests.test_product_daemon import _result

FILENAMES = (
    connections.CONNECTIONS_FILENAME,
    provider_store.PERSISTED_FILENAME,
    pi_store.PERSISTED_FILENAME,
)
CORRUPT_CONTENTS = (
    pytest.param(b"not json", id="malformed-json"),
    pytest.param(b"\xff", id="invalid-utf8"),
    pytest.param(b'{"schema_version":1,"value":"\xc3"}', id="truncated-utf8"),
)


@pytest.fixture(autouse=True)
def forbid_external_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("corrupt-state tests must not discover, spawn a process, or open a socket")

    monkeypatch.setattr("personal_ai_orchestrator.provider_registry_manager.discover", unexpected)
    monkeypatch.setattr(
        "personal_ai_orchestrator.pi_provider_registry_manager.discover_pi", unexpected
    )
    monkeypatch.setattr("subprocess.Popen", unexpected)
    monkeypatch.setattr("socket.socket", unexpected)


def _write_corrupt_state(root: Path, filename: str, payload: bytes) -> None:
    # Keep the other snapshots valid so no missing-state bootstrap is eligible.
    provider_store.save(_result(), runtime_state_root=root)
    pi_store.save(_pi_result(), runtime_state_root=root)
    connections.save_connections(connections.ProviderConnectionRegistry(), runtime_state_root=root)
    (root / filename).write_bytes(payload)


def _assert_manager_state(manager: ProviderRegistryManager, filename: str) -> None:
    pi_manager = manager.pi_runtime_manager()
    assert pi_manager is not None
    assert manager.discovery_cycle_count() == pi_manager.discovery_cycle_count() == 0
    assert not manager.bootstrap_if_empty(
        catalog_snapshot_id=provider_store.EMPTY_BOOTSTRAP_SNAPSHOT_ID
    )
    assert not pi_manager.bootstrap_if_missing()
    if filename == provider_store.PERSISTED_FILENAME:
        assert manager.load_status() is provider_store.RegistryLoadStatus.CORRUPT
        assert manager.status().discovery_state == "FAILED"
        assert manager.status().last_error_code == "PERSISTED_REGISTRY_CORRUPT"
        assert manager.status().provider_count == 0
        assert manager.last_discovery_result() is None
    elif filename == pi_store.PERSISTED_FILENAME:
        assert pi_manager.load_status() is pi_store.PiRegistryLoadStatus.CORRUPT
        assert pi_manager.status().discovery_state == "FAILED"
        assert pi_manager.status().last_error_code == "PERSISTED_PI_REGISTRY_CORRUPT"
        assert pi_manager.registry().providers == {}
        assert pi_manager.registry().execution_targets == {}
    else:
        assert manager.load_status() is provider_store.RegistryLoadStatus.LOADED
        assert manager.registry().providers  # Catalog truth cannot restore owner consent.
        assert manager.connection_registry().connections == {}
        assert manager.connected_provider_ids() == frozenset()


@pytest.mark.parametrize("filename", FILENAMES)
@pytest.mark.parametrize("payload", CORRUPT_CONTENTS)
def test_corruption_preserves_loader_and_manager_contracts(
    tmp_path: Path, filename: str, payload: bytes
) -> None:
    _write_corrupt_state(tmp_path, filename, payload)

    if filename == connections.CONNECTIONS_FILENAME:
        assert connections.load_connections(tmp_path).connections == {}
    else:
        store = provider_store if filename == provider_store.PERSISTED_FILENAME else pi_store
        outcome = store.load(tmp_path)
        assert outcome.status.value == "CORRUPT"
        assert outcome.persisted is None
        assert outcome.source_path == tmp_path / filename
        assert outcome.error_code == (
            "PERSISTED_REGISTRY_CORRUPT"
            if store is provider_store
            else "PERSISTED_PI_REGISTRY_CORRUPT"
        )

    manager = ProviderRegistryManager(
        runtime_state_root=tmp_path,
        pi_runtime_manager=PiProviderRegistryManager(runtime_state_root=tmp_path),
    )
    _assert_manager_state(manager, filename)
    assert (tmp_path / filename).read_bytes() == payload


@pytest.mark.parametrize("filename", FILENAMES)
@pytest.mark.parametrize("payload", CORRUPT_CONTENTS)
def test_product_main_reaches_daemon_with_corrupt_state_without_rediscovery(
    monkeypatch: pytest.MonkeyPatch, filename: str, payload: bytes
) -> None:
    captured: list[ProviderRegistryManager] = []

    def fake_daemon_main(_argv, *, provider_registry_manager):
        captured.append(provider_registry_manager)
        return 0

    monkeypatch.setattr(product_daemon, "daemon_main", fake_daemon_main)
    monkeypatch.setattr(product_daemon, "ensure_execution_policies", lambda _layout: None)
    # The real bootstrap validates macOS's short AF_UNIX path limit.
    with TemporaryDirectory(prefix="pao-corrupt-") as directory:
        home = Path(directory)
        layout = default_application_support_layout(home)
        _write_corrupt_state(layout.runtime_state_root, filename, payload)

        assert product_daemon.main(["--home", str(home), "--port", "0"]) == 0

        assert len(captured) == 1
        _assert_manager_state(captured[0], filename)
        assert (layout.runtime_state_root / filename).read_bytes() == payload
