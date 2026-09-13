"""Shared quota-pool helpers for runtime routing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityEvidence,
    QuotaAvailabilityState,
)

_BLOCKING_STATES = {
    QuotaAvailabilityState.EXHAUSTED_OBSERVED,
    QuotaAvailabilityState.COOLDOWN,
}
_SUCCESS_STATES = {
    QuotaAvailabilityState.AVAILABLE_OBSERVED,
    QuotaAvailabilityState.AVAILABLE_UNMETERED,
    QuotaAvailabilityState.RECOVERED_OBSERVED,
}


def quota_pool_id_for_target(
    registry: ModelRegistry,
    *,
    execution_target_id: str,
    now: datetime,
    known_at: datetime | None = None,
) -> str | None:
    target = registry.execution_targets.get(execution_target_id)
    if target is None:
        return None
    model = registry.models.get(target.model_sku_id)
    if model is None:
        return None
    try:
        binding = registry.active_quota_binding(
            model.id,
            effective_at=now,
            known_at=known_at or now,
            execution_target_id=target.id,
        )
    except (KeyError, LookupError, ValueError):
        return model.provider_id
    return binding.quota_pool_id


def pool_evidence(
    journal: Any,
    *,
    provider_id: str,
    quota_pool_id: str,
) -> tuple[QuotaAvailabilityEvidence, ...]:
    snapshot = journal.snapshot()
    if not isinstance(snapshot, dict):
        return ()
    rows: list[QuotaAvailabilityEvidence] = []
    for payload in snapshot.values():
        try:
            evidence = QuotaAvailabilityEvidence.model_validate(payload)
        except (TypeError, ValueError):
            continue
        if evidence.provider_id == provider_id and evidence.quota_pool_id == quota_pool_id:
            rows.append(evidence)
    rows.sort(key=lambda item: (item.observed_at, item.execution_target_id))
    return tuple(rows)


def active_shared_pool_blocker(
    journal: Any,
    *,
    provider_id: str,
    quota_pool_id: str,
    now: datetime,
) -> QuotaAvailabilityEvidence | None:
    rows = pool_evidence(
        journal,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
    )
    blockers = [item for item in rows if item.state_at(now=now) in _BLOCKING_STATES]
    if not blockers:
        return None
    latest_blocker = max(
        blockers,
        key=lambda item: (item.observed_at, item.execution_target_id),
    )
    successes = [item for item in rows if item.state_at(now=now) in _SUCCESS_STATES]
    if successes:
        latest_success = max(
            successes,
            key=lambda item: (item.observed_at, item.execution_target_id),
        )
        if latest_success.observed_at > latest_blocker.observed_at:
            return None
    return latest_blocker


__all__ = [
    "active_shared_pool_blocker",
    "pool_evidence",
    "quota_pool_id_for_target",
]
