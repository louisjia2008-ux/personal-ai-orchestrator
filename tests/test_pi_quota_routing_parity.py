"""Regression: Pi execution, quota collection and quota UI share connection truth.

Pi intentionally has no ordinary persisted ProviderConnection row. Its provider
surface becomes routing-connected when the Pi runtime reports READY auth plus a
catalogued model. The quota service and the Control API quota card must both use
that runtime truth without persisting a fake owner connection.
"""

from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.daemon import build_control_service
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.pi_provider_discovery import (
    PiAuthStatus,
    PiDiscoveryResult,
    PiDiscoveryState,
    PiProviderDiscovery,
    build_pi_registry,
)
from personal_ai_orchestrator.provider_connections import ProviderConnectionRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


class _PiRuntime:
    def __init__(self) -> None:
        provider = PiProviderDiscovery(
            provider_id="zai-coding-plan",
            runtime_provider_id="zai",
            display_name="GLM / Z.AI",
            auth_status=PiAuthStatus.READY,
            model_skus=("glm-5.3",),
            observed_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
        self.result = PiDiscoveryResult(
            discovered_at=provider.observed_at,
            pi_path="/fake/pi",
            pi_version="0.test",
            providers=(provider,),
            state=PiDiscoveryState.DISCOVERED,
            configured_family_count=1,
        )
        self._registry = build_pi_registry(self.result)

    def registry(self):
        return self._registry

    def last_discovery_result(self):
        return self.result

    def runtime_available(self, _target_id: str) -> bool:
        return True


class _PiOnlyProviderManager:
    """Expose Pi through routing truth while keeping the owner registry empty."""

    def __init__(self) -> None:
        self.verified_lookup_was_set = False
        self.pi = _PiRuntime()

    def registry(self) -> ModelRegistry:
        return self.pi.registry()

    def set_verified_execution_lookup(self, _lookup) -> None:
        self.verified_lookup_was_set = True

    def connected_provider_ids(self) -> frozenset[str]:
        return frozenset()

    def routing_connected_provider_ids(self) -> frozenset[str]:
        return frozenset({"zai-coding-plan"})

    def connection_registry(self) -> ProviderConnectionRegistry:
        return ProviderConnectionRegistry()

    def pi_runtime_manager(self):
        return self.pi

    def runtime_available(self, target_id: str) -> bool:
        return self.pi.runtime_available(target_id)


def test_control_service_quota_tracks_pi_routing_connections(tmp_path: Path) -> None:
    manager = _PiOnlyProviderManager()
    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    service = build_control_service(
        config=RuntimeConfig(catalog_snapshot_id="test", registry=ModelRegistry()),
        state_db=tmp_path / "state.sqlite3",
        runtime_state_root=runtime_state_root,
        provider_registry_manager=manager,  # type: ignore[arg-type]
    )
    try:
        assert service.quota_refresh_service is not None
        observations = service.quota_refresh_service.observations()
        assert [item.provider_id for item in observations] == ["zai-coding-plan"]

        overview = service.quota()
        assert overview.summary.connected_provider_count == 1
        assert [card.provider_id for card in overview.providers] == ["zai-coding-plan"]
        assert overview.providers[0].connection_state == "CONNECTED"

        # Runtime projection did not materialise a persistent owner connection.
        assert manager.connection_registry().connections == {}
        assert manager.verified_lookup_was_set is True
    finally:
        service.store.close()
