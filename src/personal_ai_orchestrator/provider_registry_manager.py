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
    """Owns the *dynamic* provider registry for the lifetime of the daemon.

    Startup policy (P4.2.4-A.1 §22 single-startup-contract):

    1. The constructor attempts to load a previously persisted sanitized
       snapshot from disk via :func:`load`. If a *valid* snapshot exists,
       the in-memory :class:`ModelRegistry` is fully reconstructed from
       that snapshot **without** invoking the OpenCode CLI. This is the
       "subsequent boot" path.
    2. If no snapshot exists, the manager starts in
       :attr:`DiscoveryState.PENDING` with an empty registry. The first
       call to :meth:`refresh` performs exactly **one** discovery cycle
       — the "cold first boot" path.
    3. An explicit user-initiated refresh (Dashboard "Refresh providers"
       button → ``POST /v1/providers/refresh``) calls :meth:`refresh` and
       performs exactly one cycle. Concurrent calls coalesce.
    4. There is **no** implicit boot-time refresh path. The manager is
       fully informative as soon as the persisted snapshot is loaded, so
       the daemon never needs to invoke the OpenCode CLI purely for the
       sake of populating the in-memory state.
    """

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
        self._discovery_cycle_count = 0
        self._state = self._rehydrate_state()

    def _rehydrate_state(self) -> _RuntimeState:
        """Build the initial runtime state from disk, if a valid snapshot
        is present.

        A valid snapshot produces a fully-populated :class:`ModelRegistry`
        (providers, accounts, models, execution targets, catalog
        snapshots) reconstructed by :func:`build_registry`. The Discovery
        module's subprocess is never invoked during this step.
        """

        persisted = load(self._runtime_state_root)
        if persisted is None:
            return _RuntimeState(
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
        try:
            result = DiscoveryResult.from_dict(persisted.payload)
        except (KeyError, ValueError, TypeError):
            # Schema drift or corruption — the persist module already
            # rejected unknown schemas; here we also reject payloads that
            # claim ``schema_version == CURRENT_SCHEMA_VERSION`` but do
            # not satisfy the typed model. Fail closed: PENDING state,
            # empty registry, no status metadata.
            return _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.PENDING.value,
                    last_discovered_at=None,
                    provider_count=0,
                    execution_target_count=0,
                    last_error_code="PERSISTED_SNAPSHOT_REJECTED",
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code="PERSISTED_SNAPSHOT_REJECTED",
            )
        try:
            registry = build_registry(result)
        except Exception:
            return _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.PENDING.value,
                    last_discovered_at=None,
                    provider_count=0,
                    execution_target_count=0,
                    last_error_code="PERSISTED_REGISTRY_BUILD_FAILED",
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code="PERSISTED_REGISTRY_BUILD_FAILED",
            )
        return _RuntimeState(
            registry=registry,
            last_result=result,
            last_status=_status_from_result(result),
            last_error_code=persisted.last_error_code,
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

    def discovery_cycle_count(self) -> int:
        """Return the cumulative number of ``refresh`` invocations.

        Used by the single-startup-contract tests to verify that:

        - subsequent boot does not run a discovery cycle, and
        - an explicit refresh runs exactly one cycle.
        """

        with self._lock:
            return self._discovery_cycle_count

    def bootstrap_if_empty(self, *, catalog_snapshot_id: str | None) -> bool:
        """Upgrade from the empty-bootstrap snapshot if needed.

        Returns ``True`` if a discovery cycle ran during bootstrap.

        Per P4.2.4-A.1 §22 single-startup-contract: bootstrap only runs a
        discovery cycle when *both* conditions hold:

        - the static runtime config still carries the legacy
          ``EMPTY_BOOTSTRAP_SNAPSHOT_ID`` (i.e. this is the cold first
          launch); AND
        - the manager has no persisted sanitized snapshot on disk to
          rehydrate from.

        If the persisted snapshot already exists, the constructor
        already rehydrated the in-memory state and the bootstrap is a
        no-op. There is no implicit "refresh on every boot".
        """

        from personal_ai_orchestrator.provider_registry_store import (
            EMPTY_BOOTSTRAP_SNAPSHOT_ID,
        )

        if catalog_snapshot_id != EMPTY_BOOTSTRAP_SNAPSHOT_ID:
            return False
        with self._lock:
            already_loaded = self._state.last_result is not None
        if already_loaded:
            return False
        return self.refresh() is not None

    def refresh(self) -> ProviderDiscoveryStatus | None:
        """Run a fresh discovery cycle.

        Coalesces concurrent invocations so the OpenCode CLI is invoked
        at most once per cycle even under load. Each ``refresh`` call is
        recorded in :attr:`discovery_cycle_count` so tests can verify the
        single-startup-contract.

        Returns the freshly-computed status, or ``None`` if the cycle
        failed (in which case the previous status remains visible to the
        Dashboard along with an updated ``last_error_code``).
        """

        with self._lock:
            if self._refresh_in_flight:
                return self._state.last_status
            self._refresh_in_flight = True
            self._discovery_cycle_count += 1

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