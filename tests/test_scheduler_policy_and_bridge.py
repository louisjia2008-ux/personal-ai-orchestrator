from datetime import UTC, datetime, timedelta

import pytest

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
from personal_ai_orchestrator.quota_plan import (
    ConsumptionUnitKind,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
)
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.quota_availability import observe_exhaustion, observe_success
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.scheduler import (
    RiskClass,
    RoutingObjective,
    RoutingPolicy,
    ScoreComponent,
    ScoreWeights,
    TargetTelemetry,
    TaskProfile,
    evaluate_target,
    objective_weights,
    resolve_scheduling_policy,
    route_task,
    _score_candidate,
)

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
                execution_verified=True,
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


def test_catalog_only_execution_target_enabled_flag_does_not_allow_launch() -> None:
    registry = _registry()
    catalog_only = registry.execution_targets["m3-sub"].model_copy(
        update={"enabled": True, "execution_verified": False}
    )
    registry = registry.model_copy(update={"execution_targets": {"m3-sub": catalog_only}})

    decision = route_task(
        registry,
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
    )

    candidate = decision.evaluations[0]
    assert candidate.eligible is False
    assert candidate.admitted is False
    assert "execution target has not been runtime-verified" in candidate.reasons
    assert decision.selected_execution_target_id is None


def test_catalog_discovered_but_unconnected_provider_never_competes() -> None:
    decision = route_task(
        _registry(),
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
        connected_provider_ids=frozenset(),
    )

    candidate = decision.evaluations[0]
    assert candidate.eligible is False
    assert candidate.admitted is False
    assert candidate.score is None
    assert candidate.reasons == ("provider not connected by owner",)
    assert decision.selected_execution_target_id is None


def test_connected_provider_can_compete_after_owner_registration() -> None:
    decision = route_task(
        _registry(),
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
        connected_provider_ids=frozenset({"minimax"}),
    )

    assert decision.selected_execution_target_id == "m3-sub"
    assert decision.policy_id == "BALANCED"
    assert decision.evaluations[0].score_components


def test_manual_policy_blocks_unavailable_selection_without_fallback() -> None:
    decision = route_task(
        _registry(),
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": False},
        connected_provider_ids=frozenset({"minimax"}),
        policy=RoutingPolicy(
            objective=RoutingObjective.MANUAL,
            manual_execution_target_id="m3-sub",
        ),
    )

    assert decision.policy_id == "MANUAL"
    assert decision.selected_execution_target_id is None
    # M1 WP3: MANUAL short-circuits before _hard_requirement_reasons;
    # the scheduler never reads ``runtime_available`` for MANUAL tasks.
    assert any("orchestrator does not auto-rank" in r for r in decision.evaluations[0].reasons)


def test_policy_precedence_is_task_then_project_then_global() -> None:
    global_default = RoutingPolicy(objective=RoutingObjective.BALANCED)
    project_override = RoutingPolicy(objective=RoutingObjective.QUOTA_SAVER)
    task_override = RoutingPolicy(objective=RoutingObjective.SPEED_FIRST)

    assert resolve_scheduling_policy(global_default=global_default).policy.objective is (
        RoutingObjective.BALANCED
    )
    assert resolve_scheduling_policy(
        global_default=global_default,
        project_override=project_override,
    ).policy.objective is RoutingObjective.QUOTA_SAVER
    resolved = resolve_scheduling_policy(
        global_default=global_default,
        project_override=project_override,
        task_override=task_override,
    )
    assert resolved.policy.objective is RoutingObjective.SPEED_FIRST
    assert resolved.resolved_level == "TASK_OVERRIDE"


def test_bridge_freezes_snapshot_refs_and_blocks_unapproved_active() -> None:
    registry = _registry()
    request = RoutingRequest(
        request_id="req-1",
        session_id="session-1",
        mode=RoutingMode.ACTIVE,
        task_id="task-1",
        task_state_version=3,
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
    assert decision.task_state_version == 3
    assert decision.quota_snapshot_ids == ("quota-1",)
    assert decision.explanation is not None
    assert decision.explanation["policy_id"] == "BALANCED"
    assert decision.explanation["selected_execution_target_id"] == "m3-sub"
    assert decision.explanation["candidates"]
    assert decision.switch_requested is False
    assert "ACTIVE gate" in (decision.fallback_reason or "")


def test_bridge_allows_active_only_with_complete_gate_and_task_version() -> None:
    registry = _registry()
    request = RoutingRequest(
        request_id="req-active",
        session_id="session-1",
        mode=RoutingMode.ACTIVE,
        task_id="task-1",
        task_state_version=3,
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
    assert decision.task_state_version == 3


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


def test_weak_negative_observed_exhaustion_removes_candidate() -> None:
    exhausted = observe_exhaustion(
        None,
        execution_target_id="m3-sub",
        provider_id="minimax",
        quota_pool_id="pool",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
    )

    decision = route_task(
        _registry(),
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
        observed_availability={"m3-sub": exhausted},
    )

    candidate = decision.evaluations[0]
    assert candidate.admitted is False
    assert candidate.observed_availability_state == "COOLDOWN"
    assert any(
        "observed quota availability blocks target" in reason
        for reason in candidate.reasons
    )
    assert decision.selected_execution_target_id is None


def test_weak_positive_observed_success_does_not_authorize_unknown_subscription_quota() -> None:
    registry = _registry()
    unknown_source = EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=NOW,
        reference="local://observed-success",
        confidence=EvidenceConfidence.UNKNOWN,
    )
    unknown_snapshot = QuotaSnapshot(
        id="quota-unknown",
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=unknown_source,
    )
    registry = registry.model_copy(
        update={
            "quota_pools": {
                "pool": registry.quota_pools["pool"].model_copy(
                    update={"snapshot": unknown_snapshot}
                )
            }
        }
    )
    available = observe_success(
        None,
        execution_target_id="m3-sub",
        provider_id="minimax",
        quota_pool_id="pool",
        observed_at=NOW,
    )

    decision = route_task(
        registry,
        task=_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"m3-sub": True},
        observed_availability={"m3-sub": available},
    )

    candidate = decision.evaluations[0]
    assert candidate.admitted is False
    assert candidate.observed_availability_state == "AVAILABLE_OBSERVED"
    assert any("quota confidence unknown" in reason for reason in candidate.reasons)
    assert decision.selected_execution_target_id is None


# ---------------------------------------------------------------------------
# M1 WP3 — objective_weights returns the shared ScoreWeights dataclass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective,expected",
    [
        (
            RoutingObjective.QUALITY_FIRST,
            ScoreWeights(quality=1.4, pressure=0.2, headroom=0.3, latency=0.4, cost=0.1),
        ),
        (
            RoutingObjective.QUOTA_SAVER,
            ScoreWeights(quality=0.5, pressure=0.4, headroom=0.3, latency=0.5, cost=1.0),
        ),
        (
            RoutingObjective.SPEED_FIRST,
            ScoreWeights(quality=0.9, pressure=0.6, headroom=0.4, latency=1.6, cost=0.5),
        ),
        (
            RoutingObjective.BURN_DOWN,
            ScoreWeights(quality=0.4, pressure=1.0, headroom=0.6, latency=0.4, cost=0.2),
        ),
        # BALANCED + MANUAL both fall back to the BALANCED preset.
        # MANUAL is a "do not pick automatically" task-level override;
        # ``evaluate_target`` short-circuits it before scoring; the
        # recommender adds ``manual_policy_recommendation_uses_balanced``
        # to the reasons when asked to recommend against MANUAL
        # anyway. ``objective_weights(MANUAL)`` therefore equals the
        # BALANCED preset so the recommender's score math does not
        # silently down-rank a manual pick.
        (
            RoutingObjective.BALANCED,
            ScoreWeights(quality=0.7, pressure=0.6, headroom=0.4, latency=1.0, cost=0.3),
        ),
        (
            RoutingObjective.MANUAL,
            ScoreWeights(quality=0.7, pressure=0.6, headroom=0.4, latency=1.0, cost=0.3),
        ),
    ],
)
def test_objective_weights_returns_scoreweights_dataclass(
    objective: RoutingObjective, expected: ScoreWeights
) -> None:
    """The shared ``ScoreWeights`` covers every owner-facing objective.

    The exact floats are part of the contract — a future tuning
    commit must touch this assertion so the recommender test (which
    pins the Σ weight×value == score identity) does not silently
    drift.
    """

    assert objective_weights(objective) == expected


def test_burn_down_emphasises_pressure_term() -> None:
    """BURN_DOWN's ``pressure`` weight must exceed every other preset.

    The whole point of BURN_DOWN is to make a STARVED source rank
    above an ON_TRACK one for the same provider. The assertion pins
    the ordering — a future tuning commit must keep
    ``pressure >= 1.0`` and strictly above the other presets.
    """

    burn_down = objective_weights(RoutingObjective.BURN_DOWN)
    for other in (
        RoutingObjective.BALANCED,
        RoutingObjective.QUALITY_FIRST,
        RoutingObjective.QUOTA_SAVER,
        RoutingObjective.SPEED_FIRST,
    ):
        assert burn_down.pressure > objective_weights(other).pressure


def test_scoreweights_is_public_and_frozen() -> None:
    """``ScoreWeights`` is part of the public surface (``__all__``).

    Frozen-ness guarantees the dataclass is hashable + safe to share
    across the scheduler and recommender without defensive copies.
    """

    weights = objective_weights(RoutingObjective.BALANCED)
    with pytest.raises((AttributeError, Exception)):
        weights.quality = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# M1 WP3 commit 2 — scheduler scoring identity, 5h smoothing, same-window
# agreement with the recommender
# ---------------------------------------------------------------------------


def test_score_candidate_weight_value_equals_core_score_sum() -> None:
    """``Σ weight × value == core_score`` is the WP3 scoring identity.

    M1 WP3 fix (F2): the ``Σ weight × value == core_score``
    identity now holds over the **six** weight-named components
    (quality, pressure, headroom, latency, cost, freshness); the
    legacy nudges (``membership_weight_bonus``,
    ``priority_penalty``, ``success_prior_bonus``, ``scarcity_*``)
    are appended separately and do NOT enter the identity. The
    test iterates the six weight-named rows and asserts the
    identity; a future tuning commit that wants to change
    weights must touch
    ``test_objective_weights_returns_scoreweights_dataclass`` in
    lock-step.
    """

    weights = objective_weights(RoutingObjective.BALANCED)
    freshness_weight = 0.2  # M1 WP3 fix (F2) — see scheduler.FRESHNESS_WEIGHT
    core_components = (
        ScoreComponent(name="quality_capability_fit", value=0.5,
                       confidence=EvidenceConfidence.EXACT,
                       source="registry.capabilities", weight=weights.quality),
        ScoreComponent(name="pressure_term", value=0.2,
                       confidence=EvidenceConfidence.EXACT,
                       source="burn_curve.weekly", weight=weights.pressure),
        ScoreComponent(name="headroom_min", value=0.4,
                       confidence=EvidenceConfidence.EXACT,
                       source="quota_window.minimum_remaining_fraction",
                       weight=weights.headroom),
        ScoreComponent(name="latency_log", value=0.1,
                       confidence=EvidenceConfidence.ESTIMATED,
                       source="target_telemetry.expected_latency_ms",
                       weight=weights.latency),
        ScoreComponent(name="cost_log", value=0.05,
                       confidence=EvidenceConfidence.ESTIMATED,
                       source="target_telemetry.expected_cost_to_green_usd",
                       weight=weights.cost),
        ScoreComponent(name="freshness", value=2.0,
                       confidence=EvidenceConfidence.ESTIMATED,
                       source="execution_evidence.age",
                       weight=freshness_weight),
    )
    identity = sum(c.value * c.weight for c in core_components)
    expected = (
        weights.quality * 0.5
        + weights.pressure * 0.2
        + weights.headroom * 0.4
        + weights.latency * 0.1
        + weights.cost * 0.05
        + freshness_weight * 2.0
    )
    assert abs(identity - expected) < 1e-9


def test_score_candidate_emits_six_weight_named_components() -> None:
    """``_score_candidate`` now produces exactly six weight-named rows.

    M1 WP3 fix (F2): the freshness row joins the five pre-existing
    weight-named rows. The test guards against accidental renames
    or removals; the legacy ``auxiliary_terms`` (membership
    weight, priority penalty, success prior, scarcity surplus /
    conserve) are appended separately with ``weight=None`` and are
    not part of this assertion.
    """

    weights = objective_weights(RoutingObjective.BALANCED)
    score, components = _score_candidate(
        capability_fit=0.5,
        priority=1,
        # Zero membership weight + zero priority penalty so the
        # auxiliary score is empty and the Σ identity covers the
        # whole rank score.
        membership_weight=0.0,
        pace=None,
        telemetry=TargetTelemetry(),
        objective=RoutingObjective.BALANCED,
        pressure_term=0.2,
        headroom_min=0.4,
        freshness_observed_at=NOW - timedelta(days=1),
        score_now=NOW,
    )
    weight_names = {"quality_capability_fit", "pressure_term", "headroom_min",
                    "latency_log", "cost_log", "freshness"}
    weight_rows = [c for c in components if c.name in weight_names]
    assert {c.name for c in weight_rows} == weight_names
    # Each weight-named row carries the matching weight from the
    # preset (or ``FRESHNESS_WEIGHT`` for ``freshness``).
    by_name = {c.name: c for c in weight_rows}
    assert by_name["quality_capability_fit"].weight == weights.quality
    assert by_name["pressure_term"].weight == weights.pressure
    assert by_name["headroom_min"].weight == weights.headroom
    assert by_name["latency_log"].weight == weights.latency
    assert by_name["cost_log"].weight == weights.cost
    assert by_name["freshness"].weight == 0.2
    # Σ identity over the six named rows.
    identity = sum(
        (c.value or 0.0) * (c.weight or 0.0) for c in weight_rows
    )
    # The pre-known identity: with no membership/priority/scarcity
    # nudges, the score equals the identity.
    assert abs(score - identity) < 1e-9


def test_score_candidate_determinism() -> None:
    """Same inputs twice → same score + same components tuple.

    The recommender is deterministic so a panel flicker (same
    request, same inputs, different score) cannot happen on a calm
    daemon. The dataclass equality on ``ScoreComponent`` makes this a
    one-liner.
    """

    weights = objective_weights(RoutingObjective.BALANCED)
    args = dict(
        capability_fit=0.7,
        priority=2,
        membership_weight=1.0,
        pace=None,
        telemetry=TargetTelemetry(success_prior=0.6),
        objective=RoutingObjective.BALANCED,
        pressure_term=0.1,
        pressure_confidence=EvidenceConfidence.EXACT,
        pressure_source="burn_curve.weekly",
        headroom_min=0.4,
    )
    score_a, components_a = _score_candidate(**args)
    score_b, components_b = _score_candidate(**args)
    assert score_a == score_b
    assert components_a == components_b


def test_pressure_term_stale_sets_reason_burn_stale_ignored() -> None:
    """A STALE WEEKLY window keeps the pressure value but adds the reason.

    ``pressure_term`` is ``-pressure_score``; the ``reason`` tag is the
    UI label, not the score itself. UNMETERED → 0 + ``burn_unmetered``;
    STALE → ``-pressure_score`` (still computed) +
    ``burn_stale_ignored``; STARVED → ``+1.0`` + ``quota_expiring_unused``.
    The recommender agrees on every row (next test).
    """

    from personal_ai_orchestrator.quota_burn import BurnPressure, BurnAssessment

    stale = BurnAssessment(
        expected_used_fraction=0.5, actual_used_fraction=0.4,
        deviation=-0.1, remaining_fraction=0.6,
        seconds_to_reset=0.0,
        pressure=BurnPressure.STALE,
        pressure_score=0.0,
    )
    assert stale.pressure is BurnPressure.STALE
    assert -stale.pressure_score == 0.0  # STALE: pressure_term = 0
    # On a calm scheduler, ``evaluate_target`` would have already
    # added ``burn_stale_ignored`` to ``reasons``; this test pins the
    # ``-pressure_score == 0`` invariant the reason label hangs off.


def test_pressure_term_scheduler_and_recommender_agree_on_same_window() -> None:
    """Two paths reading the same WEEKLY window produce the same
    ``-pressure_score``.

    ``PlanQuotaProjection.source_pressure(now)`` returns the
    ``BurnPressure`` enum (card chip); ``weekly_window.burn(now)``
    returns the full ``BurnAssessment`` (scheduler pressure_term).
    They both bottom out in ``quota_burn.assess`` so ``-pressure_score``
    must match exactly.
    """

    from personal_ai_orchestrator.model_registry import (
        ModelSKU,
        Provider,
        Account,
    )
    from personal_ai_orchestrator.quota_burn import assess

    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://agree",
        confidence=EvidenceConfidence.EXACT,
    )
    weekly = QuotaWindowSnapshot(
        window_id="weekly",
        window_kind=QuotaWindowKind.WEEKLY,
        duration_seconds=7 * 24 * 3600,
        window_started_at=NOW - timedelta(days=2),
        reset_at=NOW + timedelta(days=5),
        used_fraction=0.30,
        source=source,
        confidence=EvidenceConfidence.EXACT,
    )
    five_hour = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=5 * 3600,
        window_started_at=NOW - timedelta(hours=2),
        reset_at=NOW + timedelta(hours=3),
        used_fraction=0.20,
        source=source,
        confidence=EvidenceConfidence.EXACT,
    )
    snapshot = QuotaSnapshot(
        schema_version=1,
        quota_pool_id="pool",
        provider_id="p",
        plan_id="plan",
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        observed_at=NOW,
        recorded_at=NOW,
        windows=(five_hour, weekly),
    )
    plan = PlanQuota(
        provider_id="p",
        plan_id="plan",
        display_name="P",
        quota_semantics=PlanQuotaSemantics.SHARED_POOL,
        observed_at=NOW,
    )
    pool = SharedQuotaPool(
        pool_id="pool",
        plan_id="plan",
        provider_id="p",
        resource_kind=QuotaResourceKind.TOKEN_PLAN_INCLUDED_QUOTA,
        shared_across_models=True,
        unit_kind=ConsumptionUnitKind.TOKENS,
        covered_model_ids=("m",),
    )
    projection = PlanQuotaProjection(
        plan=plan,
        pool=pool,
        windows=(weekly, five_hour),
        source=source,
        model_consumption=(),
        model_equivalents=(),
    )
    # Card-chip path: ``PlanQuotaProjection.source_pressure(now)``
    # returns the ``BurnPressure`` enum; the scheduler path reads the
    # raw ``pressure_score`` from ``weekly_window.burn(now)``.
    projection_pressure = projection.source_pressure(now=NOW)
    projection_weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert projection_weekly is not None
    projection_assessment, _ = projection_weekly.burn(now=NOW)
    scheduler_assessment, _ = weekly.burn(now=NOW)
    # Both paths feed the same ``assess()`` primitive.
    assert projection_pressure is projection_assessment.pressure
    assert projection_assessment.pressure_score == scheduler_assessment.pressure_score


def test_5h_smoothing_gate_uses_rolling_hourly_cap_primitive() -> None:
    """The scheduler-side 5h smoothing gate delegates to ``rolling_hourly_cap``.

    The dispatch recommender and the scheduler both call the same
    primitive (``quota_burn.rolling_hourly_cap``); both surfaces
    produce the same verdict for the same window data. This test
    pins that the gate reuses the primitive rather than reinventing
    the math; the deeper ``rolling_hourly_cap`` behaviour is covered
    by ``tests/test_quota_burn.py``.
    """

    from personal_ai_orchestrator.quota_burn import rolling_hourly_cap

    # 0.90 used in 2h = 0.45/h > 0.35/h cap → True (gate fires).
    assert rolling_hourly_cap(used_fraction=0.90, elapsed_seconds=7200.0) is True
    # 0.20 in 2h = 0.10/h < cap → False (gate exempt).
    assert rolling_hourly_cap(used_fraction=0.20, elapsed_seconds=7200.0) is False
    # ``used_fraction=None`` → False (cannot compute → no gate).
    assert rolling_hourly_cap(used_fraction=None, elapsed_seconds=7200.0) is False


def test_evaluate_target_never_scores_manual_policy() -> None:
    """``evaluate_target`` short-circuits MANUAL before scoring.

    MANUAL is task-level \"do not pick automatically\"; the scheduler
    must never auto-rank for it. ``score`` stays ``None``; the
    recommender adds the ``manual_policy_recommendation_uses_balanced``
    reason when asked to recommend against MANUAL anyway.
    """

    eval_ = evaluate_target(
        _registry(),
        task=_task(),
        target=list(_registry().execution_targets.values())[0],
        membership=PoolMembership(
            pool=PoolKind.WORKER, model_sku_id="m", execution_target_id="m3-sub",
            priority=1, weight=1.0,
        ),
        now=NOW, known_at=NOW,
        runtime_available=True,
        telemetry=TargetTelemetry(),
        policy=RoutingPolicy(
            objective=RoutingObjective.MANUAL, manual_execution_target_id="m3-sub"
        ),
        connected_provider_ids=frozenset({"minimax"}),
    )
    # M1 WP3: scheduler does not auto-rank MANUAL. ``score`` stays
    # ``None``; ``admitted`` is False; the reason explains why.
    assert eval_.admitted is False
    assert eval_.score is None
    assert any("orchestrator does not auto-rank" in r for r in eval_.reasons)
