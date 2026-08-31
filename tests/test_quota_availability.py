from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityEvidence,
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    mark_recovery_probe_due,
    observe_exhaustion,
    observe_success,
)
from personal_ai_orchestrator.quota_policy_analysis import MeasurementSource
from personal_ai_orchestrator.shadow_evidence import ResetCycleSource

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def test_usage_limit_observation_creates_exhausted_cooldown_without_exact_quota() -> None:
    evidence = observe_exhaustion(
        None,
        execution_target_id="codex-cli-gpt-5.5",
        provider_id="openai",
        quota_pool_id="codex-chatgpt-plan",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
        minimum_cooldown_seconds=600,
    )

    assert evidence.state is QuotaAvailabilityState.COOLDOWN
    assert evidence.blocks_quota_billable_launch(now=NOW) is True
    assert (
        evidence.state_at(now=NOW + timedelta(minutes=11))
        is QuotaAvailabilityState.RECOVERY_PROBE_DUE
    )
    assert evidence.measurement_source is MeasurementSource.LOCALLY_MEASURED
    assert evidence.confidence is EvidenceConfidence.ESTIMATED
    assert evidence.reset_cycle_source is ResetCycleSource.LOCALLY_INFERRED
    assert evidence.remaining_fraction is None
    assert evidence.reset_at is None


def test_observed_availability_refuses_exact_or_provider_exact_claims() -> None:
    with pytest.raises(ValueError, match="PROVIDER_EXACT"):
        QuotaAvailabilityEvidence(
            execution_target_id="target",
            provider_id="openai",
            quota_pool_id="pool",
            state=QuotaAvailabilityState.UNKNOWN,
            observed_at=NOW,
            measurement_source=MeasurementSource.PROVIDER_EXACT,
        )

    with pytest.raises(ValueError, match="EXACT confidence"):
        QuotaAvailabilityEvidence(
            execution_target_id="target",
            provider_id="openai",
            quota_pool_id="pool",
            state=QuotaAvailabilityState.UNKNOWN,
            observed_at=NOW,
            confidence=EvidenceConfidence.EXACT,
        )


def test_recovery_success_records_interval_but_no_provider_exact_reset() -> None:
    exhausted = observe_exhaustion(
        None,
        execution_target_id="codex-cli-gpt-5.5",
        provider_id="openai",
        quota_pool_id="codex-chatgpt-plan",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
    )
    due = mark_recovery_probe_due(exhausted, observed_at=NOW + timedelta(hours=2))
    recovered = observe_success(
        due,
        execution_target_id="codex-cli-gpt-5.5",
        provider_id="openai",
        quota_pool_id="codex-chatgpt-plan",
        observed_at=NOW + timedelta(hours=2, minutes=1),
    )

    assert recovered.state is QuotaAvailabilityState.RECOVERED_OBSERVED
    assert recovered.exhaustion_to_recovery_seconds == 7260
    assert recovered.reset_cycle_source is ResetCycleSource.LOCALLY_INFERRED
    assert recovered.reset_at is None
    assert recovered.remaining_fraction is None


def test_availability_journal_persists_sanitized_state(tmp_path) -> None:
    journal = QuotaAvailabilityJournal(tmp_path)
    evidence = observe_exhaustion(
        None,
        execution_target_id="codex-cli-gpt-5.5",
        provider_id="openai",
        quota_pool_id="codex-chatgpt-plan",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
    )

    journal.save(evidence)

    assert journal.load("codex-cli-gpt-5.5") == evidence
    snapshot = journal.snapshot()
    assert snapshot["codex-cli-gpt-5.5"]["sanitized_reason_code"] == "USAGE_LIMIT"
