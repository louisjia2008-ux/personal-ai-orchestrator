"""Shared quota-pool helpers for runtime routing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityEvidence,
    QuotaAvailabilityState,
)
from personal_ai_orchestrator.quota_refresh import quota_pool_id_for

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
        # Explicit facts own their temporal/ambiguity semantics. Never mask an
        # expired, future, or ambiguous binding with discovery metadata.
        if any(
            fact.model_sku_id == model.id
            and fact.execution_target_id in (None, target.id)
            for fact in registry.quota_bindings
        ):
            return None
        return quota_pool_id_for(model.provider_id)
    if binding.confidence is EvidenceConfidence.UNKNOWN:
        return None
    return binding.quota_pool_id


def pool_evidence(
    journal: Any,
    *,
    provider_id: str,
    quota_pool_id: str,
) -> tuple[QuotaAvailabilityEvidence, ...]:
    try:
        snapshot = journal.snapshot()
    except (OSError, TypeError, ValueError):
        return ()
    if not isinstance(snapshot, dict):
        return ()
    rows: list[QuotaAvailabilityEvidence] = []
    for payload in snapshot.values():
        try:
            evidence = QuotaAvailabilityEvidence.model_validate(payload)
        except (TypeError, ValueError):
            continue
        evidence_pool = evidence.quota_pool_id
        # Earlier dispatch journals explicitly used the provider ID as pool.
        # Resolve that known legacy format without rewriting immutable evidence.
        if evidence_pool == evidence.provider_id:
            evidence_pool = quota_pool_id_for(evidence.provider_id) or evidence_pool
        if evidence_pool == quota_pool_id:
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
