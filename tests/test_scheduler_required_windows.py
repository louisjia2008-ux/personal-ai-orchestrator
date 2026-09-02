from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.scheduler import TaskProfile, route_task

NOW = datetime(2026, 8, 29, 7, tzinfo=UTC)


def test_missing_configured_weekly_window_fails_admission() -> None:
    src = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://quota",
        confidence=EvidenceConfidence.EXACT,
    )
    snapshot = QuotaSnapshot(
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=src,
        windows=(
            QuotaWindowSnapshot(
                window_id="5h",
                window_kind=QuotaWindowKind.FIVE_HOUR,
                duration_seconds=5 * 60 * 60,
                remaining_fraction=0.8,
                window_started_at=NOW - timedelta(hours=3),
                reset_at=NOW + timedelta(hours=2),
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.EXACT,
                source=src,
            ),
        ),
    )
    registry = ModelRegistry(
        providers={"p": Provider(id="p", display_name="Provider")},
        accounts={"a": Account(id="a", provider_id="p", label="Account")},
        plans={
            "plan": Plan(
                id="plan",
                account_id="a",
                name="Subscription",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="Shared plan quota",
                snapshot=snapshot,
                required_window_kinds=(
                    QuotaWindowKind.FIVE_HOUR,
                    QuotaWindowKind.WEEKLY,
                ),
            )
        },
        models={
            "m": ModelSKU(
                id="m",
                provider_id="p",
                display_name="Model",
                capabilities=CapabilityProfile(scores={"debugging": 0.9}),
            )
        },
        execution_targets={
            "target": ExecutionTarget(
                id="target",
                model_sku_id="m",
                account_id="a",
                runtime_id="opencode",
                execution_verified=True,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding",
                model_sku_id="m",
                execution_target_id="target",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=src,
            ),
        ),
        pool_memberships=(
            PoolMembership(pool=PoolKind.WORKER, model_sku_id="m", priority=1),
        ),
    )

    decision = route_task(
        registry,
        task=TaskProfile(
            task_id="task",
            pool=PoolKind.WORKER,
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        ),
        now=NOW,
        known_at=NOW,
        runtime_availability={"target": True},
    )

    assert decision.selected_execution_target_id is None
    evaluation = decision.evaluations[0]
    assert evaluation.admitted is False
    assert evaluation.effective_pace is None
    assert evaluation.minimum_remaining_fraction is None
    assert any("WEEKLY" in reason for reason in evaluation.reasons)
