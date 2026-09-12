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

import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.pi_provider_registry_manager import (
    PiProviderRegistryManager,
)
from personal_ai_orchestrator.provider_connections import (
    ProviderConnection,
    ProviderConnectionRegistry,
    ProviderImportCandidate,
    build_import_candidates,
    connect_from_discovery,
    disconnect_provider,
    import_connections,
    load_connections,
    project_connections,
    save_connections,
)
from personal_ai_orchestrator.provider_discovery import (
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    build_registry,
    discover,
)
from personal_ai_orchestrator.provider_registry_store import (
    RegistryLoadStatus,
    load,
    save,
)
from personal_ai_orchestrator.runtime_registry import merge_runtime_registries


@dataclass(frozen=True)
class ProviderDiscoveryStatus:
    """Status projection returned to the Dashboard.

    Wire format mirrors the typed Control API view. The status is
    produced by :meth:`ProviderRegistryManager.status`.
    """

    discovery_state: str
    last_discovered_at: str | None
    provider_count: int
    configured_family_count: int
    catalog_discovered_provider_count: int
    credential_evidence_provider_count: int
    execution_target_count: int
    last_error_code: str | None
    catalog_snapshot_id: str | None
    source_method: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_state": self.discovery_state,
            "last_discovered_at": self.last_discovered_at,
            "provider_count": self.provider_count,
            "configured_family_count": self.configured_family_count,
            "catalog_discovered_provider_count": self.catalog_discovered_provider_count,
            "credential_evidence_provider_count": self.credential_evidence_provider_count,
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
    load_status: RegistryLoadStatus


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
        audit: Any | None = None,
        pi_runtime_manager: PiProviderRegistryManager | None = None,
    ) -> None:
        self._runtime_state_root = runtime_state_root
        self._opencode_path = opencode_path
        self._lock = threading.RLock()
        self._refresh_in_flight = False
        self._discovery_cycle_count = 0
        self._connections = load_connections(runtime_state_root)
        # M1 WP4: optional audit sink for the free-model classification
        # events. ``None`` means the manager is silent — tests pass
        # ``None`` explicitly when they want the raw
        # :class:`ProviderDiscovery` rows without the audit
        # side-effect.
        self._audit = audit
        self._pi_runtime_manager = pi_runtime_manager
        # Injected by the runtime once the execution-evidence journal exists. Kept
        # optional so discovery still works before any real execution has happened.
        self._verified_execution_lookup: Callable[[str], datetime | None] | None = None
        self._state = self._rehydrate_state()

    def _rehydrate_state(self) -> _RuntimeState:
        """Build the initial runtime state from disk, if a valid snapshot
        is present.

        A valid snapshot produces a fully-populated :class:`ModelRegistry`
        (providers, accounts, models, execution targets, catalog
        snapshots) reconstructed by :func:`build_registry`. The Discovery
        module's subprocess is never invoked during this step.
        """

        load_outcome = load(self._runtime_state_root)
        if load_outcome.status is RegistryLoadStatus.MISSING:
            return _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.PENDING.value,
                    last_discovered_at=None,
                    provider_count=0,
                    configured_family_count=0,
                    catalog_discovered_provider_count=0,
                    credential_evidence_provider_count=0,
                    execution_target_count=0,
                    last_error_code=None,
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code=None,
                load_status=load_outcome.status,
            )
        if not load_outcome.loaded or load_outcome.persisted is None:
            return _RuntimeState(
                registry=_empty_registry(),
                last_result=None,
                last_status=ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.FAILED.value,
                    last_discovered_at=None,
                    provider_count=0,
                    configured_family_count=0,
                    catalog_discovered_provider_count=0,
                    credential_evidence_provider_count=0,
                    execution_target_count=0,
                    last_error_code=load_outcome.error_code,
                    catalog_snapshot_id=None,
                    source_method="persisted_registry_load",
                ),
                last_error_code=load_outcome.error_code,
                load_status=load_outcome.status,
            )
        persisted = load_outcome.persisted
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
                    configured_family_count=0,
                    catalog_discovered_provider_count=0,
                    credential_evidence_provider_count=0,
                    execution_target_count=0,
                    last_error_code="PERSISTED_SNAPSHOT_REJECTED",
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code="PERSISTED_SNAPSHOT_REJECTED",
                load_status=RegistryLoadStatus.SANITIZATION_REJECTED,
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
                    configured_family_count=0,
                    catalog_discovered_provider_count=0,
                    credential_evidence_provider_count=0,
                    execution_target_count=0,
                    last_error_code="PERSISTED_REGISTRY_BUILD_FAILED",
                    catalog_snapshot_id=None,
                    source_method=None,
                ),
                last_error_code="PERSISTED_REGISTRY_BUILD_FAILED",
                load_status=RegistryLoadStatus.SANITIZATION_REJECTED,
            )
        return _RuntimeState(
            registry=registry,
            last_result=result,
            last_status=_status_from_result(result),
            last_error_code=persisted.last_error_code,
            load_status=RegistryLoadStatus.LOADED,
        )

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def registry(self) -> ModelRegistry:
        """Return the current registry, including optional Pi runtime targets."""

        with self._lock:
            base = self._state.registry
            pi_manager = self._pi_runtime_manager
        if pi_manager is None:
            return base
        return merge_runtime_registries(base, pi_manager.registry())

    def pi_runtime_manager(self) -> PiProviderRegistryManager | None:
        with self._lock:
            return self._pi_runtime_manager

    def runtime_available(self, execution_target_id: str) -> bool:
        with self._lock:
            target = self.registry().execution_targets.get(execution_target_id)
            opencode_path = self._opencode_path
            pi_manager = self._pi_runtime_manager
        if target is None or not target.enabled:
            return False
        if target.runtime_id == "pi":
            return (
                pi_manager.runtime_available(execution_target_id)
                if pi_manager is not None
                else False
            )
        if target.runtime_id == "opencode":
            executable = str(opencode_path) if opencode_path is not None else "opencode"
            return shutil.which(executable) is not None
        return False

    def status(self) -> ProviderDiscoveryStatus:
        with self._lock:
            return self._state.last_status

    def last_discovery_result(self) -> DiscoveryResult | None:
        with self._lock:
            return self._state.last_result

    def connection_registry(self) -> ProviderConnectionRegistry:
        with self._lock:
            return self._connections

    def connected_provider_ids(self) -> frozenset[str]:
        with self._lock:
            return self._connections.connected_provider_ids()

    def routing_connected_provider_ids(self) -> frozenset[str]:
        """Return providers eligible for runtime routing.

        Explicit provider connections remain the authority for OpenCode and
        other connection-managed surfaces. Pi is local-runtime authenticated:
        a provider is considered routing-connected only when the Pi discovery
        snapshot reports READY auth and at least one discovered model.
        """

        ids = set(self.connected_provider_ids())
        with self._lock:
            pi_manager = self._pi_runtime_manager
        if pi_manager is None:
            return frozenset(ids)
        result = pi_manager.last_discovery_result()
        if result is None:
            return frozenset(ids)
        for provider in result.providers:
            if provider.auth_status.value == "READY" and provider.model_skus:
                ids.add(provider.provider_id)
        return frozenset(ids)

    def connect_provider(self, provider_id: str) -> ProviderConnection:
        with self._lock:
            if self._state.last_result is None:
                raise LookupError("provider_catalog_unavailable")
            next_registry = connect_from_discovery(
                self._connections,
                self._state.last_result,
                provider_id=provider_id,
            )
            save_connections(next_registry, runtime_state_root=self._runtime_state_root)
            self._connections = next_registry
            return self._connections.connections[provider_id]

    def disconnect_provider(self, provider_id: str) -> ProviderConnection:
        with self._lock:
            next_registry = disconnect_provider(
                self._connections,
                provider_id=provider_id,
            )
            save_connections(next_registry, runtime_state_root=self._runtime_state_root)
            self._connections = next_registry
            return self._connections.connections[provider_id]

    def set_verified_execution_lookup(
        self,
        lookup: Callable[[str], datetime | None] | None,
    ) -> None:
        """Attach the scoped prior-execution evidence source used for import candidates."""

        with self._lock:
            self._verified_execution_lookup = lookup

    def import_candidates(self) -> tuple[ProviderImportCandidate, ...]:
        with self._lock:
            return build_import_candidates(
                self._connections,
                self._state.last_result,
                verified_execution_lookup=self._verified_execution_lookup,
            )

    def import_connections(self, provider_ids: Sequence[str]) -> tuple[ProviderConnection, ...]:
        """Materialise owner-approved import candidates as real connection records."""

        with self._lock:
            if self._state.last_result is None:
                raise LookupError("provider_catalog_unavailable")
            next_registry = import_connections(
                self._connections,
                self._state.last_result,
                provider_ids=provider_ids,
                verified_execution_lookup=self._verified_execution_lookup,
            )
            save_connections(next_registry, runtime_state_root=self._runtime_state_root)
            self._connections = next_registry
            return tuple(
                self._connections.connections[provider_id] for provider_id in provider_ids
            )

    def connection_projection(self):
        with self._lock:
            return project_connections(
                self._connections,
                self._state.last_result,
                verified_execution_lookup=self._verified_execution_lookup,
            )

    def discovery_cycle_count(self) -> int:
        """Return the cumulative number of ``refresh`` invocations.

        Used by the single-startup-contract tests to verify that:

        - subsequent boot does not run a discovery cycle, and
        - an explicit refresh runs exactly one cycle.
        """

        with self._lock:
            return self._discovery_cycle_count

    def load_status(self) -> RegistryLoadStatus:
        with self._lock:
            return self._state.load_status

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
            load_status = self._state.load_status
        if already_loaded or load_status is not RegistryLoadStatus.MISSING:
            return False
        self.refresh()
        return True

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
            outcome: DiscoveryCycleOutcome = discover(
                opencode_path=self._opencode_path,
                audit=self._audit,
            )
            with self._lock:
                if outcome.result is not None and outcome.error_code is None:
                    registry = build_registry(outcome.result)
                    save(
                        outcome.result,
                        runtime_state_root=self._runtime_state_root,
                    )
                    if self._pi_runtime_manager is not None:
                        self._pi_runtime_manager.refresh()
                    status = _status_from_result(outcome.result)
                    self._state = _RuntimeState(
                        registry=registry,
                        last_result=outcome.result,
                        last_status=status,
                        last_error_code=None,
                        load_status=RegistryLoadStatus.LOADED,
                    )
                    return status
                # Failure: surface the error code; keep the previous
                # registry so the Dashboard still has something to
                # show, but flip the status to FAILED.
                failed_status = ProviderDiscoveryStatus(
                    discovery_state=DiscoveryState.FAILED.value,
                    last_discovered_at=_isoformat(self._state.last_status.last_discovered_at),
                    provider_count=self._state.last_status.provider_count,
                    configured_family_count=self._state.last_status.configured_family_count,
                    catalog_discovered_provider_count=(
                        self._state.last_status.catalog_discovered_provider_count
                    ),
                    credential_evidence_provider_count=(
                        self._state.last_status.credential_evidence_provider_count
                    ),
                    execution_target_count=self._state.last_status.execution_target_count,
                    last_error_code=outcome.error_code,
                    catalog_snapshot_id=self._state.last_status.catalog_snapshot_id,
                    source_method=self._state.last_status.source_method,
                )
                self._state.last_status = failed_status
                self._state.last_error_code = outcome.error_code
                if self._pi_runtime_manager is not None:
                    self._pi_runtime_manager.refresh()
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
    return ProviderDiscoveryStatus(
        discovery_state=result.state.value,
        last_discovered_at=result.discovered_at.isoformat(),
        provider_count=result.provider_count(),
        configured_family_count=result.configured_family_count,
        catalog_discovered_provider_count=result.catalog_discovered_provider_count,
        credential_evidence_provider_count=result.credential_evidence_provider_count,
        execution_target_count=result.execution_target_count(),
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
