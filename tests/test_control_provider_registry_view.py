from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.control_provider_registry_view import (
    ControlPlaneProviderRegistryView,
)
from personal_ai_orchestrator.pi_provider_discovery import (
    PiAuthStatus,
    PiDiscoveryResult,
    PiDiscoveryState,
    PiProviderDiscovery,
    build_pi_registry,
)
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager


class _FakePiRuntimeManager:
    def __init__(self, *, ready: bool = True, runtime_available: bool = True) -> None:
        provider = PiProviderDiscovery(
            provider_id="zai-coding-plan",
            runtime_provider_id="zai",
            display_name="GLM / Z.AI",
            auth_status=PiAuthStatus.READY if ready else PiAuthStatus.NOT_READY,
            model_skus=("glm-5.3",),
            observed_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
        self._result = PiDiscoveryResult(
            discovered_at=provider.observed_at,
            pi_path="/fake/pi",
            pi_version="0.test",
            providers=(provider,),
            state=PiDiscoveryState.DISCOVERED,
            configured_family_count=1,
        )
        self._registry = build_pi_registry(self._result)
        self._runtime_available = runtime_available

    def registry(self):
        return self._registry

    def last_discovery_result(self):
        return self._result

    def runtime_available(self, _target_id: str) -> bool:
        return self._runtime_available


def _manager(tmp_path: Path, pi_manager: _FakePiRuntimeManager) -> ProviderRegistryManager:
    return ProviderRegistryManager(
        runtime_state_root=tmp_path,
        pi_runtime_manager=pi_manager,  # type: ignore[arg-type]
    )


def test_ready_pi_is_ephemeral_control_connection_not_persisted(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakePiRuntimeManager())
    assert manager.connection_registry().connections == {}
    assert manager.connection_projection().connected == ()

    projected = ControlPlaneProviderRegistryView(manager).connection_registry()
    connection = projected.connections["zai-coding-plan"]

    assert connection.connection_state.value == "CONNECTED"
    assert connection.auth_state.value == "AUTHENTICATED"
    assert connection.runtime_state.value == "AVAILABLE"
    assert connection.execution_verified is False
    assert connection.plan_surface == "Coding Plan"
    assert connection.model_skus == ("glm-5.3",)

    # Read-only projection: nothing was written back to the owner registry.
    assert manager.connection_registry().connections == {}
    assert manager.connection_projection().connected == ()


def test_not_ready_pi_is_not_projected_as_connected(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakePiRuntimeManager(ready=False))
    projected = ControlPlaneProviderRegistryView(manager).connection_registry()
    assert projected.connections == {}


def test_ready_pi_with_unavailable_runtime_is_visible_but_not_healthy(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakePiRuntimeManager(runtime_available=False))
    projected = ControlPlaneProviderRegistryView(manager).connection_registry()
    connection = projected.connections["zai-coding-plan"]
    assert connection.connection_state.value == "CONNECTED"
    assert connection.runtime_state.value == "UNAVAILABLE"
