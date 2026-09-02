"""Host-owned, read-only quota refresh for *connected* providers.

Provider discovery and quota observation are separate operations:

``POST /v1/providers/refresh``
    Re-runs OpenCode catalog/credential discovery. It answers "which provider
    surfaces exist and does a credential appear to be configured", and it
    never contacts a provider's quota API.

``POST /v1/quota/refresh``
    Contacts each connected provider's documented **read-only** quota endpoint
    through the existing P3 collectors and persists whatever they truthfully
    report.

Safety contract (P4.2.6.4 §10)
------------------------------
Quota collection must never spend quota to measure quota. Every collector
reachable from here performs a single authenticated HTTP ``GET`` against a
documented quota/usage endpoint; no model generation, completion, or "probe"
request is ever issued. :func:`assert_read_only_collector` enforces the rule
structurally so a future collector cannot quietly become billable.

Truth contract (P4.2.6.4 §13)
-----------------------------
A provider that is connected but has no readable quota source stays visible
with ``quota_state=UNKNOWN``. UNKNOWN never fabricates a remaining fraction:
this module only persists fractions the provider itself reported.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from personal_ai_orchestrator.model_registry import EvidenceConfidence, QuotaSnapshot
from personal_ai_orchestrator.quota_cache import (
    QuotaSnapshotCache,
    QuotaSnapshotJournal,
    refresh_with_last_known_good,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionStatus,
    QuotaCollector,
)
from personal_ai_orchestrator.quota_collectors.minimax import MiniMaxQuotaCollector
from personal_ai_orchestrator.quota_collectors.zai import ZAIQuotaCollector

#: Sub-directory of ``runtime_state_root`` owning the last-known-good cache and
#: the append-only replay journal.
QUOTA_STATE_DIRNAME = "quota"


class QuotaObservationState(StrEnum):
    """Whether the host currently holds reliable quota evidence for a provider."""

    OBSERVED = "OBSERVED"
    UNKNOWN = "UNKNOWN"


class QuotaPageState(StrEnum):
    """The three distinct owner-facing states of the Quota page (§8)."""

    NO_CONNECTED_PROVIDER = "NO_CONNECTED_PROVIDER"
    CONNECTED_BUT_QUOTA_UNKNOWN = "CONNECTED_BUT_QUOTA_UNKNOWN"
    CONNECTED_WITH_QUOTA_OBSERVATIONS = "CONNECTED_WITH_QUOTA_OBSERVATIONS"


class QuotaRefreshReason(StrEnum):
    """Sanitized reason a refresh produced no usable observation.

    These are stable machine codes surfaced under Advanced Details; the
    Dashboard maps them to localized sentences. They never carry a
    credential, URL, or provider response body.
    """

    NO_READONLY_QUOTA_SOURCE = "NO_READONLY_QUOTA_SOURCE"
    CREDENTIAL_NOT_AVAILABLE = "CREDENTIAL_NOT_AVAILABLE"
    PROVIDER_NOT_CONNECTED = "PROVIDER_NOT_CONNECTED"
    COLLECTOR_UNAVAILABLE = "COLLECTOR_UNAVAILABLE"


@dataclass(frozen=True)
class QuotaSourceSpec:
    """A documented read-only quota source for one connected provider surface.

    ``quota_pool_id`` is stable per provider surface so the last-known-good
    cache file, the replay journal, and the owner-facing card all agree.
    """

    provider_id: str
    quota_pool_id: str
    plan_id: str
    #: Environment variable whose *value* the collector needs. Presence alone is
    #: what discovery checks; refresh actually reads it, and never persists it.
    credential_env_var: str
    factory: Callable[[str, str], QuotaCollector]


def _zai_collector(token: str, quota_pool_id: str) -> QuotaCollector:
    return ZAIQuotaCollector(authorization_token=token, quota_pool_id=quota_pool_id)


def _minimax_cn_collector(token: str, quota_pool_id: str) -> QuotaCollector:
    return MiniMaxQuotaCollector(
        bearer_token=token, region="cn", quota_pool_id=quota_pool_id
    )


def _minimax_global_collector(token: str, quota_pool_id: str) -> QuotaCollector:
    return MiniMaxQuotaCollector(
        bearer_token=token, region="global", quota_pool_id=quota_pool_id
    )


#: Read-only quota sources keyed by the discovery provider family id. A family
#: absent from this table has no documented read-only quota endpoint we trust,
#: and stays truthfully UNKNOWN rather than being probed with a billable call.
QUOTA_SOURCES: tuple[QuotaSourceSpec, ...] = (
    QuotaSourceSpec(
        provider_id="zai-coding-plan",
        quota_pool_id="zai-coding-plan",
        plan_id="coding-plan",
        credential_env_var="ZAI_API_KEY",
        factory=_zai_collector,
    ),
    QuotaSourceSpec(
        provider_id="minimax-cn",
        quota_pool_id="minimax-token-plan-cn",
        plan_id="token-plan",
        credential_env_var="MINIMAX_API_KEY",
        factory=_minimax_cn_collector,
    ),
    QuotaSourceSpec(
        provider_id="minimax-cn-coding-plan",
        quota_pool_id="minimax-coding-plan-cn",
        plan_id="coding-plan",
        credential_env_var="MINIMAX_API_KEY",
        factory=_minimax_cn_collector,
    ),
    QuotaSourceSpec(
        provider_id="minimax",
        quota_pool_id="minimax-token-plan-global",
        plan_id="token-plan",
        credential_env_var="MINIMAX_API_KEY",
        factory=_minimax_global_collector,
    ),
    QuotaSourceSpec(
        provider_id="minimax-coding-plan",
        quota_pool_id="minimax-coding-plan-global",
        plan_id="coding-plan",
        credential_env_var="MINIMAX_API_KEY",
        factory=_minimax_global_collector,
    ),
)

QUOTA_SOURCE_BY_PROVIDER: Mapping[str, QuotaSourceSpec] = {
    spec.provider_id: spec for spec in QUOTA_SOURCES
}


def has_readonly_quota_source(provider_id: str) -> bool:
    """Whether a documented read-only quota endpoint exists for this surface.

    Independent of whether a credential happens to be configured right now:
    "we cannot read this provider's quota at all" and "we could, but you are
    not signed in" are different owner-facing truths.
    """

    return provider_id in QUOTA_SOURCE_BY_PROVIDER


def quota_pool_id_for(provider_id: str) -> str | None:
    spec = QUOTA_SOURCE_BY_PROVIDER.get(provider_id)
    return spec.quota_pool_id if spec is not None else None


def assert_read_only_collector(collector: object) -> None:
    """Fail closed if a collector exposes anything that could spend quota.

    Collectors are read-only by construction: they own a JSON ``GET``
    transport and nothing else. This guard makes that structural rather than
    conventional, so a collector that grows a ``complete``/``generate``/
    ``chat`` surface is rejected before it can be scheduled.
    """

    forbidden = ("complete", "completion", "generate", "chat", "invoke", "prompt")
    for name in forbidden:
        if hasattr(collector, name):
            raise ValueError(
                f"quota collector {type(collector).__name__} exposes billable "
                f"surface {name!r}; quota refresh must stay read-only"
            )


@dataclass(frozen=True)
class QuotaProviderObservation:
    """Latest sanitized quota truth for exactly one connected provider."""

    provider_id: str
    quota_pool_id: str | None
    plan_id: str | None
    observation_state: QuotaObservationState
    confidence: str
    snapshot: QuotaSnapshot | None
    collector_available: bool
    last_refresh_status: str | None
    last_refresh_at: str | None
    failure_reason: str | None

    @property
    def observed_at(self) -> str | None:
        if self.snapshot is None:
            return None
        moment = self.snapshot.observed_at or self.snapshot.recorded_at
        return moment.isoformat() if moment is not None else None

    @property
    def measurement_source(self) -> str | None:
        if self.snapshot is None:
            return None
        return self.snapshot.source.source_type.value


class QuotaRefreshService:
    """Owns read-only quota collection for connected providers.

    The service is deliberately *stateless between calls* apart from the
    on-disk cache: :meth:`observations` re-reads the last-known-good cache so
    a Dashboard reload after a daemon restart still shows the last real
    observation instead of silently regressing to UNKNOWN.
    """

    def __init__(
        self,
        *,
        runtime_state_root: Path,
        connected_provider_ids: Callable[[], Iterable[str]],
        environ: Mapping[str, str] | None = None,
        collectors: Mapping[str, QuotaCollector] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        root = runtime_state_root / QUOTA_STATE_DIRNAME
        self._cache = QuotaSnapshotCache(root)
        self._journal = QuotaSnapshotJournal(root)
        self._connected_provider_ids = connected_provider_ids
        self._environ = environ if environ is not None else os.environ
        self._injected_collectors = dict(collectors) if collectors else None
        self._now = now
        self._lock = threading.RLock()
        #: provider_id -> (status, iso timestamp, sanitized reason)
        self._last_attempt: dict[str, tuple[str, str, str | None]] = {}
        #: provider_id -> the pool id a collector actually normalized to. The
        #: table id is authoritative for addressing across restarts; this only
        #: keeps a within-process collector/table disagreement from silently
        #: reading back as UNKNOWN.
        self._observed_pool_id: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Collector resolution
    # ------------------------------------------------------------------

    def _collector_for(self, provider_id: str) -> tuple[QuotaCollector | None, str | None]:
        """Return ``(collector, sanitized_failure_reason)``."""

        if self._injected_collectors is not None:
            collector = self._injected_collectors.get(provider_id)
            if collector is None:
                reason = (
                    QuotaRefreshReason.NO_READONLY_QUOTA_SOURCE.value
                    if not has_readonly_quota_source(provider_id)
                    else QuotaRefreshReason.COLLECTOR_UNAVAILABLE.value
                )
                return None, reason
            assert_read_only_collector(collector)
            return collector, None

        spec = QUOTA_SOURCE_BY_PROVIDER.get(provider_id)
        if spec is None:
            return None, QuotaRefreshReason.NO_READONLY_QUOTA_SOURCE.value
        token = self._environ.get(spec.credential_env_var)
        if not token:
            return None, QuotaRefreshReason.CREDENTIAL_NOT_AVAILABLE.value
        collector = spec.factory(token, spec.quota_pool_id)
        assert_read_only_collector(collector)
        return collector, None

    def _pool_id_for(self, provider_id: str) -> str | None:
        if self._injected_collectors is not None and provider_id in self._injected_collectors:
            # Injected collectors still normalize into a snapshot that names its
            # own pool; fall back to the table when one is also configured.
            return quota_pool_id_for(provider_id) or provider_id
        return quota_pool_id_for(provider_id)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def observations(self) -> tuple[QuotaProviderObservation, ...]:
        """Latest quota truth for every currently connected provider.

        Connected providers with no evidence are still returned — losing them
        here is exactly the defect this module exists to fix.
        """

        return tuple(
            self._observation_for(provider_id)
            for provider_id in sorted(self._connected_provider_ids())
        )

    def _observation_for(self, provider_id: str) -> QuotaProviderObservation:
        spec = QUOTA_SOURCE_BY_PROVIDER.get(provider_id)
        pool_id = self._pool_id_for(provider_id)
        snapshot = self._cache.load(pool_id) if pool_id else None
        if snapshot is None:
            with self._lock:
                fallback = self._observed_pool_id.get(provider_id)
            if fallback is not None and fallback != pool_id:
                snapshot = self._cache.load(fallback)
                if snapshot is not None:
                    pool_id = fallback
        with self._lock:
            attempt = self._last_attempt.get(provider_id)
        status, attempted_at, attempt_reason = attempt if attempt else (None, None, None)

        collector, availability_reason = self._collector_for(provider_id)
        collector_available = collector is not None

        if snapshot is not None and snapshot.confidence is not EvidenceConfidence.UNKNOWN:
            return QuotaProviderObservation(
                provider_id=provider_id,
                quota_pool_id=pool_id,
                plan_id=spec.plan_id if spec else snapshot.plan_id,
                observation_state=QuotaObservationState.OBSERVED,
                confidence=snapshot.confidence.value,
                snapshot=snapshot,
                collector_available=collector_available,
                last_refresh_status=status,
                last_refresh_at=attempted_at,
                failure_reason=attempt_reason,
            )

        return QuotaProviderObservation(
            provider_id=provider_id,
            quota_pool_id=pool_id,
            plan_id=spec.plan_id if spec else None,
            observation_state=QuotaObservationState.UNKNOWN,
            confidence=EvidenceConfidence.UNKNOWN.value,
            snapshot=None,
            collector_available=collector_available,
            last_refresh_status=status,
            last_refresh_at=attempted_at,
            failure_reason=attempt_reason or availability_reason,
        )

    def page_state(
        self,
        observations: tuple[QuotaProviderObservation, ...] | None = None,
    ) -> QuotaPageState:
        items = self.observations() if observations is None else observations
        if not items:
            return QuotaPageState.NO_CONNECTED_PROVIDER
        if any(item.observation_state is QuotaObservationState.OBSERVED for item in items):
            return QuotaPageState.CONNECTED_WITH_QUOTA_OBSERVATIONS
        return QuotaPageState.CONNECTED_BUT_QUOTA_UNKNOWN

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def refresh(self, provider_id: str | None = None) -> tuple[QuotaProviderObservation, ...]:
        """Collect quota for one or all connected providers.

        Returns the refreshed observations. A provider whose collection fails
        keeps its previous last-known-good snapshot and stays visible with a
        sanitized failure reason; failure never removes a provider from the
        Quota page.
        """

        connected = sorted(self._connected_provider_ids())
        if provider_id is not None:
            if provider_id not in connected:
                self._record_attempt(
                    provider_id,
                    status=QuotaCollectionStatus.UNKNOWN.value,
                    reason=QuotaRefreshReason.PROVIDER_NOT_CONNECTED.value,
                )
                return ()
            connected = [provider_id]

        for target in connected:
            self._refresh_one(target)
        return tuple(self._observation_for(target) for target in connected)

    def _refresh_one(self, provider_id: str) -> None:
        collector, reason = self._collector_for(provider_id)
        if collector is None:
            self._record_attempt(
                provider_id,
                status=QuotaCollectionStatus.UNKNOWN.value,
                reason=reason,
            )
            return

        pool_id = self._pool_id_for(provider_id) or provider_id
        try:
            result = refresh_with_last_known_good(
                collector,
                self._cache,
                quota_pool_id=pool_id,
                journal=self._journal,
            )
        except Exception:
            # Collector faults must never take the control plane down; the
            # provider stays visible with a sanitized reason instead.
            self._record_attempt(
                provider_id,
                status=QuotaCollectionStatus.PROVIDER_ERROR.value,
                reason="COLLECTOR_FAULT",
            )
            return

        self._record_attempt(
            provider_id,
            status=result.status.value,
            reason=result.error_category,
        )
        if result.snapshot is not None and result.snapshot.quota_pool_id:
            with self._lock:
                self._observed_pool_id[provider_id] = result.snapshot.quota_pool_id

    def _record_attempt(self, provider_id: str, *, status: str, reason: str | None) -> None:
        with self._lock:
            self._last_attempt[provider_id] = (status, self._now().isoformat(), reason)


__all__ = [
    "QUOTA_SOURCES",
    "QUOTA_SOURCE_BY_PROVIDER",
    "QUOTA_STATE_DIRNAME",
    "QuotaObservationState",
    "QuotaPageState",
    "QuotaProviderObservation",
    "QuotaRefreshReason",
    "QuotaRefreshService",
    "QuotaSourceSpec",
    "assert_read_only_collector",
    "has_readonly_quota_source",
    "quota_pool_id_for",
]
