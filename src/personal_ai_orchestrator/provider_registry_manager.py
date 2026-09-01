"""Provider-registry manager: runtime singleton that owns the *dynamic* registry.

The static ``runtime.json`` holds the legacy bootstrap snapshot. The
manager wraps:

- A :class:`~personal_ai_orchestrator.model_registry.ModelRegistry`
  rebuilt from the most recent :class:`DiscoveryResult`.
- A cached :class:`ProviderDiscoveryStatusView` snapshot.

Concurrency
-----------
The manager is intentionally simple. The control API handlers invoke
``refresh()`` and ``status()``; both are protected by a re-entrant
lock so two simultaneous refresh requests coalesce into a single
discovery cycle (§23: "avoid repeated provider calls").

Persistence
-----------
After every successful refresh the manager writes
``provider-registry.json`` so the next daemon boot picks up the same
sanitized snapshot without re-running OpenCode CLI inspection.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.provider_discovery import (
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    build_registry,
    discover,
)
from personal_ai_orchestrator.provider_registry_store import (
    load,
    save,
)


@dataclass(frozen=True)
class ProviderDiscoveryStatus:
    """Status projection returned to the Dashboard.

    Wire format mirrors the typed Control API view. The status is
    produced by :meth:`ProviderRegistryManager.status`.
    """

    discovery_state: str
    last_discovered_at: str | None
    provider_count: int
    execution_target_count: int
    last_error_code: str | None
    catalog_snapshot_id: str | None
    source_method: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_state": self.discovery_state,
            "last_discovered_at": self.last_discovered_at,
            "provider_count": self.provider_count,
            "execution_target_count": self.execution_target_count,
            "last_error_code": self.last_error_code,
            "catalog_snapshot_id": self.catalog_snapshot_id,
            "source_method": self.source_method,
        }


@dataclass
class _RuntimeState:
    """Mutable runtime state guarded by ``self._lock``."""

    registry: ModelRegistry
    last_result: DiscoveryResult | None
    last_status: ProviderDiscoveryStatus
    last_error_code: str | None


class ProviderRegistryManager:
    """Owns the *dynamic* provider registry for the lifetime of the daemon."""

    def __init__(
        self,
        *,
        runtime_state_root: Path,
        opencode_path: Path | None = None,
    ) -> None:
        self._runtime_state_root = runtime_state_root
        self._opencode_path = opencode_path
        self._lock = threading.RLock()
        self._refresh_in_flight = False
        # Best-effort load of the last persisted snapshot so a daemon
        # restart does not require re-running OpenCode CLI inspection
        # before the Dashboard becomes informative.
        persisted = load(runtime_state_root)
        if persisted is not None:
            self._state = _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=_status_from_persisted(persisted),
                last_error_code=persisted.last_error_code,
            )
        else:
            self._state = _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.PENDING.value,
                    last_discovered_at=None,
                    provider_count=0,
                    execution_target_count=0,
                    last_error_code=None,
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code=None,
            )

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def registry(self) -> ModelRegistry:
        """Return the current registry (read-only contract)."""

        with self._lock:
            return self._state.registry

    def status(self) -> ProviderDiscoveryStatus:
        with self._lock:
            return self._state.last_status

    def last_discovery_result(self) -> DiscoveryResult | None:
        with self._lock:
            return self._state.last_result

    def bootstrap_if_empty(self, *, catalog_snapshot_id: str | None) -> bool:
        """Upgrade from the empty-bootstrap snapshot if needed.

        Returns ``True`` if a discovery cycle ran during bootstrap.
        """

        from personal_ai_orchestrator.provider_registry_store import (
            EMPTY_BOOTSTRAP_SNAPSHOT_ID,
        )

        if catalog_snapshot_id != EMPTY_BOOTSTRAP_SNAPSHOT_ID:
            return False
        return self.refresh() is not None

    def refresh(self) -> ProviderDiscoveryStatus | None:
        """Run a fresh discovery cycle.

        Coalesces concurrent invocations so the OpenCode CLI is invoked
        at most once per cycle even under load.

        Returns the freshly-computed status, or ``None`` if the cycle
        failed (in which case the previous status remains visible to the
        Dashboard along with an updated ``last_error_code``).
        """

        with self._lock:
            if self._refresh_in_flight:
                return self._state.last_status
            self._refresh_in_flight = True

        try:
            outcome: DiscoveryCycleOutcome = discover(opencode_path=self._opencode_path)
            with self._lock:
                if outcome.result is not None and outcome.error_code is None:
                    registry = build_registry(outcome.result)
                    save(
                        outcome.result,
                        runtime_state_root=self._runtime_state_root,
                    )
                    status = _status_from_result(outcome.result)
                    self._state = _RuntimeState(
                        registry=registry,
                        last_result=outcome.result,
                        last_status=status,
                        last_error_code=None,
                    )
                    return status
                # Failure: surface the error code; keep the previous
                # registry so the Dashboard still has something to
                # show, but flip the status to FAILED.
                failed_status = ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.FAILED.value,
                    last_discovered_at=_isoformat(self._state.last_status.last_discovered_at),
                    provider_count=self._state.last_status.provider_count,
                    execution_target_count=self._state.last_status.execution_target_count,
                    last_error_code=outcome.error_code,
                    catalog_snapshot_id=self._state.last_status.catalog_snapshot_id,
                    source_method=self._state.last_status.source_method,
                )
                self._state.last_status = failed_status
                self._state.last_error_code = outcome.error_code
                return failed_status
        finally:
            with self._lock:
                self._refresh_in_flight = False


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _empty_registry() -> ModelRegistry:
    return ModelRegistry()


def _status_from_persisted(persisted: Any) -> ProviderDiscoveryStatus:
    payload = persisted.payload
    providers = payload.get("providers", []) if isinstance(payload, dict) else []
    target_count = sum(len(p.get("model_skus", []) or []) for p in providers)  # type: ignore[union-attr]
    return ProviderDiscoveryStatus(
        discovery_state=str(payload.get("discovery_state", "DISCOVERED")),
        last_discovered_at=_isoformat(payload.get("generated_at")) if payload else None,
        provider_count=len(providers),
        execution_target_count=target_count,
        last_error_code=payload.get("last_error_code") if payload else None,
        catalog_snapshot_id=payload.get("opencode_version"),
        source_method=payload.get("source_method"),
    )


def _status_from_result(result: DiscoveryResult) -> ProviderDiscoveryStatus:
    target_count = sum(len(p.model_skus) for p in result.providers)
    return ProviderDiscoveryStatus(
        discovery_state=result.state.value,
        last_discovered_at=result.discovered_at.isoformat(),
        provider_count=len(result.providers),
        execution_target_count=target_count,
        last_error_code=result.last_error_code,
        catalog_snapshot_id=result.opencode_version,
        source_method=result.source_method,
    )


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


__all__ = [
    "ProviderDiscoveryStatus",
    "ProviderRegistryManager",
]