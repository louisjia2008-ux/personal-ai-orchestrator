"""Policy simulation for providers without exact subscription quota surfaces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import EvidenceConfidence, RegistryModel
from personal_ai_orchestrator.shadow_evidence import ResetCycleSource


class UnsupportedQuotaPolicy(StrEnum):
    EXACT_ONLY = "EXACT_ONLY"
    LOCALLY_MEASURED_CONSERVATIVE = "LOCALLY_MEASURED_CONSERVATIVE"
    USER_DECLARED_WINDOW = "USER_DECLARED_WINDOW"


class MeasurementSource(StrEnum):
    PROVIDER_EXACT = "PROVIDER_EXACT"
    LOCALLY_MEASURED = "LOCALLY_MEASURED"
    USER_DECLARED = "USER_DECLARED"
    UNKNOWN = "UNKNOWN"


class QuotaAuthorityClass(StrEnum):
    EXACT_PROVIDER_RESET_ACCEPTED = "EXACT_PROVIDER_RESET_ACCEPTED"
    CONSERVATIVE_LOCAL_WINDOW_ACCEPTED = "CONSERVATIVE_LOCAL_WINDOW_ACCEPTED"
    USER_DECLARED_WINDOW_ACCEPTED = "USER_DECLARED_WINDOW_ACCEPTED"
    UNSUPPORTED = "UNSUPPORTED"


class CurrentPolicyResult(StrEnum):
    ACTIVE_EVENTUALLY_POSSIBLE = "ACTIVE_EVENTUALLY_POSSIBLE"
    ACTIVE_PERMANENTLY_BLOCKED_WITHOUT_PROVIDER_EXACT_SURFACE = (
        "ACTIVE_PERMANENTLY_BLOCKED_WITHOUT_PROVIDER_EXACT_SURFACE"
    )
    CONDITIONALLY_POSSIBLE = "CONDITIONALLY_POSSIBLE"
    UNKNOWN = "UNKNOWN"


class SimulatedQuotaEvidence(RegistryModel):
    measurement_source: MeasurementSource = MeasurementSource.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    has_known_reset_time: bool = False
    observed_successes: int = Field(default=0, ge=0)
    observed_exhaustions: int = Field(default=0, ge=0)
    observed_reset_transitions: int = Field(default=0, ge=0)
    user_declared_window: bool = False
    uncertainty_margin_fraction: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_source_truth(self) -> SimulatedQuotaEvidence:
        if self.measurement_source is not MeasurementSource.PROVIDER_EXACT:
            if self.confidence is EvidenceConfidence.EXACT:
                raise ValueError("non-provider measurements cannot carry EXACT confidence")
        return self

    @property
    def reset_cycle_source(self) -> ResetCycleSource:
        if self.measurement_source is MeasurementSource.PROVIDER_EXACT:
            return ResetCycleSource.PROVIDER_EXACT
        if self.measurement_source is MeasurementSource.LOCALLY_MEASURED:
            return ResetCycleSource.LOCALLY_INFERRED
        if self.measurement_source is MeasurementSource.USER_DECLARED:
            return ResetCycleSource.LOCALLY_INFERRED
        return ResetCycleSource.UNKNOWN


class QuotaPolicySimulationResult(RegistryModel):
    policy: UnsupportedQuotaPolicy
    authority_class: QuotaAuthorityClass
    admitted_for_simulation: bool
    creates_real_reset_evidence: bool = False
    creates_owner_approval: bool = False
    blocking_reasons: tuple[str, ...] = ()


def simulate_unsupported_quota_policy(
    policy: UnsupportedQuotaPolicy,
    evidence: SimulatedQuotaEvidence,
) -> QuotaPolicySimulationResult:
    if policy is UnsupportedQuotaPolicy.EXACT_ONLY:
        accepted = (
            evidence.measurement_source is MeasurementSource.PROVIDER_EXACT
            and evidence.confidence is EvidenceConfidence.EXACT
            and evidence.has_known_reset_time
        )
        return QuotaPolicySimulationResult(
            policy=policy,
            authority_class=(
                QuotaAuthorityClass.EXACT_PROVIDER_RESET_ACCEPTED
                if accepted
                else QuotaAuthorityClass.UNSUPPORTED
            ),
            admitted_for_simulation=accepted,
            blocking_reasons=()
            if accepted
            else ("provider-exact quota/reset evidence with known reset time is required",),
        )

    if policy is UnsupportedQuotaPolicy.LOCALLY_MEASURED_CONSERVATIVE:
        accepted = (
            evidence.measurement_source is MeasurementSource.LOCALLY_MEASURED
            and evidence.confidence is EvidenceConfidence.ESTIMATED
            and evidence.observed_successes > 0
            and evidence.observed_reset_transitions > 0
            and evidence.uncertainty_margin_fraction >= 0.1
        )
        return QuotaPolicySimulationResult(
            policy=policy,
            authority_class=(
                QuotaAuthorityClass.CONSERVATIVE_LOCAL_WINDOW_ACCEPTED
                if accepted
                else QuotaAuthorityClass.UNSUPPORTED
            ),
            admitted_for_simulation=accepted,
            blocking_reasons=()
            if accepted
            else (
                "locally measured fallback requires ESTIMATED evidence, observed successes, "
                "reset transitions and conservative uncertainty",
            ),
        )

    accepted = (
        evidence.measurement_source is MeasurementSource.USER_DECLARED
        and evidence.confidence is EvidenceConfidence.ESTIMATED
        and evidence.user_declared_window
        and evidence.has_known_reset_time
    )
    return QuotaPolicySimulationResult(
        policy=policy,
        authority_class=(
            QuotaAuthorityClass.USER_DECLARED_WINDOW_ACCEPTED
            if accepted
            else QuotaAuthorityClass.UNSUPPORTED
        ),
        admitted_for_simulation=accepted,
        blocking_reasons=()
        if accepted
        else (
            "user-declared fallback requires explicit user window semantics and known reset time",
        ),
    )


def current_exact_only_policy_result(
    *,
    provider_exact_surface_available: bool,
) -> CurrentPolicyResult:
    if provider_exact_surface_available:
        return CurrentPolicyResult.CONDITIONALLY_POSSIBLE
    return CurrentPolicyResult.ACTIVE_PERMANENTLY_BLOCKED_WITHOUT_PROVIDER_EXACT_SURFACE


__all__ = [
    "CurrentPolicyResult",
    "MeasurementSource",
    "QuotaAuthorityClass",
    "QuotaPolicySimulationResult",
    "SimulatedQuotaEvidence",
    "UnsupportedQuotaPolicy",
    "current_exact_only_policy_result",
    "simulate_unsupported_quota_policy",
]
