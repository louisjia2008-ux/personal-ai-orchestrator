from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from personal_ai_orchestrator.delegation_evidence import (
    DelegationCommercialMode,
    DelegationOutcomeJournal,
    DelegationOutcomePhase,
    build_delegation_host_evidence,
    commercial_mode_for_target,
    predicted_child_burn,
    resolve_delegation_policy,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from personal_ai_orchestrator.model_registry import (
    Account,
    ConsumptionRule,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import (
    RiskClass,
    RoutingObjective,
    RoutingPolicy,
    SchedulingPolicyLevel,
    TaskProfile,
)
from tests.test_pi5_child_execution import _setup

NOW = datetime(2026, 9, 13, 8, 0, tzinfo=UTC)
TARGET = "target-1"
POOL = "pool-1"
MODEL = "provider/model"


def _source() -> EvidenceSource:
    return EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=NOW,
        confidence=EvidenceConfidence.EXACT,
    )


def _registry(
    *,
    plan_kind: PlanKind = PlanKind.SUBSCRIPTION,
    unmetered: bool = False,
    multiplier: float = 1.0,
) -> ModelRegistry:
    source = _source()
    window = QuotaWindowSnapshot(
        window_id="window-1",
        window_kind=(QuotaWindowKind.UNMETERED if unmetered else QuotaWindowKind.WEEKLY),
        remaining_fraction=None if unmetered else 0.60,
        used_fraction=None if unmetered else 0.40,
        window_started_at=None if unmetered else NOW - timedelta(days=1),
        reset_at=None if unmetered else NOW + timedelta(days=6),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="snapshot-1",
        quota_pool_id=POOL,
        provider_id="provider",
        observed_at=NOW,
        recorded_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"provider": Provider(id="provider", display_name="Provider")},
        accounts={
            "account": Account(
                id="account",
                provider_id="provider",
                label="Account",
            )
        },
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Plan",
                kind=plan_kind,
            )
        },
        quota_pools={
            POOL: QuotaPool(
                id=POOL,
                plan_id="plan",
                name="Pool",
                snapshot=snapshot,
                reserve_fraction=0.10,
            )
        },
        models={
            MODEL: ModelSKU(
                id=MODEL,
                provider_id="provider",
                display_name="Model",
            )
        },
        execution_targets={
            TARGET: ExecutionTarget(
                id=TARGET,
                model_sku_id=MODEL,
                account_id="account",
                runtime_id="pi",
                execution_verified=True,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding-1",
                model_sku_id=MODEL,
                execution_target_id=TARGET,
                quota_pool_id=POOL,
                effective_from=NOW - timedelta(days=2),
                recorded_at=NOW - timedelta(days=2),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
        consumption_rules=(
            ConsumptionRule(
                id="rule-1",
                model_sku_id=MODEL,
                execution_target_id=TARGET,
                quota_pool_id=POOL,
                multiplier=multiplier,
                effective_from=NOW - timedelta(days=2),
                recorded_at=NOW - timedelta(days=2),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
    )


def _store_with_task(tmp_path):
    store = SafetyKernelStore(tmp_path / "state.db")
    store.register_project(
        project_id="project",
        display_name="Project",
        canonical_repo_root=str(tmp_path / "repo"),
        git_root=str(tmp_path / "repo"),
        default_branch="main",
        last_known_head="abc",
    )
    task = store.submit_task(
        task_id="parent",
        request_id="submit-parent",
        intent="parent",
        project_id="project",
        base_sha="abc",
    )
    return store, task


def test_policy_resolution_matches_task_project_global_precedence(tmp_path):
    store, task = _store_with_task(tmp_path)
    try:
        global_policy = RoutingPolicy(
            objective=RoutingObjective.BALANCED,
            allow_paid_usage=True,
            failure_escalation_after=7,
        )
        settings = SimpleNamespace(default_policy="SPEED_FIRST")

        global_only = resolve_delegation_policy(
            store=store,
            task=task,
            global_policy=global_policy,
            scheduling_settings=settings,
        )
        assert global_only.resolved_level is SchedulingPolicyLevel.GLOBAL_DEFAULT
        assert global_only.policy.objective is RoutingObjective.SPEED_FIRST
        assert global_only.policy.allow_paid_usage is True
        assert global_only.policy.failure_escalation_after == 7

        store.set_project_scheduling_policy(
            "project",
            scheduling_policy="QUALITY_FIRST",
        )
        task = store.get_task("parent")
        project = resolve_delegation_policy(
            store=store,
            task=task,
            global_policy=global_policy,
            scheduling_settings=settings,
        )
        assert project.resolved_level is SchedulingPolicyLevel.PROJECT_OVERRIDE
        assert project.policy.objective is RoutingObjective.QUALITY_FIRST
        assert project.policy.allow_paid_usage is True

        store.force_task_scheduling_policy(
            "parent",
            scheduling_policy="QUOTA_SAVER",
        )
        task = store.get_task("parent")
        task_level = resolve_delegation_policy(
            store=store,
            task=task,
            global_policy=global_policy,
            scheduling_settings=settings,
        )
        assert task_level.resolved_level is SchedulingPolicyLevel.TASK_OVERRIDE
        assert task_level.policy.objective is RoutingObjective.QUOTA_SAVER
        assert task_level.policy.failure_escalation_after == 7
    finally:
        store.close()


@pytest.mark.parametrize(
    ("plan_kind", "unmetered", "availability", "expected"),
    [
        (PlanKind.SUBSCRIPTION, False, None, DelegationCommercialMode.SUBSCRIPTION),
        (PlanKind.PREPAID, False, None, DelegationCommercialMode.PREPAID),
        (PlanKind.PAY_AS_YOU_GO, False, None, DelegationCommercialMode.PAY_AS_YOU_GO),
        (PlanKind.SUBSCRIPTION, True, None, DelegationCommercialMode.UNMETERED),
        (
            PlanKind.SUBSCRIPTION,
            False,
            "AVAILABLE_UNMETERED",
            DelegationCommercialMode.UNMETERED,
        ),
        (PlanKind.UNKNOWN, False, None, DelegationCommercialMode.UNKNOWN),
    ],
)
def test_commercial_semantics_come_from_registry_truth(
    plan_kind,
    unmetered,
    availability,
    expected,
):
    registry = _registry(plan_kind=plan_kind, unmetered=unmetered)
    mode, pool = commercial_mode_for_target(
        registry,
        execution_target_id=TARGET,
        observed_availability_state=availability,
        now=NOW,
    )
    assert mode is expected
    assert pool == POOL


def test_parent_burn_is_never_reused_as_child_burn():
    registry = _registry(multiplier=2.0)
    parent = TaskProfile(
        task_id="parent",
        risk=RiskClass.HIGH,
        failure_count=3,
        predicted_quota_fraction_p90=0.80,
    )
    policy = SimpleNamespace(
        policy=RoutingPolicy(
            allow_paid_usage=False,
            failure_escalation_after=4,
        ),
        resolved_level=SchedulingPolicyLevel.TASK_OVERRIDE,
    )

    evidence = build_delegation_host_evidence(
        registry=registry,
        parent_profile=parent,
        child_profile=None,
        policy_resolution=policy,
        parent_execution_target_id=TARGET,
        child_execution_target_id=TARGET,
        child_remaining_fractions=(0.60,),
        child_observed_availability_state="AVAILABLE_OBSERVED",
        now=NOW,
    )
    assert evidence.parent_risk is RiskClass.HIGH
    assert evidence.parent_failure_count == 3
    assert evidence.child_predicted_quota_fraction_p90 is None
    assert evidence.predicted_child_burn_fraction is None
    assert "predicted_child_burn_unavailable" in evidence.limitations

    child = TaskProfile(
        task_id="child",
        predicted_quota_fraction_p90=0.10,
    )
    predicted, limitation = predicted_child_burn(
        registry,
        child_profile=child,
        execution_target_id=TARGET,
        now=NOW,
    )
    assert limitation is None
    assert predicted == pytest.approx(0.20)

    enriched = build_delegation_host_evidence(
        registry=registry,
        parent_profile=parent,
        child_profile=child,
        policy_resolution=policy,
        parent_execution_target_id=TARGET,
        child_execution_target_id=TARGET,
        child_remaining_fractions=(0.60,),
        child_observed_availability_state="AVAILABLE_OBSERVED",
        now=NOW,
    )
    assert enriched.predicted_child_burn_fraction == pytest.approx(0.20)
    # 0.60 raw - 0.10 pool reserve - 0.02 policy uncertainty.
    assert enriched.usable_child_headroom_fraction == pytest.approx(0.48)
    assert enriched.same_quota_pool_as_parent is True


def test_payg_and_missing_host_signals_remain_explicit():
    registry = _registry(plan_kind=PlanKind.PAY_AS_YOU_GO)
    resolution = SimpleNamespace(
        policy=RoutingPolicy(allow_paid_usage=False),
        resolved_level=SchedulingPolicyLevel.GLOBAL_DEFAULT,
    )
    evidence = build_delegation_host_evidence(
        registry=registry,
        parent_profile=None,
        child_profile=None,
        policy_resolution=resolution,
        parent_execution_target_id=TARGET,
        child_execution_target_id=TARGET,
        child_remaining_fractions=(0.60,),
        child_observed_availability_state="AVAILABLE_OBSERVED",
        now=NOW,
    )
    assert evidence.child_commercial_mode is DelegationCommercialMode.PAY_AS_YOU_GO
    assert evidence.paid_usage_required is True
    assert evidence.resolved_policy.allow_paid_usage is False
    assert "host_required_delegation_signal_not_exposed" in evidence.limitations
    assert "independence_requirement_signal_not_exposed" in evidence.limitations
    assert "task_profile_failure_count_not_available_in_child_dispatch_context" in (
        evidence.limitations
    )
    assert evidence.enforcement_ready is False


@pytest.mark.asyncio
async def test_shadow_and_outcomes_are_separate_append_only_records(tmp_path, monkeypatch):
    store, _, port, _, plan, _ = _setup(tmp_path, monkeypatch)
    shadow = DelegationShadowJournal(tmp_path / "evidence")
    outcomes = DelegationOutcomeJournal(tmp_path / "evidence")
    port.delegation_shadow_journal = shadow
    port.delegation_outcome_journal = outcomes
    parent_profile = TaskProfile(
        task_id=plan.parent_task_id,
        risk=RiskClass.HIGH,
        failure_count=2,
    )
    port.task_profile_provider = lambda task_id: (
        parent_profile if task_id == plan.parent_task_id else None
    )
    try:
        result = await port.execute_child(plan)
        assert result.verified
        observation_id = shadow.observation_id(
            parent_run_id=plan.parent_run_id,
            child_task_id=plan.child_task_id,
        )
        shadow_path = shadow.path_for(observation_id)
        shadow_before = shadow_path.read_bytes()

        child_outcome = outcomes.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        )
        assert child_outcome is not None
        assert child_outcome.child_state == "VERIFIED"
        assert child_outcome.child_verified is True
        assert child_outcome.parent_state == "RUNNING"
        assert child_outcome.parent_verified is False
        assert (
            outcomes.load(
                observation_id=observation_id,
                phase=DelegationOutcomePhase.PARENT_FINAL,
            )
            is None
        )

        store.finish_run("parent-run", status="FINISHED", result={})
        parent = store.get_task(plan.parent_task_id)
        parent = store.transition_task(
            parent.task_id,
            TaskState.WORKER_FINISHED,
            expected_version=parent.state_version,
            reason="synthetic parent worker finished",
        )
        parent = store.transition_task(
            parent.task_id,
            TaskState.VERIFYING,
            expected_version=parent.state_version,
            reason="synthetic parent verification",
        )
        store.transition_task(
            parent.task_id,
            TaskState.VERIFIED,
            expected_version=parent.state_version,
            reason="synthetic deterministic verifier pass",
        )
        port.record_parent_final_outcomes(
            parent_task_id=plan.parent_task_id,
            parent_run_id=plan.parent_run_id,
        )

        parent_outcome = outcomes.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.PARENT_FINAL,
        )
        assert parent_outcome is not None
        assert parent_outcome.parent_state == "VERIFIED"
        assert parent_outcome.parent_verified is True
        assert parent_outcome.parent_verifier_passed is True
        assert shadow_path.read_bytes() == shadow_before
        assert outcomes.path_for(child_outcome.outcome_id) != outcomes.path_for(
            parent_outcome.outcome_id
        )
    finally:
        store.close()


def test_outcome_journal_is_first_observation_and_rejects_unsafe_ids(tmp_path):
    journal = DelegationOutcomeJournal(tmp_path)
    for value in ("", "../escape", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            journal.path_for(value)


def test_infra_blocked_row_does_not_create_quality_failure_count(tmp_path):
    store, task = _store_with_task(tmp_path)
    try:
        store.reserve_owner_dispatch(
            dispatch_id="infra-dispatch",
            request_id="infra-request",
            task_id=task.task_id,
            task_state_version=task.state_version,
            execution_target_id=TARGET,
            authority="OWNER_INITIATED_EXECUTION",
        )
        store.mark_owner_dispatch_blocked(
            "infra-request",
            failure_code="WORKER_SPAWN_FAILED",
            failure_reason="synthetic infrastructure failure",
        )
        evidence = build_delegation_host_evidence(
            registry=_registry(),
            parent_profile=None,
            child_profile=None,
            policy_resolution=None,
            parent_execution_target_id=TARGET,
            child_execution_target_id=TARGET,
            child_remaining_fractions=(0.60,),
            child_observed_availability_state="AVAILABLE_OBSERVED",
            now=NOW,
        )
        assert evidence.parent_failure_count is None
        assert "task_profile_failure_count_not_available_in_child_dispatch_context" in (
            evidence.limitations
        )
    finally:
        store.close()
