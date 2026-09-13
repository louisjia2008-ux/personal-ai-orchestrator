"""PI-5B3D: deterministic, read-only delegation readiness evaluation."""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import Field

from personal_ai_orchestrator.delegation_evidence import (
    DelegationOutcomePhase,
    DelegationOutcomeRecord,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowRecord
from personal_ai_orchestrator.model_registry import RegistryModel


class DelegationReadinessStatus(StrEnum):
    NOT_READY = "NOT_READY"
    READY_FOR_LIMITED_EXPERIMENT = "READY_FOR_LIMITED_EXPERIMENT"


class DelegationReadinessReason(StrEnum):
    POLICY_REQUIRED = "POLICY_REQUIRED"
    NO_SHADOW_EVIDENCE = "NO_SHADOW_EVIDENCE"
    SHADOW_REPLAY_MISMATCH = "SHADOW_REPLAY_MISMATCH"
    SHADOW_NOT_ENFORCEMENT_READY = "SHADOW_NOT_ENFORCEMENT_READY"
    MISSING_CHILD_OUTCOME = "MISSING_CHILD_OUTCOME"
    MISSING_PARENT_OUTCOME = "MISSING_PARENT_OUTCOME"
    OUTCOME_IDENTITY_MISMATCH = "OUTCOME_IDENTITY_MISMATCH"
    CHILD_NOT_VERIFIED = "CHILD_NOT_VERIFIED"
    PARENT_NOT_VERIFIED = "PARENT_NOT_VERIFIED"
    CHILD_VERIFIER_NOT_PASSED = "CHILD_VERIFIER_NOT_PASSED"
    PARENT_VERIFIER_NOT_PASSED = "PARENT_VERIFIER_NOT_PASSED"
    QUOTA_COMPARABILITY_MISSING = "QUOTA_COMPARABILITY_MISSING"
    INSUFFICIENT_COMPLETE_SAMPLES = "INSUFFICIENT_COMPLETE_SAMPLES"
    SAME_POOL_COVERAGE_MISSING = "SAME_POOL_COVERAGE_MISSING"
    DIFFERENT_POOL_COVERAGE_MISSING = "DIFFERENT_POOL_COVERAGE_MISSING"


class DelegationReadinessAdvisory(StrEnum):
    NO_QUOTA_COMPARABLE_SAMPLES = "NO_QUOTA_COMPARABLE_SAMPLES"
    SAME_POOL_NOT_OBSERVED = "SAME_POOL_NOT_OBSERVED"
    DIFFERENT_POOL_NOT_OBSERVED = "DIFFERENT_POOL_NOT_OBSERVED"
    UNKNOWN_POOL_RELATION_OBSERVED = "UNKNOWN_POOL_RELATION_OBSERVED"


class DelegationReadinessPolicy(RegistryModel):
    """Explicit host policy; there is intentionally no sample-count default."""

    min_complete_samples: int = Field(ge=1)
    require_quota_comparability: bool = True
    require_same_pool_sample: bool = False
    require_different_pool_sample: bool = False


class DelegationReadinessSample(RegistryModel):
    observation_id: str = Field(min_length=1)
    replay_matches: bool
    shadow_enforcement_ready: bool
    child_outcome_present: bool
    parent_outcome_present: bool
    identity_consistent: bool
    child_verified: bool
    parent_verified: bool
    child_verifier_passed: bool
    parent_verifier_passed: bool
    quota_comparable: bool
    same_quota_pool_as_parent: bool | None = None
    complete: bool
    reasons: tuple[DelegationReadinessReason, ...] = ()


class DelegationReadinessReport(RegistryModel):
    schema_version: int = Field(default=1, ge=1)
    status: DelegationReadinessStatus
    input_digest: str = Field(min_length=1)
    policy_supplied: bool
    min_complete_samples: int | None = Field(default=None, ge=1)
    total_shadow_observations: int = Field(ge=0)
    replayable_shadow_count: int = Field(ge=0)
    enforcement_ready_shadow_count: int = Field(ge=0)
    matched_child_outcome_count: int = Field(ge=0)
    matched_parent_outcome_count: int = Field(ge=0)
    complete_sample_count: int = Field(ge=0)
    child_verified_count: int = Field(ge=0)
    parent_verified_count: int = Field(ge=0)
    quota_comparable_count: int = Field(ge=0)
    same_pool_count: int = Field(ge=0)
    different_pool_count: int = Field(ge=0)
    unknown_pool_count: int = Field(ge=0)
    blocking_reasons: tuple[DelegationReadinessReason, ...] = ()
    advisories: tuple[DelegationReadinessAdvisory, ...] = ()
    samples: tuple[DelegationReadinessSample, ...] = ()


def readiness_input_digest(
    shadows: tuple[DelegationShadowRecord, ...],
    outcomes: tuple[DelegationOutcomeRecord, ...],
    policy: DelegationReadinessPolicy | None,
) -> str:
    payload = {
        "policy": None if policy is None else policy.model_dump(mode="json"),
        "shadows": [
            item.model_dump(mode="json")
            for item in sorted(shadows, key=lambda item: item.observation_id)
        ],
        "outcomes": [
            item.model_dump(mode="json")
            for item in sorted(outcomes, key=lambda item: (item.observation_id, item.phase.value))
        ],
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"delegation-readiness-{sha256(rendered).hexdigest()}"


def _pool_relation(shadow: DelegationShadowRecord) -> bool | None:
    if shadow.host_evidence is not None:
        return shadow.host_evidence.same_quota_pool_as_parent
    if shadow.parent_quota_pool_id is None or shadow.child_quota_pool_id is None:
        return None
    return shadow.parent_quota_pool_id == shadow.child_quota_pool_id


def _identity_matches(
    shadow: DelegationShadowRecord,
    child: DelegationOutcomeRecord | None,
    parent: DelegationOutcomeRecord | None,
) -> bool:
    for outcome, expected_phase in (
        (child, DelegationOutcomePhase.CHILD_FINAL),
        (parent, DelegationOutcomePhase.PARENT_FINAL),
    ):
        if outcome is None:
            continue
        if outcome.phase is not expected_phase:
            return False
        if outcome.observation_id != shadow.observation_id:
            return False
        if outcome.parent_task_id != shadow.parent_task_id:
            return False
        if outcome.parent_run_id != shadow.parent_run_id:
            return False
        if outcome.child_task_id != shadow.child_task_id:
            return False
        if outcome.child_execution_target_id != shadow.selected_child_execution_target_id:
            return False
        if outcome.child_quota_pool_id != shadow.child_quota_pool_id:
            return False
    return True


def _sample(
    shadow: DelegationShadowRecord,
    child: DelegationOutcomeRecord | None,
    parent: DelegationOutcomeRecord | None,
    policy: DelegationReadinessPolicy | None,
) -> DelegationReadinessSample:
    reasons: list[DelegationReadinessReason] = []
    replay_matches = shadow.replay_matches()
    if not replay_matches:
        reasons.append(DelegationReadinessReason.SHADOW_REPLAY_MISMATCH)
    if not shadow.enforcement_ready:
        reasons.append(DelegationReadinessReason.SHADOW_NOT_ENFORCEMENT_READY)
    if child is None:
        reasons.append(DelegationReadinessReason.MISSING_CHILD_OUTCOME)
    if parent is None:
        reasons.append(DelegationReadinessReason.MISSING_PARENT_OUTCOME)

    identity_consistent = _identity_matches(shadow, child, parent)
    if not identity_consistent:
        reasons.append(DelegationReadinessReason.OUTCOME_IDENTITY_MISMATCH)

    child_verified = child is not None and child.child_verified is True
    parent_verified = parent is not None and parent.parent_verified is True
    child_verifier_passed = child is not None and child.child_verifier_passed is True
    parent_verifier_passed = parent is not None and parent.parent_verifier_passed is True

    if child is not None and not child_verified:
        reasons.append(DelegationReadinessReason.CHILD_NOT_VERIFIED)
    if parent is not None and not parent_verified:
        reasons.append(DelegationReadinessReason.PARENT_NOT_VERIFIED)
    if child is not None and not child_verifier_passed:
        reasons.append(DelegationReadinessReason.CHILD_VERIFIER_NOT_PASSED)
    if parent is not None and not parent_verifier_passed:
        reasons.append(DelegationReadinessReason.PARENT_VERIFIER_NOT_PASSED)

    quota_comparable = bool(
        child is not None
        and child.quota_before_snapshot_id
        and child.quota_after_snapshot_id
    )
    if policy is not None and policy.require_quota_comparability and not quota_comparable:
        reasons.append(DelegationReadinessReason.QUOTA_COMPARABILITY_MISSING)

    return DelegationReadinessSample(
        observation_id=shadow.observation_id,
        replay_matches=replay_matches,
        shadow_enforcement_ready=shadow.enforcement_ready,
        child_outcome_present=child is not None,
        parent_outcome_present=parent is not None,
        identity_consistent=identity_consistent,
        child_verified=child_verified,
        parent_verified=parent_verified,
        child_verifier_passed=child_verifier_passed,
        parent_verifier_passed=parent_verifier_passed,
        quota_comparable=quota_comparable,
        same_quota_pool_as_parent=_pool_relation(shadow),
        complete=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def evaluate_delegation_readiness(
    shadows: tuple[DelegationShadowRecord, ...],
    outcomes: tuple[DelegationOutcomeRecord, ...],
    *,
    policy: DelegationReadinessPolicy | None,
) -> DelegationReadinessReport:
    """Evaluate readiness without granting any live delegation authority."""

    child_by_observation: dict[str, DelegationOutcomeRecord] = {}
    parent_by_observation: dict[str, DelegationOutcomeRecord] = {}
    duplicate_outcome_identity = False
    for outcome in outcomes:
        target = (
            child_by_observation
            if outcome.phase is DelegationOutcomePhase.CHILD_FINAL
            else parent_by_observation
        )
        if outcome.observation_id in target:
            duplicate_outcome_identity = True
        else:
            target[outcome.observation_id] = outcome

    ordered_shadows = tuple(sorted(shadows, key=lambda item: item.observation_id))
    samples = tuple(
        _sample(
            shadow,
            child_by_observation.get(shadow.observation_id),
            parent_by_observation.get(shadow.observation_id),
            policy,
        )
        for shadow in ordered_shadows
    )

    blocking: list[DelegationReadinessReason] = []
    if policy is None:
        blocking.append(DelegationReadinessReason.POLICY_REQUIRED)
    if not ordered_shadows:
        blocking.append(DelegationReadinessReason.NO_SHADOW_EVIDENCE)
    if duplicate_outcome_identity:
        blocking.append(DelegationReadinessReason.OUTCOME_IDENTITY_MISMATCH)
    for sample in samples:
        blocking.extend(sample.reasons)

    complete_count = sum(item.complete for item in samples)
    same_pool_count = sum(item.same_quota_pool_as_parent is True for item in samples)
    different_pool_count = sum(item.same_quota_pool_as_parent is False for item in samples)
    unknown_pool_count = sum(item.same_quota_pool_as_parent is None for item in samples)

    if policy is not None:
        if complete_count < policy.min_complete_samples:
            blocking.append(DelegationReadinessReason.INSUFFICIENT_COMPLETE_SAMPLES)
        if policy.require_same_pool_sample and same_pool_count == 0:
            blocking.append(DelegationReadinessReason.SAME_POOL_COVERAGE_MISSING)
        if policy.require_different_pool_sample and different_pool_count == 0:
            blocking.append(DelegationReadinessReason.DIFFERENT_POOL_COVERAGE_MISSING)

    quota_comparable_count = sum(item.quota_comparable for item in samples)
    advisories: list[DelegationReadinessAdvisory] = []
    if quota_comparable_count == 0:
        advisories.append(DelegationReadinessAdvisory.NO_QUOTA_COMPARABLE_SAMPLES)
    if same_pool_count == 0:
        advisories.append(DelegationReadinessAdvisory.SAME_POOL_NOT_OBSERVED)
    if different_pool_count == 0:
        advisories.append(DelegationReadinessAdvisory.DIFFERENT_POOL_NOT_OBSERVED)
    if unknown_pool_count:
        advisories.append(DelegationReadinessAdvisory.UNKNOWN_POOL_RELATION_OBSERVED)

    unique_blocking = tuple(dict.fromkeys(blocking))
    status = (
        DelegationReadinessStatus.READY_FOR_LIMITED_EXPERIMENT
        if policy is not None and not unique_blocking
        else DelegationReadinessStatus.NOT_READY
    )
    return DelegationReadinessReport(
        status=status,
        input_digest=readiness_input_digest(shadows, outcomes, policy),
        policy_supplied=policy is not None,
        min_complete_samples=None if policy is None else policy.min_complete_samples,
        total_shadow_observations=len(samples),
        replayable_shadow_count=sum(item.replay_matches for item in samples),
        enforcement_ready_shadow_count=sum(item.shadow_enforcement_ready for item in samples),
        matched_child_outcome_count=sum(item.child_outcome_present for item in samples),
        matched_parent_outcome_count=sum(item.parent_outcome_present for item in samples),
        complete_sample_count=complete_count,
        child_verified_count=sum(item.child_verified for item in samples),
        parent_verified_count=sum(item.parent_verified for item in samples),
        quota_comparable_count=quota_comparable_count,
        same_pool_count=same_pool_count,
        different_pool_count=different_pool_count,
        unknown_pool_count=unknown_pool_count,
        blocking_reasons=unique_blocking,
        advisories=tuple(dict.fromkeys(advisories)),
        samples=samples,
    )


def load_readiness_evidence(
    runtime_state_root: str | Path,
) -> tuple[tuple[DelegationShadowRecord, ...], tuple[DelegationOutcomeRecord, ...]]:
    """Read existing evidence without creating or mutating runtime files."""

    root = Path(runtime_state_root)
    shadows = tuple(
        DelegationShadowRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((root / "delegation-shadow-history").glob("*.json"))
    )
    outcomes = tuple(
        DelegationOutcomeRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((root / "delegation-outcome-history").glob("*.json"))
    )
    return shadows, outcomes


__all__ = [
    "DelegationReadinessAdvisory",
    "DelegationReadinessPolicy",
    "DelegationReadinessReason",
    "DelegationReadinessReport",
    "DelegationReadinessSample",
    "DelegationReadinessStatus",
    "evaluate_delegation_readiness",
    "load_readiness_evidence",
    "readiness_input_digest",
]
