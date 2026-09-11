from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_orchestrator.model_registry import QuotaWindowKind
from personal_ai_orchestrator.quota_burn import (
    BurnAssessment,
    BurnPressure,
    assess,
    infer_window_started_at,
    rolling_hourly_cap,
)


def _window(
    *,
    reset_in_seconds: float,
    total_seconds: float,
    now_offset: float,
) -> dict[str, datetime]:
    """Build a window whose reset is ``reset_in_seconds`` away from ``now``."""

    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    reset_at = now + timedelta(seconds=reset_in_seconds)
    window_started_at = reset_at - timedelta(seconds=total_seconds)
    return {
        "window_started_at": window_started_at,
        "reset_at": reset_at,
        "now": now + timedelta(seconds=now_offset),
    }


def test_assess_unmetered_when_used_fraction_is_none() -> None:
    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=0.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=None,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.UNMETERED
    assert result.pressure_score == 0.0
    # UNMETERED returns None for every numerical field — no 0.0/1.0 impostors.
    assert result.expected_used_fraction is None
    assert result.actual_used_fraction is None
    assert result.deviation is None
    assert result.remaining_fraction is None
    assert result.seconds_to_reset is None


def test_assess_stale_when_now_is_at_or_after_reset_at() -> None:
    # reset_at - 1ms: now strictly after the window has ended, snapshot not
    # refreshed yet. STALE wins over STARVED (the cached reading may still
    # report 0.9 remaining — close enough to look "starved" by the truth
    # table) precisely because the orchestrator must not trigger STARVED
    # against expired data.
    now = datetime(2026, 9, 1, 13, 0, 1, tzinfo=UTC)
    window_started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    result = assess(
        window_started_at=window_started_at,
        reset_at=reset_at,
        used_fraction=0.10,  # 90% remaining — would be STARVED on a live window
        now=now,
    )
    assert result.pressure is BurnPressure.STALE
    assert result.pressure_score == 0.0
    # STALE keeps the real figures so the bar can still draw.
    assert result.expected_used_fraction is not None
    assert result.actual_used_fraction == 0.10
    assert result.seconds_to_reset == 0.0


def test_assess_stale_takes_precedence_over_starved() -> None:
    """Even with ample remaining and a fast horizon, expired → STALE."""

    # Make the data look "starved" (90% remaining) AND the window expired.
    now = datetime(2026, 9, 1, 13, 0, 1, tzinfo=UTC)
    window_started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    result = assess(
        window_started_at=window_started_at,
        reset_at=reset_at,
        used_fraction=0.10,
        now=now,
    )
    assert result.pressure is BurnPressure.STALE
    assert result.pressure is not BurnPressure.STARVED


def test_assess_unmetered_takes_precedence_over_stale() -> None:
    """``used_fraction=None`` short-circuits before any time check."""

    now = datetime(2026, 9, 1, 13, 0, 1, tzinfo=UTC)
    window_started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    result = assess(
        window_started_at=window_started_at,
        reset_at=reset_at,
        used_fraction=None,
        now=now,
    )
    assert result.pressure is BurnPressure.UNMETERED
    assert result.pressure is not BurnPressure.STALE


def test_assess_exhausted_when_used_fraction_at_or_above_one() -> None:
    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=1800.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=1.0,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.EXHAUSTED
    assert result.pressure_score == 1.0


def test_assess_clamps_used_fraction_above_one_to_exhausted() -> None:
    """A collector-reported 1.02 (rounding boundary) is EXHAUSTED, not raise."""

    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=1800.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=1.02,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.EXHAUSTED
    assert result.actual_used_fraction == 1.0


def test_assess_clamps_used_fraction_below_zero() -> None:
    # 24h window so seconds_to_reset stays above the 12h STARVED threshold;
    # we want this assertion to isolate BEHIND, not STARVED.
    times = _window(reset_in_seconds=24 * 3600.0, total_seconds=24 * 3600.0, now_offset=12 * 3600.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=-0.01,
        now=times["now"],
    )
    assert result.actual_used_fraction == 0.0
    # At the midpoint with 0% used → deviation ≈ -0.5 → BEHIND.
    assert result.pressure is BurnPressure.BEHIND


def test_assess_starved_when_remaining_high_and_reset_soon() -> None:
    """STARVED = too much remaining, reset imminent — NOT almost-empty."""

    # 80% remaining, reset in 1 hour (< 12h threshold); 24h total so the
    # STARVED row actually fires instead of being silenced by an off-by-one.
    times = _window(reset_in_seconds=3600.0, total_seconds=86_400.0, now_offset=0.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.20,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.STARVED
    assert result.pressure_score == -1.0


def test_assess_starved_requires_both_remaining_and_horizon() -> None:
    # 80% remaining but 24h to reset (> 12h): not STARVED.
    times = _window(reset_in_seconds=24 * 3600.0, total_seconds=86_400.0, now_offset=0.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.20,
        now=times["now"],
    )
    assert result.pressure is not BurnPressure.STARVED


def test_assess_ahead_when_deviation_above_band() -> None:
    # 80% used at the 50% mark → deviation 0.30 > band 0.15 → AHEAD.
    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=1800.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.80,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.AHEAD
    # deviation = 0.30, score = min(1, 0.30/0.85) ≈ 0.353
    assert 0.0 < result.pressure_score <= 1.0


def test_assess_behind_when_deviation_below_negative_band() -> None:
    # 20% used at the 50% mark → deviation -0.30 < -band → BEHIND. Use a 24h
    # total so seconds_to_reset stays above the 12h STARVED threshold and
    # BEHIND gets a chance to fire.
    times = _window(reset_in_seconds=24 * 3600.0, total_seconds=24 * 3600.0, now_offset=12 * 3600.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.20,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.BEHIND
    assert -1.0 <= result.pressure_score < 0.0


def test_assess_on_track_when_within_band() -> None:
    # 50% used at the 50% mark → deviation 0 → ON_TRACK. 24h total so the
    # residual 50% remaining does not push the verdict into STARVED.
    times = _window(reset_in_seconds=24 * 3600.0, total_seconds=24 * 3600.0, now_offset=12 * 3600.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.50,
        now=times["now"],
    )
    assert result.pressure is BurnPressure.ON_TRACK
    assert result.pressure_score == 0.0


def test_assess_does_not_raise_when_now_precedes_window_started_at() -> None:
    """Clock skew is clamped to ``expected=0``, classification continues.

    24h window so the residual 100% remaining does not trip STARVED.
    """

    now = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    window_started_at = now + timedelta(hours=1)
    reset_at = window_started_at + timedelta(hours=24)
    result = assess(
        window_started_at=window_started_at,
        reset_at=reset_at,
        used_fraction=0.0,
        now=now,
    )
    assert result.expected_used_fraction == 0.0
    # 0% used at expected 0% → deviation 0 → ON_TRACK (24h horizon keeps us
    # out of STARVED territory).
    assert result.pressure is BurnPressure.ON_TRACK


def test_assess_does_not_raise_when_total_seconds_is_non_positive() -> None:
    """reset_at <= window_started_at falls into ``expected=0`` branch.

    Set ``reset_at`` 13 h into the future and ``window_started_at`` one hour
    after that, so the total is negative but ``now`` is comfortably before
    ``reset_at`` and the 13-hour horizon keeps us out of STARVED.
    """

    now = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    reset_at = now + timedelta(hours=13)
    window_started_at = reset_at + timedelta(hours=1)  # total = -1h
    result = assess(
        window_started_at=window_started_at,
        reset_at=reset_at,
        used_fraction=0.30,
        now=now,
    )
    # expected=0 (total <= 0) → deviation = 0.30 > band → AHEAD.
    assert result.expected_used_fraction == 0.0
    assert result.pressure is BurnPressure.AHEAD


@pytest.mark.parametrize(
    "naive_field",
    ["window_started_at", "reset_at", "now"],
)
def test_assess_raises_only_on_naive_datetime(naive_field: str) -> None:
    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=0.0)
    payload = {
        "window_started_at": times["window_started_at"],
        "reset_at": times["reset_at"],
        "now": times["now"],
    }
    payload[naive_field] = payload[naive_field].replace(tzinfo=None)
    with pytest.raises(ValueError, match="tz-aware"):
        assess(used_fraction=0.10, **payload)


def test_assess_returns_assessment_dataclass() -> None:
    times = _window(reset_in_seconds=3600.0, total_seconds=3600.0, now_offset=1800.0)
    result = assess(
        window_started_at=times["window_started_at"],
        reset_at=times["reset_at"],
        used_fraction=0.5,
        now=times["now"],
    )
    assert isinstance(result, BurnAssessment)


def test_infer_window_started_at_for_five_hour_and_weekly() -> None:
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    five_hour_start = infer_window_started_at(
        reset_at=reset_at, duration_seconds=QuotaWindowKind.FIVE_HOUR.duration_seconds()
    )
    assert five_hour_start == reset_at - timedelta(hours=5)

    weekly_start = infer_window_started_at(
        reset_at=reset_at,
        duration_seconds=QuotaWindowKind.WEEKLY.duration_seconds(),
    )
    assert weekly_start == reset_at - timedelta(days=7)


def test_infer_window_started_at_for_monthly_and_daily() -> None:
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    monthly_start = infer_window_started_at(
        reset_at=reset_at,
        duration_seconds=QuotaWindowKind.MONTHLY.duration_seconds(),
    )
    assert monthly_start == reset_at - timedelta(days=30)
    daily_start = infer_window_started_at(
        reset_at=reset_at,
        duration_seconds=QuotaWindowKind.DAILY.duration_seconds(),
    )
    assert daily_start == reset_at - timedelta(days=1)


def test_infer_window_started_at_raises_for_non_positive_duration() -> None:
    reset_at = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="duration_seconds"):
        infer_window_started_at(reset_at=reset_at, duration_seconds=0.0)
    with pytest.raises(ValueError, match="duration_seconds"):
        infer_window_started_at(reset_at=reset_at, duration_seconds=-1.0)


def test_infer_window_started_at_raises_for_naive_reset_at() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        infer_window_started_at(
            reset_at=datetime(2026, 9, 1, 13, 0, 0),  # noqa: DTZ001
            duration_seconds=QuotaWindowKind.WEEKLY.duration_seconds(),
        )


def test_rolling_hourly_cap_below_threshold_returns_false() -> None:
    assert rolling_hourly_cap(used_fraction=0.10, elapsed_seconds=3600.0) is False


def test_rolling_hourly_cap_at_threshold_returns_false() -> None:
    # 0.35/h threshold: exactly 0.35 in 1h is False (strict greater-than).
    assert rolling_hourly_cap(used_fraction=0.35, elapsed_seconds=3600.0) is False


def test_rolling_hourly_cap_above_threshold_returns_true() -> None:
    assert rolling_hourly_cap(used_fraction=0.40, elapsed_seconds=3600.0) is True


def test_rolling_hourly_cap_returns_false_when_used_fraction_is_none() -> None:
    assert rolling_hourly_cap(used_fraction=None, elapsed_seconds=3600.0) is False


def test_rolling_hourly_cap_returns_false_when_used_fraction_at_or_above_one() -> None:
    assert rolling_hourly_cap(used_fraction=1.0, elapsed_seconds=3600.0) is False
    assert rolling_hourly_cap(used_fraction=1.5, elapsed_seconds=3600.0) is False


def test_rolling_hourly_cap_returns_false_when_elapsed_is_non_positive() -> None:
    assert rolling_hourly_cap(used_fraction=0.5, elapsed_seconds=0.0) is False
    assert rolling_hourly_cap(used_fraction=0.5, elapsed_seconds=-1.0) is False


def test_quota_window_kind_duration_seconds_lookup() -> None:
    assert QuotaWindowKind.FIVE_HOUR.duration_seconds() == 5 * 3600.0
    assert QuotaWindowKind.WEEKLY.duration_seconds() == 7 * 24 * 3600.0
    assert QuotaWindowKind.MONTHLY.duration_seconds() == 30 * 24 * 3600.0
    assert QuotaWindowKind.DAILY.duration_seconds() == 24 * 3600.0
    assert QuotaWindowKind.UNKNOWN.duration_seconds() is None
    assert QuotaWindowKind.CUSTOM.duration_seconds() is None