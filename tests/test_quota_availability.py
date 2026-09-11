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
    observe_rate_limited,
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


# -----------------------------------------------------------------------------
# M1 WP4 — AVAILABLE_UNMETERED state + observe_rate_limited + state_at expiry
# -----------------------------------------------------------------------------


def test_available_unmetered_state_exists_and_round_trips() -> None:
    """``QuotaAvailabilityState.AVAILABLE_UNMETERED`` is exported and serialises."""

    assert QuotaAvailabilityState.AVAILABLE_UNMETERED.value == "AVAILABLE_UNMETERED"
    payload = QuotaAvailabilityEvidence(
        execution_target_id="opencode-big-pickle",
        provider_id="opencode",
        quota_pool_id="opencode",
        observed_at=NOW,
        state=QuotaAvailabilityState.AVAILABLE_UNMETERED,
    )
    assert payload.state is QuotaAvailabilityState.AVAILABLE_UNMETERED
    rendered = payload.model_dump_json()
    assert "AVAILABLE_UNMETERED" in rendered


def test_observe_rate_limited_records_cooldown_and_baseline() -> None:
    """``observe_rate_limited`` flips to COOLDOWN, records cooldown_until + baseline."""

    target = "opencode-big-pickle"
    later = NOW + timedelta(seconds=900)
    evidence = observe_rate_limited(
        previous=None,
        execution_target_id=target,
        provider_id="opencode",
        quota_pool_id="opencode",
        observed_at=NOW,
        cooldown_seconds=900,
    )
    assert evidence.state is QuotaAvailabilityState.COOLDOWN
    assert evidence.cooldown_until == later
    # Baseline is None when no previous exists (windowed path will
    # pass the previous state). Unmetered path calls evidence()
    # directly so the baseline is filled in by the collector.
    assert evidence.previous_state_baseline is None
    assert evidence.consecutive_failures == 0
    assert evidence.sanitized_reason_code == "WORKER_RATE_LIMIT"


def test_state_at_returns_to_unmetered_baseline_after_cooldown() -> None:
    """An unmetered target's COOLDOWN expires back to AVAILABLE_UNMETERED.

    The windowed path returns RECOVERY_PROBE_DUE. M1 WP4 splits the
    two via ``previous_state_baseline`` so the unmetered path
    skips the probe round-trip.
    """

    evidence = observe_rate_limited(
        previous=None,
        execution_target_id="opencode-big-pickle",
        provider_id="opencode",
        quota_pool_id="opencode",
        observed_at=NOW,
        cooldown_seconds=60,
    )
    # Without a baseline, the unmetered path cannot short-circuit; the
    # state stays COOLDOWN until a new observation arrives.
    assert evidence.state_at(now=NOW) is QuotaAvailabilityState.COOLDOWN
    assert (
        evidence.state_at(now=NOW + timedelta(seconds=120))
        is QuotaAvailabilityState.RECOVERY_PROBE_DUE
    )

    # With the baseline set to AVAILABLE_UNMETERED, the cooldown
    # expires back to AVAILABLE_UNMETERED directly.
    baseline_evidence = evidence.model_copy(
        update={"previous_state_baseline": QuotaAvailabilityState.AVAILABLE_UNMETERED}
    )
    assert (
        baseline_evidence.state_at(now=NOW + timedelta(seconds=120))
        is QuotaAvailabilityState.AVAILABLE_UNMETERED
    )


def test_observe_rate_limited_is_idempotent() -> None:
    """A second ``observe_rate_limited`` in the same window keeps the streak 0.

    The ``consecutive_failures`` counter is for *quota collection*
    failures (A2). Rate-limit hits are a worker-classified event;
    they must not advance the streak or risk firing
    UNCERTAIN_LOCKED on the unmetered path.
    """

    first = observe_rate_limited(
        previous=None,
        execution_target_id="opencode-big-pickle",
        provider_id="opencode",
        quota_pool_id="opencode",
        observed_at=NOW,
        cooldown_seconds=900,
    )
    second = observe_rate_limited(
        previous=first,
        execution_target_id="opencode-big-pickle",
        provider_id="opencode",
        quota_pool_id="opencode",
        observed_at=NOW + timedelta(seconds=10),
        cooldown_seconds=900,
    )
    assert second.consecutive_failures == 0
    # The cooldown_until pushes forward because the new observation
    # arrives 10 seconds later.
    assert second.cooldown_until > first.cooldown_until


def test_windowed_observe_exhaustion_baseline_remains_windowed() -> None:
    """Windowed targets keep ``previous_state_baseline = AVAILABLE_OBSERVED``.

    A windowed target going through ``observe_exhaustion`` records
    ``AVAILABLE_OBSERVED`` (or whatever the previous state was) as
    the baseline. ``state_at`` then returns RECOVERY_PROBE_DUE when
    the cooldown expires — no probe-skip on the windowed path.
    """

    target = "zai-coding-plan-glm-5.3"
    success = observe_success(
        previous=None,
        execution_target_id=target,
        provider_id="zai-coding-plan",
        quota_pool_id="zai-coding-plan",
        observed_at=NOW,
    )
    exhausted = observe_exhaustion(
        success,
        execution_target_id=target,
        provider_id="zai-coding-plan",
        quota_pool_id="zai-coding-plan",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
    )
    assert (
        exhausted.previous_state_baseline
        is QuotaAvailabilityState.AVAILABLE_OBSERVED
    )
    assert (
        exhausted.state_at(now=NOW + timedelta(seconds=7200))
        is QuotaAvailabilityState.RECOVERY_PROBE_DUE
    )
