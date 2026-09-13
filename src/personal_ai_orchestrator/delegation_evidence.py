"""Canonical host evidence and append-only outcomes for PI-5B3C.

The objects in this module are observational only. They never select a target,
admit quota, launch a worker, or advance task truth. Missing evidence stays
explicitly missing so later enforcement cannot be justified by guessed values.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import (
    PlanKind,
    QuotaWindowKind,
    RegistryModel,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.runtime_quota_routing import quota_pool_id_for_target
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskRecord
from personal_ai_orchestrator.scheduler import (
    RiskClass,
    RoutingPolicy,
    SchedulingPolicyLevel,
    SchedulingPolicyResolution,
    TaskProfile,
    policy_from_name,
    resolve_scheduling_policy,
)


class DelegationCommercialMode(StrEnum):
    SUBSCRIPTION = "SUBSCRIPTION"
    PREPAID = "PREPAID"
    PAY_AS_YOU_GO = "PAY_AS_YOU_GO"
    UNMETERED = "UNMETERED"
    UNKNOWN = "UNKNOWN"


class DelegationOutcomePhase(StrEnum):
    CHILD_FINAL = "CHILD_FINAL"
    PARENT_FINAL = "PARENT_FINAL"


class DelegationHostEvidence(RegistryModel):
    """Structured host facts consumed by a delegation SHADOW decision."""

    schema_version: int = Field(default=1, ge=1)
    parent_profile_present: bool = False
    parent_risk: RiskClass | None = None
    parent_failure_count: int | None = Field(default=None, ge=0)
    child_profile_present: bool = False
    child_predicted_quota_fraction_p90: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    predicted_child_burn_fraction: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    resolved_policy: RoutingPolicy | None = None
    resolved_policy_level: SchedulingPolicyLevel | None = None
    host_required_delegation: bool | None = None
    independence_required: bool | None = None
    child_commercial_mode: DelegationCommercialMode = (
        DelegationCommercialMode.UNKNOWN
    )
    paid_usage_required: bool | None = None
    parent_quota_pool_id: str | None = None
    child_quota_pool_id: str | None = None
    same_quota_pool_as_parent: bool | None = None
    raw_child_headroom_fraction: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    usable_child_headroom_fraction: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    evidence_sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    enforcement_ready: bool = False

    @model_validator(mode="after")
    def validate_ready(self) -> DelegationHostEvidence:
        if self.enforcement_ready and self.limitations:
            raise ValueError("limited delegation host evidence cannot be enforcement-ready")
        return self


class DelegationOutcomeRecord(RegistryModel):
    """One immutable outcome phase linked to an original SHADOW observation."""

    schema_version: int = Field(default=1, ge=1)
    outcome_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    phase: DelegationOutcomePhase
    observed_at: datetime
    parent_task_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    child_task_id: str = Field(min_length=1)
    child_execution_target_id: str | None = None
    child_quota_pool_id: str | None = None
    child_state: str | None = None
    child_verified: bool | None = None
    parent_state: str | None = None
    parent_verified: bool | None = None
    child_verifier_passed: bool | None = None
    parent_verifier_passed: bool | None = None
    child_latency_seconds: float | None = Field(default=None, ge=0.0)
    parent_latency_seconds: float | None = Field(default=None, ge=0.0)
    child_run_count: int | None = Field(default=None, ge=0)
    parent_run_count: int | None = Field(default=None, ge=0)
    child_attempts_to_green: int | None = Field(default=None, ge=1)
    parent_attempts_to_green: int | None = Field(default=None, ge=1)
    quota_before_snapshot_id: str | None = None
    quota_after_snapshot_id: str | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_phase(self) -> DelegationOutcomeRecord:
        if self.child_verified is True and self.child_state != "VERIFIED":
            raise ValueError("child verified outcome requires child_state=VERIFIED")
        if self.parent_verified is True and self.parent_state != "VERIFIED":
            raise ValueError("parent verified outcome requires parent_state=VERIFIED")
        if self.child_verifier_passed is True and self.child_verified is not True:
            raise ValueError("child verifier PASS requires verified child truth")
        if self.parent_verifier_passed is True and self.parent_verified is not True:
            raise ValueError("parent verifier PASS requires verified parent truth")
        return self


class DelegationOutcomeJournal:
    """Append-only one-record-per-observation-phase outcome journal."""

    def __init__(self, root: str | Path) -> None:
        self.directory = Path(root) / "delegation-outcome-history"

    @staticmethod
    def outcome_id(
        *,
        observation_id: str,
        phase: DelegationOutcomePhase,
    ) -> str:
        rendered = json.dumps(
            {"observation_id": observation_id, "phase": phase.value},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"delegation-outcome-{sha256(rendered).hexdigest()[:24]}"

    def path_for(self, outcome_id: str) -> Path:
        if not outcome_id or any(part in outcome_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe delegation outcome id")
        return self.directory / f"{outcome_id}.json"

    def append(self, record: DelegationOutcomeRecord) -> Path:
        assert_sanitized(record.model_dump(mode="json"))
        target = self.path_for(record.outcome_id)
        rendered = record.model_dump_json(indent=2) + "\n"
        if target.exists():
            existing = DelegationOutcomeRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            if (
                existing.observation_id != record.observation_id
                or existing.phase is not record.phase
            ):
                raise ValueError("delegation outcome id belongs to another identity")
            return target

        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=self.directory,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(
        self,
        *,
        observation_id: str,
        phase: DelegationOutcomePhase,
    ) -> DelegationOutcomeRecord | None:
        outcome_id = self.outcome_id(observation_id=observation_id, phase=phase)
        path = self.path_for(outcome_id)
        if not path.exists():
            return None
        return DelegationOutcomeRecord.model_validate_json(
            path.read_text(encoding="utf-8")
        )


def resolve_delegation_policy(
    *,
    store: SafetyKernelStore,
    task: TaskRecord,
    global_policy: RoutingPolicy,
    project_policy_overrides: dict[str, RoutingPolicy] | None = None,
    task_policy_overrides: dict[str, RoutingPolicy] | None = None,
    scheduling_settings: Any = None,
) -> SchedulingPolicyResolution:
    """Resolve task > project > global with the product routing semantics."""

    project_policy_overrides = project_policy_overrides or {}
    task_policy_overrides = task_policy_overrides or {}

    global_default = global_policy
    if scheduling_settings is not None:
        durable_global = policy_from_name(
            scheduling_settings.default_policy,
            base=global_policy,
        )
        if durable_global is not None:
            global_default = durable_global

    task_policy = task_policy_overrides.get(task.task_id)
    if task_policy is None:
        task_policy = policy_from_name(
            task.scheduling_policy,
            manual_execution_target_id=task.manual_execution_target_id,
            base=global_policy,
        )

    project_policy = None
    if task.project_id is not None:
        project_policy = project_policy_overrides.get(task.project_id)
        if project_policy is None:
            try:
                project = store.get_project(task.project_id)
            except KeyError:
                project = None
            if project is not None:
                project_policy = policy_from_name(
                    project.scheduling_policy,
                    manual_execution_target_id=project.manual_execution_target_id,
                    base=global_policy,
                )

    return resolve_scheduling_policy(
        global_default=global_default,
        project_override=project_policy,
        task_override=task_policy,
    )


def commercial_mode_for_target(
    registry: Any,
    *,
    execution_target_id: str | None,
    observed_availability_state: str | None,
    now: datetime,
) -> tuple[DelegationCommercialMode, str | None]:
    """Resolve commercial semantics from canonical target -> pool -> plan truth."""

    if execution_target_id is None:
        return DelegationCommercialMode.UNKNOWN, None
    if observed_availability_state == "AVAILABLE_UNMETERED":
        pool_id = quota_pool_id_for_target(
            registry,
            execution_target_id=execution_target_id,
            now=now,
        )
        return DelegationCommercialMode.UNMETERED, pool_id

    pool_id = quota_pool_id_for_target(
        registry,
        execution_target_id=execution_target_id,
        now=now,
    )
    if pool_id is None:
        return DelegationCommercialMode.UNKNOWN, None
    pool = registry.quota_pools.get(pool_id)
    if pool is None:
        return DelegationCommercialMode.UNKNOWN, pool_id

    active_windows = pool.snapshot.active_windows(at=now)
    if any(window.window_kind is QuotaWindowKind.UNMETERED for window in active_windows):
        return DelegationCommercialMode.UNMETERED, pool_id

    plan = registry.plans.get(pool.plan_id)
    if plan is None:
        return DelegationCommercialMode.UNKNOWN, pool_id
    mapping = {
        PlanKind.SUBSCRIPTION: DelegationCommercialMode.SUBSCRIPTION,
        PlanKind.PREPAID: DelegationCommercialMode.PREPAID,
        PlanKind.PAY_AS_YOU_GO: DelegationCommercialMode.PAY_AS_YOU_GO,
        PlanKind.UNKNOWN: DelegationCommercialMode.UNKNOWN,
    }
    return mapping[plan.kind], pool_id


def predicted_child_burn(
    registry: Any,
    *,
    child_profile: TaskProfile | None,
    execution_target_id: str | None,
    now: datetime,
) -> tuple[float | None, str | None]:
    """Return target-adjusted child burn only when both profile and rule exist."""

    if (
        child_profile is None
        or child_profile.predicted_quota_fraction_p90 is None
        or execution_target_id is None
    ):
        return None, None
    target = registry.execution_targets.get(execution_target_id)
    if target is None:
        return None, "child_execution_target_unresolved"
    try:
        rule = registry.active_consumption_rule(
            target.model_sku_id,
            effective_at=now,
            known_at=now,
            execution_target_id=execution_target_id,
        )
    except LookupError:
        return None, "child_consumption_rule_unresolved"
    return (
        min(1.0, child_profile.predicted_quota_fraction_p90 * rule.multiplier),
        None,
    )


def build_delegation_host_evidence(
    *,
    registry: Any,
    parent_profile: TaskProfile | None,
    child_profile: TaskProfile | None,
    policy_resolution: SchedulingPolicyResolution | None,
    parent_execution_target_id: str | None,
    child_execution_target_id: str | None,
    child_remaining_fractions: tuple[float, ...],
    child_observed_availability_state: str | None,
    now: datetime,
    host_required_delegation: bool | None = None,
    independence_required: bool | None = None,
) -> DelegationHostEvidence:
    """Build canonical structured evidence without provider/model heuristics."""

    sources: list[str] = ["runtime_quota_routing.quota_pool_id_for_target"]
    limitations: list[str] = []

    if parent_profile is None:
        limitations.append(
            "task_profile_failure_count_not_available_in_child_dispatch_context"
        )
        parent_risk = None
        parent_failure_count = None
    else:
        parent_risk = parent_profile.risk
        parent_failure_count = parent_profile.failure_count
        sources.append("runtime_config.task_profiles[parent]")

    child_estimate = (
        None if child_profile is None else child_profile.predicted_quota_fraction_p90
    )
    if child_profile is not None:
        sources.append("runtime_config.task_profiles[child]")

    if policy_resolution is None:
        resolved_policy = None
        resolved_level = None
        limitations.append("resolved_routing_policy_not_exposed_by_child_dispatch_context")
    else:
        resolved_policy = policy_resolution.policy
        resolved_level = policy_resolution.resolved_level
        sources.append("host_scheduling_policy_resolution")

    parent_pool = (
        quota_pool_id_for_target(
            registry,
            execution_target_id=parent_execution_target_id,
            now=now,
        )
        if parent_execution_target_id is not None
        else None
    )
    mode, child_pool = commercial_mode_for_target(
        registry,
        execution_target_id=child_execution_target_id,
        observed_availability_state=child_observed_availability_state,
        now=now,
    )
    same_pool = (
        parent_pool == child_pool
        if parent_pool is not None and child_pool is not None
        else None
    )
    if parent_pool is None:
        limitations.append("parent_quota_pool_unresolved")
    if child_execution_target_id is not None and child_pool is None:
        limitations.append("child_quota_pool_unresolved")

    if mode is DelegationCommercialMode.UNKNOWN:
        paid_required = None
        limitations.append("child_commercial_semantics_unknown")
    else:
        paid_required = mode is DelegationCommercialMode.PAY_AS_YOU_GO

    predicted, predicted_limitation = predicted_child_burn(
        registry,
        child_profile=child_profile,
        execution_target_id=child_execution_target_id,
        now=now,
    )
    if predicted_limitation is not None:
        limitations.append(predicted_limitation)

    raw_headroom = min(child_remaining_fractions) if child_remaining_fractions else None
    usable_headroom = raw_headroom
    if raw_headroom is not None and child_pool is not None and resolved_policy is not None:
        pool = registry.quota_pools.get(child_pool)
        if pool is not None:
            usable_headroom = max(
                0.0,
                raw_headroom
                - pool.reserve_fraction
                - resolved_policy.uncertainty_margin_fraction,
            )
            sources.append("quota_pool.reserve_fraction")
            sources.append("routing_policy.uncertainty_margin_fraction")

    if host_required_delegation is None:
        limitations.append("host_required_delegation_signal_not_exposed")
    if independence_required is None:
        limitations.append("independence_requirement_signal_not_exposed")

    if (
        resolved_policy is not None
        and resolved_policy.require_burn_estimate_for_subscription
        and mode in {
            DelegationCommercialMode.SUBSCRIPTION,
            DelegationCommercialMode.PREPAID,
        }
        and predicted is None
    ):
        limitations.append("predicted_child_burn_unavailable")

    return DelegationHostEvidence(
        parent_profile_present=parent_profile is not None,
        parent_risk=parent_risk,
        parent_failure_count=parent_failure_count,
        child_profile_present=child_profile is not None,
        child_predicted_quota_fraction_p90=child_estimate,
        predicted_child_burn_fraction=predicted,
        resolved_policy=resolved_policy,
        resolved_policy_level=resolved_level,
        host_required_delegation=host_required_delegation,
        independence_required=independence_required,
        child_commercial_mode=mode,
        paid_usage_required=paid_required,
        parent_quota_pool_id=parent_pool,
        child_quota_pool_id=child_pool,
        same_quota_pool_as_parent=same_pool,
        raw_child_headroom_fraction=raw_headroom,
        usable_child_headroom_fraction=usable_headroom,
        evidence_sources=tuple(dict.fromkeys(sources)),
        limitations=tuple(dict.fromkeys(limitations)),
        enforcement_ready=not limitations,
    )


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _bounded_latency(started_at: str | None, finished_at: str | None) -> float | None:
    start = _parse_time(started_at)
    finish = _parse_time(finished_at)
    if start is None or finish is None:
        return None
    seconds = (finish - start).total_seconds()
    if seconds < 0 or seconds > 7 * 24 * 60 * 60:
        return None
    return seconds


def _task_runs(store: SafetyKernelStore, task_id: str) -> tuple[Any, ...]:
    return tuple(
        store.connection.execute(
            "SELECT run_id,status,started_at,finished_at FROM runs "
            "WHERE task_id=? ORDER BY started_at,run_id",
            (task_id,),
        ).fetchall()
    )


def build_delegation_outcome_record(
    *,
    store: SafetyKernelStore,
    registry: Any,
    shadow_record: Any,
    phase: DelegationOutcomePhase,
    observed_at: datetime | None = None,
) -> DelegationOutcomeRecord:
    """Build one immutable outcome phase strictly from durable host state."""

    now = observed_at or datetime.now(UTC)
    limitations: list[str] = ["comparable_quota_before_after_not_captured"]
    child = store.get_task(shadow_record.child_task_id)
    parent = store.get_task(shadow_record.parent_task_id)

    child_dispatch = None
    child_request_id = f"pi5-child-dispatch-{shadow_record.child_task_id}"
    try:
        child_dispatch = store.get_owner_dispatch_by_request_id(child_request_id)
    except KeyError:
        limitations.append("child_dispatch_not_available")

    child_target = (
        None if child_dispatch is None else child_dispatch.execution_target_id
    )
    child_pool = (
        quota_pool_id_for_target(
            registry,
            execution_target_id=child_target,
            now=now,
        )
        if child_target is not None
        else None
    )

    child_runs = _task_runs(store, child.task_id)
    parent_runs = _task_runs(store, parent.task_id)
    child_last = child_runs[-1] if child_runs else None
    parent_exact = store.connection.execute(
        "SELECT run_id,status,started_at,finished_at FROM runs WHERE run_id=?",
        (shadow_record.parent_run_id,),
    ).fetchone()

    child_verified = child.state.value == "VERIFIED"
    parent_verified = parent.state.value == "VERIFIED"
    child_latency = (
        None
        if child_last is None
        else _bounded_latency(child_last["started_at"], child_last["finished_at"])
    )
    parent_latency = (
        None
        if parent_exact is None
        else _bounded_latency(parent_exact["started_at"], parent_exact["finished_at"])
    )
    if child_last is not None and child_latency is None:
        limitations.append("child_latency_not_reliably_bounded")
    if parent_exact is not None and parent_latency is None:
        limitations.append("parent_latency_not_reliably_bounded")

    child_attempts = len(child_runs) if child_verified and child_runs else None
    parent_attempts = len(parent_runs) if parent_verified and parent_runs else None

    if not child_verified:
        limitations.append("child_verifier_pass_not_proven")
    if phase is DelegationOutcomePhase.PARENT_FINAL and not parent_verified:
        limitations.append("parent_verifier_pass_not_proven")

    outcome_id = DelegationOutcomeJournal.outcome_id(
        observation_id=shadow_record.observation_id,
        phase=phase,
    )
    return DelegationOutcomeRecord(
        outcome_id=outcome_id,
        observation_id=shadow_record.observation_id,
        phase=phase,
        observed_at=now,
        parent_task_id=shadow_record.parent_task_id,
        parent_run_id=shadow_record.parent_run_id,
        child_task_id=shadow_record.child_task_id,
        child_execution_target_id=child_target,
        child_quota_pool_id=child_pool,
        child_state=child.state.value,
        child_verified=child_verified,
        parent_state=parent.state.value,
        parent_verified=parent_verified,
        child_verifier_passed=True if child_verified else None,
        parent_verifier_passed=True if parent_verified else None,
        child_latency_seconds=child_latency,
        parent_latency_seconds=parent_latency,
        child_run_count=len(child_runs),
        parent_run_count=len(parent_runs),
        child_attempts_to_green=child_attempts,
        parent_attempts_to_green=parent_attempts,
        limitations=tuple(dict.fromkeys(limitations)),
    )


__all__ = [
    "DelegationCommercialMode",
    "DelegationHostEvidence",
    "DelegationOutcomeJournal",
    "DelegationOutcomePhase",
    "DelegationOutcomeRecord",
    "build_delegation_host_evidence",
    "build_delegation_outcome_record",
    "commercial_mode_for_target",
    "predicted_child_burn",
    "resolve_delegation_policy",
]
