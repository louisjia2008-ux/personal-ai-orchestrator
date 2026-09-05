from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_availability import (
    UNCERTAIN_LOCKED_THRESHOLD,
    QuotaAvailabilityEvidence,
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    mark_recovery_probe_due,
    observe_exhaustion,
    observe_success,
    unknown_availability,
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


def test_unknown_availability_without_previous_starts_streak_at_one() -> None:
    """A fresh target's first UNKNOWN observation starts the streak at 1.

    Only consecutive UNKNOWNs count; one failure is below the threshold so
    admission MUST not lock yet.
    """

    evidence = unknown_availability(
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW,
    )
    assert evidence.consecutive_failures == 1
    assert evidence.state is QuotaAvailabilityState.UNKNOWN
    assert evidence.state_at(now=NOW) is QuotaAvailabilityState.UNKNOWN


def test_unknown_availability_carries_streak_from_previous_unknown() -> None:
    """A second consecutive UNKNOWN bumps the streak to 2.

    Two failures is still below the threshold — admission MUST not lock yet.
    """

    first = unknown_availability(
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW,
    )
    second = unknown_availability(
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW + timedelta(seconds=30),
        previous=first,
    )
    assert first.consecutive_failures == 1
    assert second.consecutive_failures == 2
    assert second.state is QuotaAvailabilityState.UNKNOWN
    assert second.state_at(now=NOW + timedelta(seconds=30)) is QuotaAvailabilityState.UNKNOWN


def test_unknown_availability_locks_after_threshold_consecutive_failures() -> None:
    """Threshold consecutive UNKNOWNs project UNCERTAIN_LOCKED.

    The underlying state stays UNKNOWN so a single observe_success() can
    clear both the lock and the streak in one transaction. Note that
    ``blocks_quota_billable_launch`` is intentionally scoped to definitive
    exhaustions — UNCERTAIN_LOCKED must be observable separately so
    ``_admit_quota`` can attempt a fresh observation that may release it.
    """

    streak = None
    for index in range(UNCERTAIN_LOCKED_THRESHOLD):
        streak = unknown_availability(
            execution_target_id="target-x",
            provider_id="provider-x",
            quota_pool_id="provider-x",
            observed_at=NOW + timedelta(seconds=index + 1),
            previous=streak,
        )
    assert streak is not None
    assert streak.consecutive_failures == UNCERTAIN_LOCKED_THRESHOLD
    assert streak.state is QuotaAvailabilityState.UNKNOWN
    assert streak.state_at(now=NOW + timedelta(seconds=10)) is (
        QuotaAvailabilityState.UNCERTAIN_LOCKED
    )
    # UNCERTAIN_LOCKED is intentionally NOT a "definitive" block — the
    # next dispatch must still attempt a fresh collection so a single
    # success can release the lock atomically.
    assert streak.blocks_quota_billable_launch(now=NOW + timedelta(seconds=10)) is False


def test_uncertain_locked_state_clears_on_next_successful_observation() -> None:
    """One observe_success() resets the streak to zero.

    The lock is derived from the streak, so a single success immediately
    admits the target again — admission is fail-closed in the bad direction,
    not the good one.
    """

    streak = None
    for index in range(UNCERTAIN_LOCKED_THRESHOLD):
        streak = unknown_availability(
            execution_target_id="target-x",
            provider_id="provider-x",
            quota_pool_id="provider-x",
            observed_at=NOW + timedelta(seconds=index + 1),
            previous=streak,
        )
    assert streak is not None
    assert streak.state_at(now=NOW + timedelta(seconds=10)) is (
        QuotaAvailabilityState.UNCERTAIN_LOCKED
    )

    recovered = observe_success(
        streak,
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW + timedelta(minutes=1),
    )
    assert recovered.consecutive_failures == 0
    assert recovered.state is QuotaAvailabilityState.AVAILABLE_OBSERVED
    assert recovered.state_at(now=NOW + timedelta(minutes=1)) is (
        QuotaAvailabilityState.AVAILABLE_OBSERVED
    )


def test_uncertain_locked_resets_when_an_unknown_follows_a_success() -> None:
    """Success resets the streak; the next UNKNOWN starts a new streak at 1.

    A single recovered observation must not preserve the previous streak —
    the host's only record of a working probe should immediately lower the
    rejection pressure.
    """

    streak = None
    for index in range(UNCERTAIN_LOCKED_THRESHOLD):
        streak = unknown_availability(
            execution_target_id="target-x",
            provider_id="provider-x",
            quota_pool_id="provider-x",
            observed_at=NOW + timedelta(seconds=index + 1),
            previous=streak,
        )
    recovered = observe_success(
        streak,
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW + timedelta(minutes=1),
    )
    next_unknown = unknown_availability(
        execution_target_id="target-x",
        provider_id="provider-x",
        quota_pool_id="provider-x",
        observed_at=NOW + timedelta(minutes=2),
        previous=recovered,
    )
    assert recovered.consecutive_failures == 0
    assert next_unknown.consecutive_failures == 1
    assert next_unknown.state_at(now=NOW + timedelta(minutes=2)) is (
        QuotaAvailabilityState.UNKNOWN
    )
