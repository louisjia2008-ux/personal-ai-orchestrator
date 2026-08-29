from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.activation import ActiveRoutingGate
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
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.scheduler import RiskClass, TargetTelemetry, TaskProfile, route_task

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _source() -> EvidenceSource:
    return EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://quota",
        confidence=EvidenceConfidence.EXACT,
    )


def _registry() -> ModelRegistry:
    source = _source()
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18000,
        remaining_fraction=0.8,
        window_started_at=NOW - timedelta(hours=3),
        reset_at=NOW + timedelta(hours=2),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-1",
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax")},
        accounts={
            "account": Account(id="account", provider_id="minimax", label="subscription")
        },
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="shared",
                snapshot=snapshot,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
            )
        },
        models={
            "m3": ModelSKU(
                id="m3",
                provider_id="minimax",
                display_name="M3",
                capabilities=CapabilityProfile(scores={"debugging": 0.9, "reasoning": 0.85}),
            )
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding",
                model_sku_id="m3",
                execution_target_id="m3-sub",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="m3",
                execution_target_id="m3-sub",
            ),
        ),
    )


def _task(**overrides: object) -> TaskProfile:
    values: dict[str, object] = {
        "task_id": "task-1",
        "required_capabilities": {"debugging": 0.8},
        "predicted_quota_fraction_p90": 0.05,
    }
    values.update(overrides)
    return TaskProfile(**values)


def _scheduler(registry: ModelRegistry):
    return route_task(
        registry,
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
    )


def test_high_risk_task_fails_closed_without_reliability_prior() -> None:
    decision = route_task(
        _registry(),
        task=_task(risk=RiskClass.HIGH),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
    )
    candidate = decision.evaluations[0]
    assert candidate.eligible is False
    assert any("success prior" in reason for reason in candidate.reasons)


def test_context_vision_and_tool_requirements_are_hard_gates() -> None:
    decision = route_task(
        _registry(),
        task=_task(
            required_context_tokens=200_000,
            requires_vision=True,
            required_tools=("terminal", "filesystem"),
        ),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
        telemetry={
            "m3-sub": TargetTelemetry(
                success_prior=0.9,
                context_window_tokens=128_000,
                supports_vision=False,
                supported_tools=("terminal",),
            )
        },
    )
    reasons = decision.evaluations[0].reasons
    assert any("context window too small" in reason for reason in reasons)
    assert any("vision" in reason for reason in reasons)
    assert any("filesystem" in reason for reason in reasons)


def test_failure_count_activates_escalation_floor() -> None:
    registry = _registry()
    weak_model = registry.models["m3"].model_copy(
        update={"capabilities": CapabilityProfile(scores={"debugging": 0.9, "reasoning": 0.4})}
    )
    registry = registry.model_copy(update={"models": {"m3": weak_model}})
    decision = route_task(
        registry,
        task=_task(failure_count=2),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
    )
    assert any(
        "failure escalation floor failed" in reason for reason in decision.evaluations[0].reasons
    )


def test_bridge_freezes_snapshot_refs_and_blocks_unapproved_active() -> None:
    registry = _registry()
    request = RoutingRequest(
        request_id="req-1",
        session_id="session-1",
        mode=RoutingMode.ACTIVE,
        task_id="task-1",
        requested_at=NOW,
    )
    decision = build_routing_decision(
        request,
        _scheduler(registry),
        registry,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        decided_at=NOW,
    )
    assert decision.selected_execution_target_id == "m3-sub"
    assert decision.selected_model is not None
    assert decision.quota_snapshot_ids == ("quota-1",)
    assert decision.switch_requested is False
    assert "ACTIVE gate" in (decision.fallback_reason or "")


def test_bridge_allows_active_only_with_complete_gate() -> None:
    registry = _registry()
    request = RoutingRequest(
        request_id="req-active",
        session_id="session-1",
        mode=RoutingMode.ACTIVE,
        task_id="task-1",
        requested_at=NOW,
    )
    gate = ActiveRoutingGate(
        p0_safety_kernel_authoritative=True,
        p1_verifier_authoritative=True,
        adapter_fail_closed_validated=True,
        shadow_evidence_accepted=True,
        safe_bypass_validated=True,
        owner_approved=True,
    )
    decision = build_routing_decision(
        request,
        _scheduler(registry),
        registry,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        activation_gate=gate,
        decided_at=NOW,
    )
    assert decision.switch_requested is True


def test_bridge_is_idempotent_for_identical_inputs() -> None:
    registry = _registry()
    request = RoutingRequest(
        request_id="req-stable",
        session_id="session-1",
        mode=RoutingMode.SHADOW,
        requested_at=NOW,
    )
    first = build_routing_decision(
        request,
        _scheduler(registry),
        registry,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        decided_at=NOW,
    )
    second = build_routing_decision(
        request,
        _scheduler(registry),
        registry,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        decided_at=NOW,
    )
    assert first.decision_id == second.decision_id
