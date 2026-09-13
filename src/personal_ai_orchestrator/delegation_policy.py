"""Deterministic host-owned delegation admission policy.

PI-5B3 deliberately starts with a small, explainable policy surface.  The
policy answers whether host evidence is sufficient to justify a delegation
request; it does not select a provider, launch a worker, grant repository
permissions, or replace downstream quota/verification gates.

The first slice intentionally avoids an opaque utility score.  Signals such as
same-pool scarcity are recorded as reasons but do not become arbitrary numeric
penalties until shadow evidence exists to calibrate them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import RegistryModel


class DelegationPolicyMode(StrEnum):
    """How a delegation decision may be consumed by a caller."""

    OFF = "OFF"
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"


class DelegationVerdict(StrEnum):
    """Policy recommendation before downstream child execution gates."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    SHADOW_ONLY = "SHADOW_ONLY"


class DelegationReasonCode(StrEnum):
    FEATURE_DISABLED = "FEATURE_DISABLED"
    NO_ELIGIBLE_CHILD = "NO_ELIGIBLE_CHILD"
    REQUIRED_QUOTA_TRUTH_MISSING = "REQUIRED_QUOTA_TRUTH_MISSING"
    REQUIRED_BURN_ESTIMATE_MISSING = "REQUIRED_BURN_ESTIMATE_MISSING"
    CHILD_HEADROOM_INSUFFICIENT = "CHILD_HEADROOM_INSUFFICIENT"
    PAID_USAGE_NOT_ALLOWED = "PAID_USAGE_NOT_ALLOWED"
    INDEPENDENCE_REQUIREMENT_UNSATISFIED = "INDEPENDENCE_REQUIREMENT_UNSATISFIED"
    HOST_REQUIRED_DELEGATION = "HOST_REQUIRED_DELEGATION"
    FAILURE_ESCALATION_REACHED = "FAILURE_ESCALATION_REACHED"
    SHARED_QUOTA_POOL = "SHARED_QUOTA_POOL"
    DIFFERENT_QUOTA_POOL = "DIFFERENT_QUOTA_POOL"
    INSUFFICIENT_JUSTIFICATION_FOR_ACTIVE_DELEGATION = (
        "INSUFFICIENT_JUSTIFICATION_FOR_ACTIVE_DELEGATION"
    )


class DelegationPolicyInput(RegistryModel):
    """Host-derived facts only; no provider/model choice comes from the worker."""

    delegation_feature_enabled: bool = False
    host_required: bool = False
    failure_count: int = Field(default=0, ge=0)
    failure_escalation_after: int = Field(default=2, ge=1)

    eligible_child_count: int = Field(default=0, ge=0)
    quota_truth_required: bool = True
    quota_truth_known: bool = False
    require_burn_estimate: bool = True
    predicted_child_burn_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    usable_child_headroom_fraction: float | None = Field(default=None, ge=0.0, le=1.0)

    paid_usage_required: bool = False
    paid_usage_allowed: bool = False

    independence_required: bool = False
    different_provider_candidate_available: bool = False

    same_quota_pool_as_parent: bool | None = None

    @model_validator(mode="after")
    def validate_headroom_pair(self) -> DelegationPolicyInput:
        if (
            self.predicted_child_burn_fraction is not None
            and self.usable_child_headroom_fraction is None
            and self.quota_truth_required
        ):
            raise ValueError("predicted burn requires usable headroom when quota truth is required")
        return self


class DelegationDecision(RegistryModel):
    verdict: DelegationVerdict
    reasons: tuple[DelegationReasonCode, ...]
    enforceable: bool

    @model_validator(mode="after")
    def validate_enforceable(self) -> DelegationDecision:
        if self.verdict is DelegationVerdict.SHADOW_ONLY and self.enforceable:
            raise ValueError("SHADOW_ONLY decisions cannot be enforceable")
        return self


def _pool_reason(value: bool | None) -> DelegationReasonCode | None:
    if value is True:
        return DelegationReasonCode.SHARED_QUOTA_POOL
    if value is False:
        return DelegationReasonCode.DIFFERENT_QUOTA_POOL
    return None


def evaluate_delegation(
    facts: DelegationPolicyInput,
    *,
    mode: DelegationPolicyMode = DelegationPolicyMode.SHADOW,
) -> DelegationDecision:
    """Return a deterministic constraint-first delegation recommendation.

    Hard denials are evaluated before justification.  Passing the hard gates is
    not itself enough to spend a second worker: active ALLOW requires a host-owned
    requirement or the existing failure-escalation threshold.  Otherwise the
    result remains SHADOW_ONLY so PAO can collect evidence without pretending a
    calibrated delegation-economics model exists.
    """

    pool_reason = _pool_reason(facts.same_quota_pool_as_parent)

    def deny(reason: DelegationReasonCode) -> DelegationDecision:
        reasons = (reason,) + ((pool_reason,) if pool_reason is not None else ())
        return DelegationDecision(
            verdict=DelegationVerdict.DENY,
            reasons=reasons,
            enforceable=mode is DelegationPolicyMode.ENFORCE,
        )

    if not facts.delegation_feature_enabled or mode is DelegationPolicyMode.OFF:
        return deny(DelegationReasonCode.FEATURE_DISABLED)
    if facts.eligible_child_count == 0:
        return deny(DelegationReasonCode.NO_ELIGIBLE_CHILD)
    if facts.quota_truth_required and not facts.quota_truth_known:
        return deny(DelegationReasonCode.REQUIRED_QUOTA_TRUTH_MISSING)
    if facts.require_burn_estimate and facts.predicted_child_burn_fraction is None:
        return deny(DelegationReasonCode.REQUIRED_BURN_ESTIMATE_MISSING)
    if (
        facts.predicted_child_burn_fraction is not None
        and facts.usable_child_headroom_fraction is not None
        and facts.predicted_child_burn_fraction > facts.usable_child_headroom_fraction
    ):
        return deny(DelegationReasonCode.CHILD_HEADROOM_INSUFFICIENT)
    if facts.paid_usage_required and not facts.paid_usage_allowed:
        return deny(DelegationReasonCode.PAID_USAGE_NOT_ALLOWED)
    if facts.independence_required and not facts.different_provider_candidate_available:
        return deny(DelegationReasonCode.INDEPENDENCE_REQUIREMENT_UNSATISFIED)

    reasons: list[DelegationReasonCode] = []
    if facts.host_required:
        reasons.append(DelegationReasonCode.HOST_REQUIRED_DELEGATION)
    if facts.failure_count >= facts.failure_escalation_after:
        reasons.append(DelegationReasonCode.FAILURE_ESCALATION_REACHED)
    if pool_reason is not None:
        reasons.append(pool_reason)

    if any(
        reason
        in {
            DelegationReasonCode.HOST_REQUIRED_DELEGATION,
            DelegationReasonCode.FAILURE_ESCALATION_REACHED,
        }
        for reason in reasons
    ):
        return DelegationDecision(
            verdict=DelegationVerdict.ALLOW,
            reasons=tuple(reasons),
            enforceable=mode is DelegationPolicyMode.ENFORCE,
        )

    reasons.append(DelegationReasonCode.INSUFFICIENT_JUSTIFICATION_FOR_ACTIVE_DELEGATION)
    return DelegationDecision(
        verdict=DelegationVerdict.SHADOW_ONLY,
        reasons=tuple(reasons),
        enforceable=False,
    )


__all__ = [
    "DelegationDecision",
    "DelegationPolicyInput",
    "DelegationPolicyMode",
    "DelegationReasonCode",
    "DelegationVerdict",
    "evaluate_delegation",
]
