"""Persistent, read-only Pi runtime discovery state for the bundled daemon."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.pi_provider_discovery import (
    PiDiscoveryCycleOutcome,
    PiDiscoveryResult,
    PiDiscoveryState,
    _resolve_pi,
    build_pi_registry,
    discover_pi,
)
from personal_ai_orchestrator.pi_provider_registry_store import (
    PiRegistryLoadStatus,
    load,
    save,
)


@dataclass(frozen=True)
class PiRuntimeDiscoveryStatus:
    discovery_state: str
    last_discovered_at: str | None
    provider_count: int
    configured_family_count: int
    execution_target_count: int
    catalog_snapshot_id: str | None
    source_method: str
    last_error_code: str | None


@dataclass
class _PiRuntimeState:
    registry: ModelRegistry
    last_result: PiDiscoveryResult | None
    status: PiRuntimeDiscoveryStatus
    load_status: PiRegistryLoadStatus
    last_error_code: str | None


class PiProviderRegistryManager:
    """Owns persisted Pi discovery without performing implicit boot-time refresh."""

    def __init__(
        self,
        *,
        runtime_state_root: Path,
        pi_path: Path | None = None,
    ) -> None:
        self._runtime_state_root = runtime_state_root
        self._pi_path = pi_path
        self._lock = threading.RLock()
        self._refresh_in_flight = False
        self._discovery_cycle_count = 0
        self._state = self._rehydrate_state()

    def _empty_state(
        self,
        *,
        load_status: PiRegistryLoadStatus,
        discovery_state: PiDiscoveryState = PiDiscoveryState.PENDING,
        error_code: str | None = None,
    ) -> _PiRuntimeState:
        return _PiRuntimeState(
            registry=ModelRegistry(),
            last_result=None,
            status=PiRuntimeDiscoveryStatus(
                discovery_state=discovery_state.value,
                last_discovered_at=None,
                provider_count=0,
                configured_family_count=0,
                execution_target_count=0,
                catalog_snapshot_id=None,
                source_method="pi_cli_inspection",
                last_error_code=error_code,
            ),
            load_status=load_status,
            last_error_code=error_code,
        )

    def _rehydrate_state(self) -> _PiRuntimeState:
        outcome = load(self._runtime_state_root)
        if outcome.status is PiRegistryLoadStatus.MISSING:
            return self._empty_state(load_status=outcome.status)
        if outcome.persisted is None:
            return self._empty_state(
                load_status=outcome.status,
                discovery_state=PiDiscoveryState.FAILED,
                error_code=outcome.error_code,
            )
        try:
            result = PiDiscoveryResult.from_dict(outcome.persisted.payload)
            registry = build_pi_registry(result)
        except (AssertionError, KeyError, TypeError, ValueError):
            return self._empty_state(
                load_status=PiRegistryLoadStatus.SANITIZATION_REJECTED,
                error_code="PERSISTED_PI_REGISTRY_REJECTED",
            )
        snapshot_id = next(iter(registry.catalog_snapshots), None)
        return _PiRuntimeState(
            registry=registry,
            last_result=result,
            status=PiRuntimeDiscoveryStatus(
                discovery_state=result.state.value,
                last_discovered_at=result.discovered_at.isoformat(),
                provider_count=result.provider_count(),
                configured_family_count=result.configured_family_count,
                execution_target_count=result.execution_target_count(),
                catalog_snapshot_id=snapshot_id,
                source_method=result.source_method,
                last_error_code=result.last_error_code,
            ),
            load_status=PiRegistryLoadStatus.LOADED,
            last_error_code=result.last_error_code,
        )

    def registry(self) -> ModelRegistry:
        with self._lock:
            return self._state.registry

    def last_discovery_result(self) -> PiDiscoveryResult | None:
        with self._lock:
            return self._state.last_result

    def status(self) -> PiRuntimeDiscoveryStatus:
        with self._lock:
            return self._state.status

    def load_status(self) -> PiRegistryLoadStatus:
        with self._lock:
            return self._state.load_status

    def discovery_cycle_count(self) -> int:
        with self._lock:
            return self._discovery_cycle_count

    def runtime_available(self, execution_target_id: str) -> bool:
        with self._lock:
            target = self._state.registry.execution_targets.get(execution_target_id)
            result = self._state.last_result
        if target is None or target.runtime_id != "pi" or not target.enabled or result is None:
            return False
        executable = _resolve_pi(self._pi_path)
        if executable is None:
            return False
        model_provider, separator, model_name = target.model_sku_id.partition("/")
        if not separator:
            return False
        for provider in result.providers:
            if provider.provider_id != model_provider:
                continue
            return (
                provider.auth_status.value == "READY"
                and model_name in provider.model_skus
            )
        return False

    def refresh(self) -> PiRuntimeDiscoveryStatus:
        with self._lock:
            if self._refresh_in_flight:
                return self._state.status
            self._refresh_in_flight = True
            self._discovery_cycle_count += 1
        try:
            outcome: PiDiscoveryCycleOutcome = discover_pi(pi_path=self._pi_path)
            with self._lock:
                if outcome.result is not None and outcome.error_code is None:
                    result = outcome.result
                    registry = build_pi_registry(result)
                    save(result, runtime_state_root=self._runtime_state_root)
                    snapshot_id = next(iter(registry.catalog_snapshots), None)
                    self._state = _PiRuntimeState(
                        registry=registry,
                        last_result=result,
                        status=PiRuntimeDiscoveryStatus(
                            discovery_state=result.state.value,
                            last_discovered_at=result.discovered_at.isoformat(),
                            provider_count=result.provider_count(),
                            configured_family_count=result.configured_family_count,
                            execution_target_count=result.execution_target_count(),
                            catalog_snapshot_id=snapshot_id,
                            source_method=result.source_method,
                            last_error_code=None,
                        ),
                        load_status=PiRegistryLoadStatus.LOADED,
                        last_error_code=None,
                    )
                    return self._state.status

                failed = PiRuntimeDiscoveryStatus(
                    discovery_state=PiDiscoveryState.FAILED.value,
                    last_discovered_at=self._state.status.last_discovered_at,
                    provider_count=self._state.status.provider_count,
                    configured_family_count=self._state.status.configured_family_count,
                    execution_target_count=self._state.status.execution_target_count,
                    catalog_snapshot_id=self._state.status.catalog_snapshot_id,
                    source_method=self._state.status.source_method,
                    last_error_code=outcome.error_code,
                )
                self._state.status = failed
                self._state.last_error_code = outcome.error_code
                return failed
        finally:
            with self._lock:
                self._refresh_in_flight = False


__all__ = [
    "PiProviderRegistryManager",
    "PiRuntimeDiscoveryStatus",
]
