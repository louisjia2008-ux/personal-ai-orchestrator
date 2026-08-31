import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_policy_analysis import (
    CurrentPolicyResult,
    MeasurementSource,
    QuotaAuthorityClass,
    SimulatedQuotaEvidence,
    UnsupportedQuotaPolicy,
    current_exact_only_policy_result,
    simulate_unsupported_quota_policy,
)
from personal_ai_orchestrator.shadow_evidence import ResetCycleSource


def test_exact_only_blocks_provider_without_exact_quota_surface() -> None:
    result = current_exact_only_policy_result(provider_exact_surface_available=False)
    assert result is CurrentPolicyResult.ACTIVE_PERMANENTLY_BLOCKED_WITHOUT_PROVIDER_EXACT_SURFACE

    simulated = simulate_unsupported_quota_policy(
        UnsupportedQuotaPolicy.EXACT_ONLY,
        SimulatedQuotaEvidence(),
    )

    assert simulated.admitted_for_simulation is False
    assert simulated.authority_class is QuotaAuthorityClass.UNSUPPORTED
    assert simulated.creates_real_reset_evidence is False
    assert simulated.creates_owner_approval is False


def test_provider_exact_remains_strongest_authority() -> None:
    simulated = simulate_unsupported_quota_policy(
        UnsupportedQuotaPolicy.EXACT_ONLY,
        SimulatedQuotaEvidence(
            measurement_source=MeasurementSource.PROVIDER_EXACT,
            confidence=EvidenceConfidence.EXACT,
            has_known_reset_time=True,
        ),
    )

    assert simulated.admitted_for_simulation is True
    assert simulated.authority_class is QuotaAuthorityClass.EXACT_PROVIDER_RESET_ACCEPTED


def test_unknown_and_locally_measured_evidence_cannot_become_provider_exact() -> None:
    with pytest.raises(ValueError, match="cannot carry EXACT confidence"):
        SimulatedQuotaEvidence(
            measurement_source=MeasurementSource.LOCALLY_MEASURED,
            confidence=EvidenceConfidence.EXACT,
        )

    evidence = SimulatedQuotaEvidence(
        measurement_source=MeasurementSource.LOCALLY_MEASURED,
        confidence=EvidenceConfidence.ESTIMATED,
        observed_successes=20,
        observed_reset_transitions=2,
    )

    assert evidence.reset_cycle_source is ResetCycleSource.LOCALLY_INFERRED
    assert evidence.confidence is EvidenceConfidence.ESTIMATED


def test_locally_measured_conservative_mode_is_simulation_only() -> None:
    simulated = simulate_unsupported_quota_policy(
        UnsupportedQuotaPolicy.LOCALLY_MEASURED_CONSERVATIVE,
        SimulatedQuotaEvidence(
            measurement_source=MeasurementSource.LOCALLY_MEASURED,
            confidence=EvidenceConfidence.ESTIMATED,
            observed_successes=20,
            observed_exhaustions=1,
            observed_reset_transitions=2,
            uncertainty_margin_fraction=0.25,
        ),
    )

    assert simulated.admitted_for_simulation is True
    assert simulated.authority_class is QuotaAuthorityClass.CONSERVATIVE_LOCAL_WINDOW_ACCEPTED
    assert simulated.creates_real_reset_evidence is False
    assert simulated.creates_owner_approval is False


def test_user_declared_window_mode_is_distinct_from_provider_truth() -> None:
    simulated = simulate_unsupported_quota_policy(
        UnsupportedQuotaPolicy.USER_DECLARED_WINDOW,
        SimulatedQuotaEvidence(
            measurement_source=MeasurementSource.USER_DECLARED,
            confidence=EvidenceConfidence.ESTIMATED,
            has_known_reset_time=True,
            user_declared_window=True,
        ),
    )

    assert simulated.admitted_for_simulation is True
    assert simulated.authority_class is QuotaAuthorityClass.USER_DECLARED_WINDOW_ACCEPTED
    assert simulated.creates_real_reset_evidence is False
    assert simulated.creates_owner_approval is False
