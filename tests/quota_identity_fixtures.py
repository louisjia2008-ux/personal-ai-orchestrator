"""Typed offline quota fixtures shared by host recommendation tests."""

from datetime import datetime, timedelta

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ModelRegistry,
    Plan,
    PlanKind,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)


def snapshot(pool: str, now: datetime, remaining: float = 0.8) -> QuotaSnapshot:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=now,
        confidence=EvidenceConfidence.EXACT,
    )
    return QuotaSnapshot(
        quota_pool_id=pool,
        observed_at=now,
        source=source,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        windows=(
            QuotaWindowSnapshot(
                window_id="5h",
                window_kind=QuotaWindowKind.FIVE_HOUR,
                duration_seconds=18000,
                window_started_at=now - timedelta(hours=1),
                reset_at=now + timedelta(hours=4),
                remaining_fraction=remaining,
                used_fraction=1 - remaining,
                source=source,
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.EXACT,
            ),
        ),
    )


def bind(registry: ModelRegistry, model_id: str, pool_id: str, now: datetime) -> ModelRegistry:
    model = registry.models[model_id]
    account = next(a for a in registry.accounts.values() if a.provider_id == model.provider_id)
    plan = Plan(
        id=f"plan-{pool_id}", account_id=account.id, name="fixture plan", kind=PlanKind.SUBSCRIPTION
    )
    quota = snapshot(pool_id, now)
    fact = QuotaBinding(
        id=f"binding-{model_id}",
        model_sku_id=model_id,
        quota_pool_id=pool_id,
        effective_from=now,
        recorded_at=now,
        confidence=EvidenceConfidence.EXACT,
        source=quota.source,
    )
    return ModelRegistry(
        **{
            **registry.model_dump(),
            "plans": {**registry.plans, plan.id: plan},
            "quota_pools": {
                **registry.quota_pools,
                pool_id: QuotaPool(
                    id=pool_id,
                    plan_id=plan.id,
                    name="fixture pool",
                    snapshot=quota,
                ),
            },
            "quota_bindings": (*registry.quota_bindings, fact),
        }
    )
