"""Shared quota-pool helpers for runtime routing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityEvidence,
    QuotaAvailabilityState,
)


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


__all__ = ["pool_evidence", "quota_pool_id_for_target"]
