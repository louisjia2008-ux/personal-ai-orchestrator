"""M1 WP5a-1 commit 1 — host application/service layer for dispatch recommendations.

This module wraps the deterministic scoring core
(``dispatch_recommender.recommend_owner_dispatch``) with the
host-specific data gathering needed to build the candidate inputs
the recommender consumes.

Scope split (kept strict for WP5a-1 commit 1):
- ``dispatch_recommender.py`` owns the **scoring algorithm** —
  candidate admission, ranking, score decomposition. Pure
  functions, deterministic, no I/O.
- ``dispatch_recommendation_service.py`` (this module) owns the
  **host application/service layer** — gathering
  ``DispatchCandidateInput`` rows from the model registry, the
  live quota refresh service, the execution evidence journal, and
  the host-owned tier table. This is the layer that knows about
  ``ControlPlaneService`` runtime state.

PI-4B additionally projects a commercial quota-pool blocker across sibling
worker runtimes before recommendation scoring. The authoritative PI-4A runtime
gate still revalidates the frozen target immediately before execution.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from personal_ai_orchestrator.dispatch_recommender import (
    CandidateWindowInput,
    DispatchCandidateInput,
    DispatchRecommendation,
    recommend_owner_dispatch,
)
from personal_ai_orchestrator.execution_controller import (
    execution_target_has_launch_verification,
)
from personal_ai_orchestrator.model_tiers import ModelTier
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
from personal_ai_orchestrator.runtime_quota_routing import (
    active_shared_pool_blocker,
    quota_pool_id_for_target,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskRecord
from personal_ai_orchestrator.scheduler import RoutingObjective, RoutingPolicy


class DispatchRecommendationService:
    """Host application/service layer for owner-dispatch recommendations."""

    def __init__(
        self,
        store: SafetyKernelStore,
        *,
        registry_provider: Callable[[], object],
        quota_refresh_service: object | None,
        execution_evidence_journal: object | None,
        quota_availability_journal: object | None,
        tier_table: object | None,
        runtime_availability: dict[str, bool] | None,
        runtime_availability_fallback: Callable[[str], bool],
    ) -> None:
        self._store = store
        self._registry_provider = registry_provider
        self._quota_refresh_service = quota_refresh_service
        self._execution_evidence_journal = execution_evidence_journal
        self._quota_availability_journal = quota_availability_journal
        self._tier_table = tier_table
        self._runtime_availability = runtime_availability
        self._runtime_availability_fallback = runtime_availability_fallback

    def collect_candidates(
        self,
        *,
        now: datetime | None = None,
    ) -> list[DispatchCandidateInput]:
        """Gather one host-evidence row per execution target."""

        if now is None:
            now = datetime.now(UTC)

        registry = self._registry_provider()
        provider_by_target: dict[str, str] = {
            target_id: registry.models[target.model_sku_id].provider_id
            for target_id, target in registry.execution_targets.items()
            if target.model_sku_id in registry.models
        }

        connected = (
            {item.provider_id for item in self._quota_refresh_service.observations()}
            if self._quota_refresh_service is not None else set()
        )
        pool_by_target = {
            target_id: quota_pool_id_for_target(
                registry, execution_target_id=target_id, now=now,
            ) if provider_by_target.get(target_id) in connected else None
            for target_id in registry.execution_targets
        }
        remaining_by_pool: dict[str, list[float]] = {}
        windows_by_pool: dict[str, list[CandidateWindowInput]] = {}
        if self._quota_refresh_service is not None:
            for pool_id in sorted({pool for pool in pool_by_target.values() if pool is not None}):
                snapshot = self._quota_refresh_service.snapshot_for_pool(pool_id)
                if snapshot is None or snapshot.quota_pool_id != pool_id:
                    continue
                # Keep identity resolution separate from observation validity.
                if snapshot.is_stale(
                    as_of=now, max_age_seconds=RoutingPolicy().max_quota_age_seconds
                ) or snapshot.confidence.value == "UNKNOWN":
                    continue
                if snapshot.state.value in {"UNKNOWN", "EXHAUSTED"}:
                    continue
                if snapshot.minimum_remaining_fraction(at=now) is None:
                    continue
                for window in snapshot.active_windows(at=now):
                    if window.reset_at is None:
                        continue
                    windows_by_pool.setdefault(pool_id, []).append(
                        CandidateWindowInput(
                            kind=window.window_kind,
                            window_started_at=window.window_started_at,
                            reset_at=window.reset_at,
                            used_fraction=window.used_fraction,
                        )
                    )
                    fraction = window.remaining_fraction
                    if fraction is not None:
                        remaining_by_pool.setdefault(pool_id, []).append(fraction)

        candidates: list[DispatchCandidateInput] = []
        for target_id, target in sorted(registry.execution_targets.items()):
            provider_id = provider_by_target.get(target_id, "")
            quota_pool_id = pool_by_target[target_id]
            remaining = tuple(remaining_by_pool.get(quota_pool_id, ()))

            if self._runtime_availability is not None and target_id in self._runtime_availability:
                runtime_available = self._runtime_availability.get(target_id)
            else:
                runtime_available = self._runtime_availability_fallback(target_id)

            verified = execution_target_has_launch_verification(
                registry,
                execution_target_id=target_id,
                execution_evidence_journal=self._execution_evidence_journal,
                now=now,
            )
            verified_stale = False
            evidence_observed_at: datetime | None = None
            if self._execution_evidence_journal is not None:
                try:
                    verified_evidence, stale_since = (
                        self._execution_evidence_journal.latest_verified_for_target(target_id)
                    )
                except Exception:
                    verified_evidence, stale_since = None, None
                if verified_evidence is not None:
                    evidence_observed_at = verified_evidence.observed_at
                    # Keep historical VERIFIED evidence visible for diagnostics,
                    # but mark demote-fallback and age expiry stale. The
                    # launch-safe verified bit above remains fail-closed.
                    verified_stale = stale_since is not None or not verified

            availability_state = QuotaAvailabilityState.UNKNOWN
            if self._quota_availability_journal is not None:
                evidence = self._quota_availability_journal.load(target_id)
                if evidence is not None:
                    availability_state = evidence.state_at(now=now)
                if quota_pool_id is not None and provider_id:
                    shared = active_shared_pool_blocker(
                        self._quota_availability_journal,
                        provider_id=provider_id,
                        quota_pool_id=quota_pool_id,
                        now=now,
                    )
                    if shared is not None:
                        availability_state = shared.state_at(now=now)

            tier_value = None
            tier_match_reason = None
            if self._tier_table is not None:
                entry, tier_match_reason = self._tier_table.lookup(target_id)
                tier_value = entry.tier

            candidates.append(
                DispatchCandidateInput(
                    execution_target_id=target_id,
                    model_sku_id=target.model_sku_id,
                    runtime_available=bool(runtime_available),
                    verified=bool(verified),
                    verified_stale=verified_stale,
                    remaining_fractions=remaining,
                    windows=tuple(windows_by_pool.get(quota_pool_id, ())),
                    evidence_observed_at=evidence_observed_at,
                    availability_state=availability_state,
                    tier=tier_value,
                    tier_match_reason=tier_match_reason,
                )
            )
        return candidates

    def recommend_for_task(
        self,
        task: TaskRecord,
        *,
        policy: RoutingObjective,
        now: datetime | None = None,
    ) -> tuple[DispatchRecommendation, list[DispatchCandidateInput], str | None]:
        if now is None:
            now = datetime.now(UTC)
        candidates = self.collect_candidates(now=now)

        invalid_min_tier: str | None = None
        try:
            min_tier_value = ModelTier(task.min_tier)
        except ValueError:
            invalid_min_tier = task.min_tier
            min_tier_value = ModelTier.T1
            self._store.record_system_event(
                "TASK_MIN_TIER_INVALID",
                {"task_id": task.task_id, "raw": task.min_tier},
            )

        recommendation = recommend_owner_dispatch(
            candidates,
            policy=policy,
            now=now,
            min_tier=min_tier_value,
            invalid_min_tier=invalid_min_tier,
        )
        return recommendation, candidates, invalid_min_tier


__all__ = ["DispatchRecommendationService"]
