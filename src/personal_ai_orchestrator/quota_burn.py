"""Quota burn assessment: pure functions over a single observed window.

The dashboard has to answer two questions a single ``remaining_fraction``
cannot:

- *how fast am I using this quota* — the deviation between what the ideal
  line says I should have used by now and what the collector actually
  observed, normalised to the band's tolerance;
- *is there still time to change course* — whether the window is on the
  verge of resetting while a lot of remaining quota is still unused, in
  which case the orchestrator would rather dispatch a few more tasks
  than let it expire.

Both questions are scoped to one window of one pool, fed a single
``now``. There is no scheduler, no provider I/O, and no journal. Every
piece of state the functions read is passed in by the caller, so a test
can freeze ``now`` and read the answer off the result without mocking
anything.

The truth table is the single authoritative reference for the
:class:`BurnPressure` enum, and the test suite asserts each row
independently. New rows belong here, not in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class BurnPressure(StrEnum):
    """Classification of one window's burn curve at ``now``.

    The order matters: ``assess`` returns the first matching pressure in
    declaration order, so a single ``used_fraction=None`` short-circuits
    to ``UNMETERED`` before any of the time-based or fraction-based rows
    fire. Reordering the rows changes the verdict.
    """

    UNMETERED = "UNMETERED"
    STALE = "STALE"
    EXHAUSTED = "EXHAUSTED"
    STARVED = "STARVED"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    ON_TRACK = "ON_TRACK"


@dataclass(frozen=True)
class BurnAssessment:
    """Result of one ``assess`` call.

    The five numerical fields are ``float | None``: ``UNMETERED`` returns
    ``None`` for every one of them (the window was never read), while
    ``STALE`` keeps the real figures so the UI can still draw the bar
    it already has. ``pressure_score`` is always a float — ``UNMETERED``
    is ``0.0`` so callers can sum without checking for ``None`` first.
    """

    expected_used_fraction: float | None
    actual_used_fraction: float | None
    deviation: float | None
    remaining_fraction: float | None
    seconds_to_reset: float | None
    pressure: BurnPressure
    pressure_score: float


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a tz-aware datetime in UTC")


def _coerce_used_fraction(used_fraction: float | None) -> float | None:
    """Clamp ``used_fraction`` into ``[0, 1]`` and pass ``None`` through.

    The orchestrator refuses to raise on a collector-reported ``1.02`` or
    ``-0.01`` — those happen on rounding boundaries. Clamping them lets
    the truth table work without leaking provider noise into the UI.
    """

    if used_fraction is None:
        return None
    if used_fraction <= 0.0:
        return 0.0
    if used_fraction >= 1.0:
        return 1.0
    return used_fraction


def infer_window_started_at(
    *, reset_at: datetime, duration_seconds: float
) -> datetime:
    """Infer ``reset_at - duration_seconds`` for windows the collector omits.

    The kind → duration lookup lives in :mod:`model_registry`; callers
    pass the resolved seconds in. Keeping this module free of any
    project-internal import lets it sit at the bottom of the dependency
    DAG (``quota_burn ← model_registry ← dispatch_recommender``) without
    a cycle.

    Raises ``ValueError`` when ``duration_seconds <= 0`` — a non-positive
    duration means the caller forgot to short-circuit on
    ``kind.duration_seconds() is None``. The error mentions the field
    name so the call site is obvious in a stack trace.
    """

    _require_aware(reset_at, field_name="reset_at")
    if duration_seconds <= 0.0:
        raise ValueError(
            "duration_seconds must be > 0; check kind.duration_seconds() first"
        )
    return reset_at - timedelta(seconds=duration_seconds)


def assess(
    *,
    window_started_at: datetime,
    reset_at: datetime,
    used_fraction: float | None,
    now: datetime,
    band: float = 0.15,
    starved_remaining: float = 0.40,
    starved_hours: float = 12.0,
) -> BurnAssessment:
    """Classify one window's burn curve at ``now``.

    Truth table — first row that matches wins:

    ============  ============================================  ===========
    pressure      condition                                     score
    ============  ============================================  ===========
    UNMETERED     ``used_fraction is None``                     0.0
    STALE         ``now >= reset_at``                           0.0
    EXHAUSTED     ``actual_used >= 1.0``                        +1.0
    STARVED       ``remaining > starved_remaining``             −1.0
                  ``and seconds_to_reset < starved_hours``
    AHEAD         ``deviation > +band``                         ``min(1, deviation/(1-band))``
    BEHIND        ``deviation < -band``                         ``max(−1, deviation/(1-band))``
    ON_TRACK      everything else                               ``deviation/band * 0.25``
    ============  ============================================  ===========

    Boundary semantics:

    - Only **naive datetimes** raise ``ValueError``. Every other ill-shaped
      input — ``used_fraction`` outside ``[0, 1]``, ``total_seconds <= 0``,
      ``now < window_started_at`` (clock skew), ``now >= reset_at`` —
      is *clamped / coerced* and the truth table proceeds. A quota
      handler that crashes on a 3-second-stale reset snapshot would be
      worse than the snapshot itself.
    - ``expected_used_fraction`` is ``clamp(elapsed/total, 0, 1)``; on a
      negative total it is ``0`` so classification continues.
    - ``seconds_to_reset`` is clamped to ``0`` for ``STALE``; everywhere
      else it is the true ``reset_at - now``.
    - For ``UNMETERED`` the five numerical fields are ``None`` (no
      ``0.0``/``1.0`` impostors).
    - For ``STALE`` the five numerical fields are the real values the
      collector returned, so the bar can still draw on cached data; the
      pressure itself just refuses to trigger ``STARVED`` on an
      expired window.
    """

    _require_aware(window_started_at, field_name="window_started_at")
    _require_aware(reset_at, field_name="reset_at")
    _require_aware(now, field_name="now")

    actual_used = _coerce_used_fraction(used_fraction)

    # UNMETERED short-circuits before any of the time math runs, so the
    # other fields are legitimately absent rather than zeroed.
    if actual_used is None:
        return BurnAssessment(
            expected_used_fraction=None,
            actual_used_fraction=None,
            deviation=None,
            remaining_fraction=None,
            seconds_to_reset=None,
            pressure=BurnPressure.UNMETERED,
            pressure_score=0.0,
        )

    total_seconds = (reset_at - window_started_at).total_seconds()
    elapsed_seconds = (now - window_started_at).total_seconds()
    if elapsed_seconds < 0.0:
        elapsed_seconds = 0.0  # clock skew → assume the window just opened

    expected_used = (
        0.0 if total_seconds <= 0.0 else max(0.0, min(1.0, elapsed_seconds / total_seconds))
    )
    deviation = actual_used - expected_used
    remaining = 1.0 - actual_used
    raw_seconds_to_reset = (reset_at - now).total_seconds()

    if now >= reset_at:
        # STALE preserves the real numbers but refuses to feed STARVED
        # — the cached data describes a window that has already ended.
        return BurnAssessment(
            expected_used_fraction=expected_used,
            actual_used_fraction=actual_used,
            deviation=deviation,
            remaining_fraction=remaining,
            seconds_to_reset=0.0,
            pressure=BurnPressure.STALE,
            pressure_score=0.0,
        )

    seconds_to_reset = max(0.0, raw_seconds_to_reset)

    if actual_used >= 1.0:
        return BurnAssessment(
            expected_used_fraction=expected_used,
            actual_used_fraction=actual_used,
            deviation=deviation,
            remaining_fraction=remaining,
            seconds_to_reset=seconds_to_reset,
            pressure=BurnPressure.EXHAUSTED,
            pressure_score=1.0,
        )

    if remaining > starved_remaining and seconds_to_reset < starved_hours * 3600.0:
        return BurnAssessment(
            expected_used_fraction=expected_used,
            actual_used_fraction=actual_used,
            deviation=deviation,
            remaining_fraction=remaining,
            seconds_to_reset=seconds_to_reset,
            pressure=BurnPressure.STARVED,
            pressure_score=-1.0,
        )

    if deviation > band:
        score = min(1.0, deviation / max(1.0 - band, 1e-9))
        return BurnAssessment(
            expected_used_fraction=expected_used,
            actual_used_fraction=actual_used,
            deviation=deviation,
            remaining_fraction=remaining,
            seconds_to_reset=seconds_to_reset,
            pressure=BurnPressure.AHEAD,
            pressure_score=score,
        )

    if deviation < -band:
        score = max(-1.0, deviation / max(1.0 - band, 1e-9))
        return BurnAssessment(
            expected_used_fraction=expected_used,
            actual_used_fraction=actual_used,
            deviation=deviation,
            remaining_fraction=remaining,
            seconds_to_reset=seconds_to_reset,
            pressure=BurnPressure.BEHIND,
            pressure_score=score,
        )

    score = (deviation / band) * 0.25
    return BurnAssessment(
        expected_used_fraction=expected_used,
        actual_used_fraction=actual_used,
        deviation=deviation,
        remaining_fraction=remaining,
        seconds_to_reset=seconds_to_reset,
        pressure=BurnPressure.ON_TRACK,
        pressure_score=score,
    )


def rolling_hourly_cap(
    *,
    used_fraction: float | None,
    elapsed_seconds: float,
    max_fraction_per_hour: float = 0.35,
) -> bool:
    """Return ``True`` if the current burn rate has crossed the hourly cap.

    Independent of the window's reset horizon: the cap is a per-hour
    sliding rule of thumb used by the 5-hour window admission path, not
    the weekly pressure gauge. ``used_fraction=None`` returns ``False``;
    callers cannot rely on the cap for a window they never measured.
    """

    if used_fraction is None or used_fraction >= 1.0:
        return False
    if elapsed_seconds <= 0.0:
        return False
    cap_total = max_fraction_per_hour * (elapsed_seconds / 3600.0)
    return used_fraction > cap_total


__all__ = [
    "BurnAssessment",
    "BurnPressure",
    "assess",
    "infer_window_started_at",
    "rolling_hourly_cap",
]