"""Deterministic constraint-first model-resource scheduler.

The scheduler deliberately separates three stages:

1. hard eligibility constraints;
2. quota/task admission feasibility;
3. deterministic ranking of surviving execution targets.

Quota pace is a temporal-expiry signal, not a substitute for absolute task headroom.
This module produces recommendations only; it does not grant repository permissions or
bypass the Safety Kernel / deterministic verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import log1p

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    ExecutionTarget,
    ModelRegistry,
    PlanKind,
    PoolKind,
    PoolMembership,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    RegistryModel,
)
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityEvidence
from personal_ai_orchestrator.quota_observability import (
    DEFAULT_SCARCITY_THRESHOLDS,
    ScarcityClass,
)


class RoutingObjective(StrEnum):
    BALANCED = "BALANCED"
    QUALITY_FIRST = "QUALITY_FIRST"
    QUOTA_SAVER = "QUOTA_SAVER"
    SPEED_FIRST = "SPEED_FIRST"
    MANUAL = "MANUAL"
    MAX_QUALITY = "MAX_QUALITY"
    SAVE_QUOTA = "SAVE_QUOTA"
    LOW_LATENCY = "LOW_LATENCY"
    # M1 WP3: pressure-first objective. The recommender and the
    # scheduler raise the weight on ``pressure_term`` and
    # ``headroom_term`` so the verdict moves towards targets whose
    # quota is about to reset unused (WP6 dispatches on this row).
    BURN_DOWN = "BURN_DOWN"


class RiskClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskProfile(RegistryModel):
    task_id: str = Field(min_length=1)
    pool: PoolKind = PoolKind.WORKER
    task_family: str = "unknown"
    risk: RiskClass = RiskClass.MEDIUM
    required_capabilities: dict[str, float] = Field(default_factory=dict)
    required_context_tokens: int | None = Field(default=None, ge=1)
    requires_vision: bool = False
    required_tools: tuple[str, ...] = ()
    failure_count: int = Field(default=0, ge=0)
    predicted_quota_fraction_p90: float | None = Field(default=None, ge=0.0, le=1.0)
    # M1 WP2: capability tier floor. ``T1`` (workhorse) is the default
    # so existing profiles that never thought about tier still
    # recommend against the same targets they did before. The
    # dispatch recommender reads this field directly — the string
    # value is validated in the recommender against the live
    # :class:`ModelTier` enum.
    min_tier: str = "T1"

    @model_validator(mode="after")
    def validate_requirements(self) -> TaskProfile:
        invalid = {
            key: value
            for key, value in self.required_capabilities.items()
            if not 0.0 <= value <= 1.0
        }
        if invalid:
            raise ValueError(f"capability floors must be within [0, 1]: {invalid}")
        if any(not tool.strip() for tool in self.required_tools):
            raise ValueError("required_tools must not contain blank names")
        if not self.task_family.strip():
            raise ValueError("task_family must not be blank")
        return self


class TargetTelemetry(RegistryModel):
    success_prior: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_latency_ms: float | None = Field(default=None, ge=0.0)
    expected_cost_to_green_usd: float | None = Field(default=None, ge=0.0)
    context_window_tokens: int | None = Field(default=None, ge=1)
    supports_vision: bool | None = None
    supported_tools: tuple[str, ...] = ()


class RoutingPolicy(RegistryModel):
    objective: RoutingObjective = RoutingObjective.BALANCED
    manual_execution_target_id: str | None = None
    max_quota_age_seconds: float = Field(default=600.0, gt=0.0)
    uncertainty_margin_fraction: float = Field(default=0.02, ge=0.0, le=1.0)
    require_burn_estimate_for_subscription: bool = True
    allow_paid_usage: bool = False
    high_risk_requires_success_prior: bool = True
    high_risk_min_success_prior: float = Field(default=0.75, ge=0.0, le=1.0)
    failure_escalation_after: int = Field(default=2, ge=1)
    failure_escalation_capability: str = Field(default="reasoning", min_length=1)
    failure_escalation_floor: float = Field(default=0.75, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_manual_target(self) -> RoutingPolicy:
        if self.objective is RoutingObjective.MANUAL and not self.manual_execution_target_id:
            raise ValueError("MANUAL policy requires manual_execution_target_id")
        return self


class SchedulingPolicyLevel(StrEnum):
    GLOBAL_DEFAULT = "GLOBAL_DEFAULT"
    PROJECT_OVERRIDE = "PROJECT_OVERRIDE"
    TASK_OVERRIDE = "TASK_OVERRIDE"


class SchedulingPolicyResolution(RegistryModel):
    policy: RoutingPolicy
    resolved_level: SchedulingPolicyLevel


class ScoreComponent(RegistryModel):
    name: str
    value: float | str | None = None
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: str = "UNKNOWN"
    weight: float | None = None


class CandidateEvaluation(RegistryModel):
    execution_target_id: str
    model_sku_id: str
    eligible: bool
    admitted: bool
    score: float | None = None
    reasons: tuple[str, ...] = ()
    quota_pool_id: str | None = None
    quota_snapshot_id: str | None = None
    effective_pace: float | None = None
    scarcity_class: ScarcityClass = ScarcityClass.UNKNOWN
    minimum_remaining_fraction: float | None = None
    usable_headroom_fraction: float | None = None
    predicted_burn_fraction: float | None = None
    observed_availability_state: str | None = None
    score_components: tuple[ScoreComponent, ...] = ()


class SchedulerDecision(RegistryModel):
    task_id: str
    policy_id: str
    policy_objective: RoutingObjective
    selected_execution_target_id: str | None
    selected_model_sku_id: str | None
    evaluations: tuple[CandidateEvaluation, ...]
    decision_reason: str


@dataclass(frozen=True)
class ScoreWeights:
    """Per-objective weights shared by ``evaluate_target`` and the
    dispatch recommender.

    Five terms so every signal the orchestrator knows about has a
    named slot, even when its weight is zero (M1 holds ``cost`` at 0
    until the cost surface lands in M3). The total score is always
    ``Σ weight × value`` — see ``_score_candidate`` for the scoring
    contract and the regression test that asserts the identity.
    """

    quality: float
    pressure: float
    headroom: float
    latency: float
    cost: float


def objective_weights(objective: RoutingObjective) -> ScoreWeights:
    """Per-objective weights; ``MANUAL`` is the BALANCED preset by spec.

    The owner-facing ``MANUAL`` is a "do not pick automatically" task-
    level policy. The scheduler must not score ``MANUAL`` tasks
    (``evaluate_target`` returns early with no auto-rank); when the
    recommender is asked to recommend against a ``MANUAL`` policy
    anyway (defence-in-depth), it uses BALANCED's preset and surfaces
    ``manual_policy_recommendation_uses_balanced`` in the reasons so
    the recommendation panel never silently downgrades a manual
    pick to an automatic one.
    """

    if objective in {RoutingObjective.QUALITY_FIRST, RoutingObjective.MAX_QUALITY}:
        return ScoreWeights(quality=1.4, pressure=0.2, headroom=0.3, latency=0.4, cost=0.1)
    if objective in {RoutingObjective.QUOTA_SAVER, RoutingObjective.SAVE_QUOTA}:
        return ScoreWeights(quality=0.5, pressure=0.4, headroom=0.3, latency=0.5, cost=1.0)
    if objective in {RoutingObjective.SPEED_FIRST, RoutingObjective.LOW_LATENCY}:
        return ScoreWeights(quality=0.9, pressure=0.6, headroom=0.4, latency=1.6, cost=0.5)
    if objective is RoutingObjective.BURN_DOWN:
        # Pressure-first: ``STARVED`` should outrank ``ON_TRACK`` for the
        # same provider; ``headroom`` matters because the verifier
        # rejects ``remaining == 0`` already so a tiny remainder is
        # still a useful signal.
        return ScoreWeights(quality=0.4, pressure=1.0, headroom=0.6, latency=0.4, cost=0.2)
    # MANUAL + every other value fall back to BALANCED. Both
    # ``evaluate_target`` and ``recommend_owner_dispatch`` consult this
    # function; ``evaluate_target`` short-circuits MANUAL before
    # calling it, and ``recommend_owner_dispatch`` adds the
    # reason-tag.
    return ScoreWeights(quality=0.7, pressure=0.6, headroom=0.4, latency=1.0, cost=0.3)


def policy_from_name(
    name: str | None,
    *,
    manual_execution_target_id: str | None = None,
    base: RoutingPolicy | None = None,
) -> RoutingPolicy | None:
    """Build a RoutingPolicy from a durable owner-facing policy name.

    Returns ``None`` for ``None``, which callers read as "no override at this level".
    Non-objective policy knobs (quota freshness, risk floors, escalation) are carried
    over from ``base`` so that choosing a scheduling objective never silently relaxes
    an unrelated safety threshold.
    """

    if name is None:
        return None
    objective = RoutingObjective(name)
    template = base or RoutingPolicy()
    return template.model_copy(
        update={
            "objective": objective,
            "manual_execution_target_id": (
                manual_execution_target_id
                if objective is RoutingObjective.MANUAL
                else None
            ),
        }
    )


def resolve_scheduling_policy(
    *,
    global_default: RoutingPolicy,
    project_override: RoutingPolicy | None = None,
    task_override: RoutingPolicy | None = None,
) -> SchedulingPolicyResolution:
    if task_override is not None:
        return SchedulingPolicyResolution(
            policy=task_override,
            resolved_level=SchedulingPolicyLevel.TASK_OVERRIDE,
        )
    if project_override is not None:
        return SchedulingPolicyResolution(
            policy=project_override,
            resolved_level=SchedulingPolicyLevel.PROJECT_OVERRIDE,
        )
    return SchedulingPolicyResolution(
        policy=global_default,
        resolved_level=SchedulingPolicyLevel.GLOBAL_DEFAULT,
    )


def _capability_fit(registry: ModelRegistry, task: TaskProfile, model_sku_id: str) -> float:
    model = registry.models[model_sku_id]
    if not task.required_capabilities:
        return 0.5
    return sum(model.capabilities.scores.get(name, 0.0) for name in task.required_capabilities) / len(
        task.required_capabilities
    )


def _membership_targets(
    registry: ModelRegistry,
    membership: PoolMembership,
) -> tuple[ExecutionTarget, ...]:
    if membership.execution_target_id is not None:
        target = registry.execution_targets[membership.execution_target_id]
        return (target,) if target.enabled else ()
    return registry.execution_targets_for_model(membership.model_sku_id)


def _missing_required_window_kinds(
    *,
    required: tuple[QuotaWindowKind, ...],
    snapshot: QuotaSnapshot,
    at: datetime,
) -> tuple[QuotaWindowKind, ...]:
    if not required:
        return ()
    present = {
        window.window_kind
        for window in snapshot.active_windows(at=at, required_window_kinds=required)
    }
    return tuple(sorted((kind for kind in set(required) if kind not in present), key=str))


def _score_candidate(
    *,
    capability_fit: float,
    priority: int,
    membership_weight: float,
    pace: float | None,
    telemetry: TargetTelemetry,
    objective: RoutingObjective,
) -> float:
    weights = objective_weights(objective)
    quality_weight = weights.quality
    quota_weight = weights.headroom
    latency_weight = weights.latency
    cost_weight = weights.cost
    score = quality_weight * capability_fit * 100.0
    score += membership_weight * 2.0
    score -= max(priority - 1, 0) * 1.5

    if telemetry.success_prior is not None:
        score += quality_weight * telemetry.success_prior * 20.0
    if pace is not None:
        if pace > DEFAULT_SCARCITY_THRESHOLDS.surplus_upper:
            score += quota_weight * 5.0
        elif pace < DEFAULT_SCARCITY_THRESHOLDS.conserve_below:
            score -= quota_weight * 5.0
    if telemetry.expected_latency_ms is not None:
        score -= latency_weight * log1p(telemetry.expected_latency_ms / 1000.0)
    if telemetry.expected_cost_to_green_usd is not None:
        score -= cost_weight * log1p(telemetry.expected_cost_to_green_usd) * 5.0
    return round(score, 8)


def _hard_requirement_reasons(
    registry: ModelRegistry,
    *,
    task: TaskProfile,
    target: ExecutionTarget,
    runtime_available: bool,
    telemetry: TargetTelemetry,
    policy: RoutingPolicy,
) -> list[str]:
    model = registry.models[target.model_sku_id]
    reasons: list[str] = []

    if not model.enabled:
        reasons.append("model disabled")
    if not target.enabled:
        reasons.append("execution target disabled")
    if not target.execution_verified:
        reasons.append("execution target has not been runtime-verified")
    if not runtime_available:
        reasons.append("runtime unavailable")

    for capability, floor in sorted(task.required_capabilities.items()):
        observed = model.capabilities.scores.get(capability, 0.0)
        if observed < floor:
            reasons.append(f"capability floor failed: {capability} {observed:.3f} < {floor:.3f}")

    if task.required_context_tokens is not None:
        if telemetry.context_window_tokens is None:
            reasons.append("context window unknown")
        elif telemetry.context_window_tokens < task.required_context_tokens:
            reasons.append(
                "context window too small: "
                f"{telemetry.context_window_tokens} < {task.required_context_tokens}"
            )

    if task.requires_vision and telemetry.supports_vision is not True:
        reasons.append("required vision capability unavailable or unknown")

    missing_tools = sorted(set(task.required_tools) - set(telemetry.supported_tools))
    if missing_tools:
        reasons.append(f"required runtime tools unavailable: {', '.join(missing_tools)}")

    if task.risk is RiskClass.HIGH and policy.high_risk_requires_success_prior:
        if telemetry.success_prior is None:
            reasons.append("high-risk task requires an observed success prior")
        elif telemetry.success_prior < policy.high_risk_min_success_prior:
            reasons.append(
                "high-risk success prior below floor: "
                f"{telemetry.success_prior:.3f} < {policy.high_risk_min_success_prior:.3f}"
            )

    if task.failure_count >= policy.failure_escalation_after:
        capability = policy.failure_escalation_capability
        observed = model.capabilities.scores.get(capability, 0.0)
        if observed < policy.failure_escalation_floor:
            reasons.append(
                "failure escalation floor failed: "
                f"{capability} {observed:.3f} < {policy.failure_escalation_floor:.3f}"
            )

    return reasons


def evaluate_target(
    registry: ModelRegistry,
    *,
    task: TaskProfile,
    target: ExecutionTarget,
    membership: PoolMembership,
    now: datetime,
    known_at: datetime,
    runtime_available: bool,
    telemetry: TargetTelemetry,
    policy: RoutingPolicy,
    observed_availability: QuotaAvailabilityEvidence | None = None,
    connected_provider_ids: frozenset[str] | None = None,
) -> CandidateEvaluation:
    model = registry.models[target.model_sku_id]
    if connected_provider_ids is not None and model.provider_id not in connected_provider_ids:
        return CandidateEvaluation(
            execution_target_id=target.id,
            model_sku_id=model.id,
            eligible=False,
            admitted=False,
            reasons=("provider not connected by owner",),
        )
    if (
        policy.objective is RoutingObjective.MANUAL
        and policy.manual_execution_target_id != target.id
    ):
        return CandidateEvaluation(
            execution_target_id=target.id,
            model_sku_id=model.id,
            eligible=False,
            admitted=False,
            reasons=("manual policy selected another execution target",),
        )
    reasons = _hard_requirement_reasons(
        registry,
        task=task,
        target=target,
        runtime_available=runtime_available,
        telemetry=telemetry,
        policy=policy,
    )

    if reasons:
        return CandidateEvaluation(
            execution_target_id=target.id,
            model_sku_id=model.id,
            eligible=False,
            admitted=False,
            reasons=tuple(reasons),
        )

    try:
        binding = registry.active_quota_binding(
            model.id,
            effective_at=now,
            known_at=known_at,
            execution_target_id=target.id,
        )
    except LookupError as exc:
        return CandidateEvaluation(
            execution_target_id=target.id,
            model_sku_id=model.id,
            eligible=False,
            admitted=False,
            reasons=(f"quota binding unavailable: {exc}",),
        )

    pool = registry.quota_pools[binding.quota_pool_id]
    plan = registry.plans[pool.plan_id]
    snapshot = pool.snapshot
    snapshot_id = snapshot.id

    if plan.kind is PlanKind.UNKNOWN:
        reasons.append("plan kind unknown; routing requires explicit commercial semantics")
    if snapshot.is_stale(as_of=now, max_age_seconds=policy.max_quota_age_seconds):
        reasons.append("quota snapshot stale")
    if snapshot.state is QuotaState.EXHAUSTED:
        reasons.append("quota exhausted")
    if observed_availability is not None:
        observed_state = observed_availability.state_at(now=now)
        if observed_availability.blocks_quota_billable_launch(now=now):
            reasons.append(f"observed quota availability blocks target: {observed_state.value}")
    if plan.kind is PlanKind.PAY_AS_YOU_GO and not policy.allow_paid_usage:
        reasons.append("paid usage requires explicit policy")

    missing_required = _missing_required_window_kinds(
        required=pool.required_window_kinds,
        snapshot=snapshot,
        at=now,
    )
    if missing_required:
        missing_labels = ", ".join(kind.value for kind in missing_required)
        reasons.append(f"required quota windows missing or inactive: {missing_labels}")

    pace = snapshot.effective_pace(
        at=now,
        required_window_kinds=pool.required_window_kinds,
    )
    remaining = snapshot.minimum_remaining_fraction(
        at=now,
        required_window_kinds=pool.required_window_kinds,
    )
    if missing_required:
        pace = None
        remaining = None

    is_metered_subscription = plan.kind in {
        PlanKind.SUBSCRIPTION,
        PlanKind.PREPAID,
    }
    if is_metered_subscription:
        if snapshot.confidence is EvidenceConfidence.UNKNOWN:
            reasons.append("quota confidence unknown")
        if pace is None:
            reasons.append("one or more binding quota windows have unknown pace")
        if remaining is None:
            reasons.append("one or more binding quota windows have unknown remaining quota")
        if policy.require_burn_estimate_for_subscription and task.predicted_quota_fraction_p90 is None:
            reasons.append("task quota burn estimate unavailable")

    rule = None
    try:
        rule = registry.active_consumption_rule(
            model.id,
            effective_at=now,
            known_at=known_at,
            execution_target_id=target.id,
        )
    except LookupError as exc:
        reasons.append(f"consumption rule ambiguous: {exc}")

    multiplier = rule.multiplier if rule is not None else 1.0
    predicted = (
        None
        if task.predicted_quota_fraction_p90 is None
        else min(1.0, task.predicted_quota_fraction_p90 * multiplier)
    )
    usable = (
        None
        if remaining is None
        else max(0.0, remaining - pool.reserve_fraction - policy.uncertainty_margin_fraction)
    )
    if is_metered_subscription and predicted is not None and usable is not None and predicted > usable:
        reasons.append(f"task burn {predicted:.3f} exceeds usable quota headroom {usable:.3f}")

    scarcity = DEFAULT_SCARCITY_THRESHOLDS.classify(pace)
    if reasons:
        return CandidateEvaluation(
            execution_target_id=target.id,
            model_sku_id=model.id,
            eligible=True,
            admitted=False,
            reasons=tuple(reasons),
            quota_pool_id=pool.id,
            quota_snapshot_id=snapshot_id,
            effective_pace=pace,
            scarcity_class=scarcity,
            minimum_remaining_fraction=remaining,
            usable_headroom_fraction=usable,
            predicted_burn_fraction=predicted,
            observed_availability_state=observed_availability.state_at(now=now).value
            if observed_availability is not None
            else None,
        )

    capability_fit = _capability_fit(registry, task, model.id)
    score = _score_candidate(
        capability_fit=capability_fit,
        priority=membership.priority,
        membership_weight=membership.weight,
        pace=pace,
        telemetry=telemetry,
        objective=policy.objective,
    )
    weights = objective_weights(policy.objective)
    quality_weight = weights.quality
    quota_weight = weights.headroom
    latency_weight = weights.latency
    cost_weight = weights.cost
    score_components = (
        ScoreComponent(
            name="task_capability_fit",
            value=round(capability_fit, 6),
            confidence=EvidenceConfidence.ESTIMATED,
            source="model_registry_capability_profile",
            weight=quality_weight,
        ),
        ScoreComponent(
            name="quota_pace",
            value=round(pace, 6) if pace is not None else "UNKNOWN",
            confidence=snapshot.confidence,
            source=snapshot.source.source_type.value,
            weight=quota_weight,
        ),
        ScoreComponent(
            name="latency",
            value=round(telemetry.expected_latency_ms, 3)
            if telemetry.expected_latency_ms is not None
            else "UNKNOWN",
            confidence=(
                EvidenceConfidence.ESTIMATED
                if telemetry.expected_latency_ms is not None
                else EvidenceConfidence.UNKNOWN
            ),
            source="target_telemetry",
            weight=latency_weight,
        ),
        ScoreComponent(
            name="cost_to_green",
            value=round(telemetry.expected_cost_to_green_usd, 6)
            if telemetry.expected_cost_to_green_usd is not None
            else "UNKNOWN",
            confidence=(
                EvidenceConfidence.ESTIMATED
                if telemetry.expected_cost_to_green_usd is not None
                else EvidenceConfidence.UNKNOWN
            ),
            source="target_telemetry",
            weight=cost_weight,
        ),
    )
    reasons.extend(
        [
            "hard eligibility gates passed",
            "task burn fits usable quota headroom" if is_metered_subscription else "quota admission passed",
            f"capability fit {capability_fit:.3f}",
            f"scarcity {scarcity.value}",
        ]
    )
    if task.failure_count >= policy.failure_escalation_after:
        reasons.append(f"failure escalation active after {task.failure_count} prior failures")
    if task.risk is RiskClass.HIGH:
        reasons.append("high-risk reliability gate passed")

    return CandidateEvaluation(
        execution_target_id=target.id,
        model_sku_id=model.id,
        eligible=True,
        admitted=True,
        score=score,
        reasons=tuple(reasons),
        quota_pool_id=pool.id,
        quota_snapshot_id=snapshot_id,
        effective_pace=pace,
        scarcity_class=scarcity,
        minimum_remaining_fraction=remaining,
        usable_headroom_fraction=usable,
        predicted_burn_fraction=predicted,
        observed_availability_state=observed_availability.state_at(now=now).value
        if observed_availability is not None
        else None,
        score_components=score_components,
    )


def route_task(
    registry: ModelRegistry,
    *,
    task: TaskProfile,
    now: datetime,
    known_at: datetime,
    runtime_availability: dict[str, bool],
    telemetry: dict[str, TargetTelemetry] | None = None,
    observed_availability: dict[str, QuotaAvailabilityEvidence] | None = None,
    policy: RoutingPolicy | None = None,
    connected_provider_ids: frozenset[str] | None = None,
) -> SchedulerDecision:
    """Return one deterministic recommendation without applying any runtime switch."""

    telemetry = telemetry or {}
    observed_availability = observed_availability or {}
    policy = policy or RoutingPolicy()
    evaluations_by_target: dict[str, CandidateEvaluation] = {}
    membership_by_target: dict[str, PoolMembership] = {}

    for membership in registry.candidates_for_pool(task.pool):
        for target in _membership_targets(registry, membership):
            prior_membership = membership_by_target.get(target.id)
            if prior_membership is not None and (
                prior_membership.priority,
                -prior_membership.weight,
            ) <= (membership.priority, -membership.weight):
                continue
            membership_by_target[target.id] = membership
            evaluations_by_target[target.id] = evaluate_target(
                registry,
                task=task,
                target=target,
                membership=membership,
                now=now,
                known_at=known_at,
                runtime_available=runtime_availability.get(target.id, False),
                telemetry=telemetry.get(target.id, TargetTelemetry()),
                observed_availability=observed_availability.get(target.id),
                policy=policy,
                connected_provider_ids=connected_provider_ids,
            )

    evaluations = tuple(
        evaluations_by_target[target_id] for target_id in sorted(evaluations_by_target)
    )
    admitted = [
        evaluation
        for evaluation in evaluations
        if evaluation.admitted and evaluation.score is not None
    ]
    if not admitted:
        return SchedulerDecision(
            task_id=task.task_id,
            policy_id=policy.objective.value,
            policy_objective=policy.objective,
            selected_execution_target_id=None,
            selected_model_sku_id=None,
            evaluations=evaluations,
            decision_reason="no candidate passed hard eligibility and quota admission gates",
        )

    selected = min(
        admitted,
        key=lambda item: (
            -float(item.score),
            membership_by_target[item.execution_target_id].priority,
            item.execution_target_id,
        ),
    )
    return SchedulerDecision(
        task_id=task.task_id,
        policy_id=policy.objective.value,
        policy_objective=policy.objective,
        selected_execution_target_id=selected.execution_target_id,
        selected_model_sku_id=selected.model_sku_id,
        evaluations=evaluations,
        decision_reason=(
            f"selected {selected.execution_target_id} after hard eligibility, quota admission, "
            "and deterministic ranking"
        ),
    )


__all__ = [
    "CandidateEvaluation",
    "RiskClass",
    "RoutingObjective",
    "RoutingPolicy",
    "ScoreComponent",
    "ScoreWeights",
    "SchedulingPolicyLevel",
    "policy_from_name",
    "SchedulingPolicyResolution",
    "SchedulerDecision",
    "TargetTelemetry",
    "TaskProfile",
    "evaluate_target",
    "objective_weights",
    "resolve_scheduling_policy",
    "route_task",
]
