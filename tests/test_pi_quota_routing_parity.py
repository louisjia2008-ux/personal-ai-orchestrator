"""Regression: quota collection must use the same routing-connected provider set as execution.

Pi intentionally has no ordinary persisted ProviderConnection row. Its provider
surface becomes routing-connected when the Pi runtime reports READY auth plus a
catalogued model. The quota service therefore cannot key off only the ordinary
connection registry or SUPERVISED_AUTO will see an empty quota observation set
for an otherwise runnable Pi target.
"""

from pathlib import Path

from personal_ai_orchestrator.daemon import build_control_service
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


class _PiOnlyProviderManager:
    """Minimal manager exposing Pi through routing truth, not ordinary connections."""

    def __init__(self) -> None:
        self.verified_lookup_was_set = False

    def registry(self) -> ModelRegistry:
        return ModelRegistry()

    def set_verified_execution_lookup(self, _lookup) -> None:
        self.verified_lookup_was_set = True

    def connected_provider_ids(self) -> frozenset[str]:
        # This is the legacy/incorrect source for quota refresh: Pi has no row here.
        return frozenset()

    def routing_connected_provider_ids(self) -> frozenset[str]:
        return frozenset({"zai-coding-plan"})


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
        assert manager.verified_lookup_was_set is True
    finally:
        service.store.close()
