from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityState,
    mark_recovery_probe_due,
    observe_exhaustion,
    observe_success,
)
from personal_ai_orchestrator.supervised_auto_step import _AUTO_OK_QUOTA_STATES

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def test_recovered_observed_quota_is_admitted_after_proven_recovery() -> None:
    exhausted = observe_exhaustion(
        None,
        execution_target_id="target",
        provider_id="provider",
        quota_pool_id="pool",
        observed_at=NOW,
        sanitized_reason_code="USAGE_LIMIT",
    )
    due = mark_recovery_probe_due(
        exhausted,
        observed_at=NOW + timedelta(hours=2),
    )
    recovered = observe_success(
        due,
        execution_target_id="target",
        provider_id="provider",
        quota_pool_id="pool",
        observed_at=NOW + timedelta(hours=2, minutes=1),
    )

    assert recovered.state is QuotaAvailabilityState.RECOVERED_OBSERVED
    assert recovered.state in _AUTO_OK_QUOTA_STATES


def test_supervised_auto_quota_gate_remains_fail_closed_for_non_admissible_states() -> None:
    blocked_states = {
        QuotaAvailabilityState.UNKNOWN,
        QuotaAvailabilityState.EXHAUSTED_OBSERVED,
        QuotaAvailabilityState.COOLDOWN,
        QuotaAvailabilityState.RECOVERY_PROBE_DUE,
        QuotaAvailabilityState.UNCERTAIN_LOCKED,
    }

    assert blocked_states.isdisjoint(_AUTO_OK_QUOTA_STATES)
