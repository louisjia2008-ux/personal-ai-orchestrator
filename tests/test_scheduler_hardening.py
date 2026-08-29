from datetime import UTC, datetime, timedelta

import pytest

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
from personal_ai_orchestrator.quota_cache import QuotaSnapshotJournal
from personal_ai_orchestrator.quota_observability import ScarcityClass, build_pace_trace
from personal_ai_orchestrator.scheduler import TaskProfile, route_task

NOW = datetime(2026, 8, 29, 6, tzinfo=UTC)


def source(confidence: EvidenceConfidence = EvidenceConfidence.EXACT) -> EvidenceSource:
    return EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://quota",
        confidence=confidence,
    )


def quota_window(
    window_id: str,
    kind: QuotaWindowKind,
    *,
    remaining: float | None,
    reset_delta: timedelta | None,
    duration: timedelta,
    confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
) -> QuotaWindowSnapshot:
    reset = None if reset_delta is None else NOW + reset_delta
    started = None if reset is None else reset - duration
    return QuotaWindowSnapshot(
        window_id=window_id,
        window_kind=kind,
        duration_seconds=duration.total_seconds(),
        remaining_fraction=remaining,
        window_started_at=started,
        reset_at=reset,
        state=(
            QuotaState.UNKNOWN
            if remaining is None
            else QuotaState.EXHAUSTED
            if remaining == 0
            else QuotaState.AVAILABLE
        ),
        confidence=confidence,
        source=source(confidence),
    )


def test_unknown_simultaneous_window_blocks_effective_pace() -> None:
    five_hour = quota_window(
        "5h",
        QuotaWindowKind.FIVE_HOUR,
        remaining=0.8,
        reset_delta=timedelta(hours=2),
        duration=timedelta(hours=5),
    )
    weekly = quota_window(
        "week",
        QuotaWindowKind.WEEKLY,
        remaining=None,
        reset_delta=timedelta(days=3),
        duration=timedelta(days=7),
        confidence=EvidenceConfidence.UNKNOWN,
    )
    snapshot = QuotaSnapshot(
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.UNKNOWN,
        source=source(EvidenceConfidence.UNKNOWN),
        windows=(five_hour, weekly),
    )

    assert snapshot.known_min_pace(at=NOW) == pytest.approx(2.0)
    assert snapshot.effective_pace(at=NOW) is None
    trace = build_pace_trace(snapshot, at=NOW)
    assert trace.known_min_pace == pytest.approx(2.0)
    assert trace.effective_pace is None
    assert trace.all_binding_windows_known is False
    assert trace.scarcity_class is ScarcityClass.UNKNOWN


def test_quota_snapshot_has_stable_replay_identity_and_journal(tmp_path) -> None:
    snapshot = QuotaSnapshot(
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source(),
        windows=(
            quota_window(
                "5h",
                QuotaWindowKind.FIVE_HOUR,
                remaining=0.5,
                reset_delta=timedelta(hours=2),
                duration=timedelta(hours=5),
            ),
        ),
    )
    journal = QuotaSnapshotJournal(tmp_path)

    first = journal.append(snapshot)
    second = journal.append(snapshot)

    assert first == second
    assert journal.load(snapshot.id) == snapshot
    assert QuotaSnapshot.model_validate_json(snapshot.model_dump_json()).id == snapshot.id


def test_superseding_fact_wins_even_with_earlier_effective_from() -> None:
    registry = _registry_for_supersession()

    binding = registry.active_quota_binding(
        "model",
        effective_at=NOW,
        known_at=NOW,
    )

    assert binding.id == "corrected"
    assert binding.quota_pool_id == "new-pool"


def test_overlapping_unsuperseded_bindings_fail_closed() -> None:
    registry = _registry_for_supersession(include_ambiguous=True)

    with pytest.raises(LookupError, match="ambiguous active quota binding"):
        registry.active_quota_binding("model", effective_at=NOW, known_at=NOW)


def _registry_for_supersession(*, include_ambiguous: bool = False) -> ModelRegistry:
    providers = {"p": Provider(id="p", display_name="Provider")}
    accounts = {"a": Account(id="a", provider_id="p", label="account")}
    plans = {
        "old": Plan(id="old", account_id="a", name="old", kind=PlanKind.SUBSCRIPTION),
        "new": Plan(id="new", account_id="a", name="new", kind=PlanKind.SUBSCRIPTION),
    }
    empty = QuotaSnapshot(
        observed_at=NOW,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=source(EvidenceConfidence.UNKNOWN),
    )
    pools = {
        "old-pool": QuotaPool(id="old-pool", plan_id="old", name="old", snapshot=empty),
        "new-pool": QuotaPool(id="new-pool", plan_id="new", name="new", snapshot=empty),
    }
    models = {"model": ModelSKU(id="model", provider_id="p", display_name="Model")}
    bindings = [
        QuotaBinding(
            id="original",
            model_sku_id="model",
            quota_pool_id="old-pool",
            effective_from=NOW - timedelta(days=2),
            recorded_at=NOW - timedelta(days=2),
            confidence=EvidenceConfidence.EXACT,
            source=source(),
        ),
        QuotaBinding(
            id="corrected",
            model_sku_id="model",
            quota_pool_id="new-pool",
            effective_from=NOW - timedelta(days=3),
            recorded_at=NOW - timedelta(hours=1),
            supersedes_binding_id="original",
            confidence=EvidenceConfidence.EXACT,
            source=source(),
        ),
    ]
    if include_ambiguous:
        bindings.append(
            QuotaBinding(
                id="unrelated",
                model_sku_id="model",
                quota_pool_id="old-pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(hours=2),
                confidence=EvidenceConfidence.EXACT,
                source=source(),
            )
        )
    return ModelRegistry(
        providers=providers,
        accounts=accounts,
        plans=plans,
        quota_pools=pools,
        models=models,
        quota_bindings=tuple(bindings),
    )


def test_harvest_signal_does_not_admit_task_larger_than_headroom() -> None:
    registry = _scheduler_registry(minimax_remaining=0.01, minimax_reset_minutes=1)
    decision = route_task(
        registry,
        task=TaskProfile(
            task_id="t",
            pool=PoolKind.WORKER,
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        ),
        now=NOW,
        known_at=NOW,
        runtime_availability={"minimax-sub": True, "glm-sub": False},
    )

    minimax = next(item for item in decision.evaluations if item.execution_target_id == "minimax-sub")
    assert minimax.scarcity_class is ScarcityClass.HARVEST
    assert minimax.admitted is False
    assert any("exceeds usable quota headroom" in reason for reason in minimax.reasons)
    assert decision.selected_execution_target_id is None


def test_scheduler_selects_known_minimax_and_excludes_unknown_glm() -> None:
    registry = _scheduler_registry(minimax_remaining=0.65, minimax_reset_minutes=120)
    decision = route_task(
        registry,
        task=TaskProfile(
            task_id="swift-debug",
            pool=PoolKind.WORKER,
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.06,
        ),
        now=NOW,
        known_at=NOW,
        runtime_availability={"minimax-sub": True, "glm-sub": True},
    )

    assert decision.selected_execution_target_id == "minimax-sub"
    assert decision.selected_model_sku_id == "minimax-m3"
    glm = next(item for item in decision.evaluations if item.execution_target_id == "glm-sub")
    assert glm.admitted is False
    assert any("unknown" in reason for reason in glm.reasons)


def _scheduler_registry(*, minimax_remaining: float, minimax_reset_minutes: int) -> ModelRegistry:
    providers = {
        "minimax": Provider(id="minimax", display_name="MiniMax"),
        "zai": Provider(id="zai", display_name="Z.AI"),
    }
    accounts = {
        "minimax-a": Account(id="minimax-a", provider_id="minimax", label="MiniMax"),
        "zai-a": Account(id="zai-a", provider_id="zai", label="Z.AI"),
    }
    plans = {
        "minimax-plan": Plan(
            id="minimax-plan",
            account_id="minimax-a",
            name="Coding Plan",
            kind=PlanKind.SUBSCRIPTION,
        ),
        "zai-plan": Plan(
            id="zai-plan",
            account_id="zai-a",
            name="Coding Plan",
            kind=PlanKind.SUBSCRIPTION,
        ),
    }
    minimax_snapshot = QuotaSnapshot(
        quota_pool_id="minimax-pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source(),
        windows=(
            quota_window(
                "5h",
                QuotaWindowKind.FIVE_HOUR,
                remaining=minimax_remaining,
                reset_delta=timedelta(minutes=minimax_reset_minutes),
                duration=timedelta(hours=5),
            ),
        ),
    )
    glm_snapshot = QuotaSnapshot(
        quota_pool_id="glm-pool",
        observed_at=NOW,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=source(EvidenceConfidence.UNKNOWN),
        windows=(
            quota_window(
                "5h",
                QuotaWindowKind.FIVE_HOUR,
                remaining=None,
                reset_delta=None,
                duration=timedelta(hours=5),
                confidence=EvidenceConfidence.UNKNOWN,
            ),
        ),
    )
    pools = {
        "minimax-pool": QuotaPool(
            id="minimax-pool",
            plan_id="minimax-plan",
            name="MiniMax shared",
            snapshot=minimax_snapshot,
            reserve_fraction=0.0,
            required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
        ),
        "glm-pool": QuotaPool(
            id="glm-pool",
            plan_id="zai-plan",
            name="GLM shared",
            snapshot=glm_snapshot,
            reserve_fraction=0.15,
            required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
        ),
    }
    models = {
        "minimax-m3": ModelSKU(
            id="minimax-m3",
            provider_id="minimax",
            display_name="M3",
            capabilities=CapabilityProfile(scores={"debugging": 0.91}),
        ),
        "glm-5.3": ModelSKU(
            id="glm-5.3",
            provider_id="zai",
            display_name="GLM-5.3",
            capabilities=CapabilityProfile(scores={"debugging": 0.96}),
        ),
    }
    targets = {
        "minimax-sub": ExecutionTarget(
            id="minimax-sub",
            model_sku_id="minimax-m3",
            account_id="minimax-a",
            runtime_id="opencode",
        ),
        "glm-sub": ExecutionTarget(
            id="glm-sub",
            model_sku_id="glm-5.3",
            account_id="zai-a",
            runtime_id="opencode",
        ),
    }
    bindings = (
        QuotaBinding(
            id="m3-binding",
            model_sku_id="minimax-m3",
            execution_target_id="minimax-sub",
            quota_pool_id="minimax-pool",
            effective_from=NOW - timedelta(days=1),
            recorded_at=NOW - timedelta(days=1),
            confidence=EvidenceConfidence.EXACT,
            source=source(),
        ),
        QuotaBinding(
            id="glm-binding",
            model_sku_id="glm-5.3",
            execution_target_id="glm-sub",
            quota_pool_id="glm-pool",
            effective_from=NOW - timedelta(days=1),
            recorded_at=NOW - timedelta(days=1),
            confidence=EvidenceConfidence.UNKNOWN,
            source=source(EvidenceConfidence.UNKNOWN),
        ),
    )
    memberships = (
        PoolMembership(pool=PoolKind.WORKER, model_sku_id="minimax-m3", priority=1),
        PoolMembership(pool=PoolKind.WORKER, model_sku_id="glm-5.3", priority=1),
    )
    return ModelRegistry(
        providers=providers,
        accounts=accounts,
        plans=plans,
        quota_pools=pools,
        models=models,
        execution_targets=targets,
        quota_bindings=bindings,
        pool_memberships=memberships,
    )
