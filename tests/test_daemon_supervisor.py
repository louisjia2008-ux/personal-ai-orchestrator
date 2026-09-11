"""``DaemonSupervisor`` unit tests.

These tests run against an injected fake clock and a fake audit store
so the supervisor stays deterministic and free of subprocess / SQLite
/ I/O concerns. The integration test
(``tests/test_daemon_tick_integration.py``) covers the real daemon
boot + ``/v1/health`` round-trip.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from personal_ai_orchestrator.daemon_supervisor import (
    BACKOFF_EVERY_N_TICKS,
    CONSECUTIVE_FAILURE_THRESHOLD,
    HEARTBEAT_STEP_NAME,
    SUPERVISOR_STEP_BACKOFF,
    SUPERVISOR_STEP_FAILED,
    SUPERVISOR_STEP_RECOVERED,
    DaemonSupervisor,
    SupervisorStepSnapshot,
    build_default_supervisor,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore


class FakeClock:
    """Advanceable tz-aware UTC clock for deterministic supervisor tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now: datetime = start or datetime(2026, 9, 5, tzinfo=UTC)
        self.calls: int = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class NaiveClock:
    """Returns a tz-naive datetime — used to prove the supervisor rejects it."""

    def __call__(self) -> datetime:
        return datetime(2026, 9, 5)


class FakeAuditStore:
    """Captures ``record_system_event`` calls without touching SQLite."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record_system_event(self, event_type: str, payload: Any) -> None:
        self.events.append((event_type, dict(payload)))


def test_register_validates_step_name() -> None:
    clock = FakeClock()
    supervisor = DaemonSupervisor(interval_seconds=0.05, clock=clock)
    with pytest.raises(ValueError):
        supervisor.register("", lambda _now: None)
    with pytest.raises(ValueError):
        supervisor.register("a/b", lambda _now: None)
    with pytest.raises(ValueError):
        supervisor.register("a\nb", lambda _now: None)


def test_run_iterates_registered_steps_each_tick() -> None:
    """Happy path: every registered step runs on every tick."""

    clock = FakeClock()
    supervisor = DaemonSupervisor(interval_seconds=0.01, clock=clock)
    calls: list[tuple[str, datetime]] = []

    def _record_a(now: datetime) -> None:
        calls.append(("a", now))

    def _record_b(now: datetime) -> None:
        calls.append(("b", now))

    supervisor.register("a", _record_a)
    supervisor.register("b", _record_b)

    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,), daemon=True)
    thread.start()
    # Advance the clock to force the loop to fire on the next sleep wakeup.
    target_calls = 6
    while len(calls) < target_calls:
        clock.advance(0.02)
        time.sleep(0.02)
        if clock.calls > 100:
            break  # safety: avoid an infinite loop if a regression breaks the tick
    stop.set()
    thread.join(timeout=2.0)

    assert len(calls) >= target_calls, f"only got {len(calls)} calls"
    assert [name for name, _ in calls[:4]] == ["a", "b", "a", "b"]
    snapshot = supervisor.snapshot()
    assert snapshot.last_tick_at is not None
    assert {step.name for step in snapshot.steps} == {"a", "b"}


def test_step_exception_does_not_skip_subsequent_steps() -> None:
    """An exception in step A must not stop step C from running."""

    clock = FakeClock()
    supervisor = DaemonSupervisor(interval_seconds=0.01, clock=clock)
    seen: list[str] = []

    def _boom(_now: datetime) -> None:
        seen.append("boom")
        raise RuntimeError("deliberate failure")

    def _good(_now: datetime) -> None:
        seen.append("good")

    supervisor.register("boom", _boom)
    supervisor.register("good", _good)

    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,), daemon=True)
    thread.start()
    target = 4  # 2 ticks * 2 steps
    while len(seen) < target:
        clock.advance(0.02)
        time.sleep(0.02)
        if clock.calls > 100:
            break
    stop.set()
    thread.join(timeout=2.0)

    assert seen.count("good") >= 2
    assert seen.count("boom") >= 2


def test_step_exception_writes_failed_audit_event() -> None:
    """Every failed step call writes one ``SUPERVISOR_STEP_FAILED`` audit."""

    clock = FakeClock()
    audit = FakeAuditStore()
    supervisor = DaemonSupervisor(
        interval_seconds=0.01, clock=clock, audit_store=audit
    )

    def _boom(_now: datetime) -> None:
        raise ValueError("nope")

    supervisor.register("boom", _boom)

    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,), daemon=True)
    thread.start()
    # Let the loop run for a few ticks.
    for _ in range(20):
        clock.advance(0.02)
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2.0)

    failed = [e for e in audit.events if e[0] == SUPERVISOR_STEP_FAILED]
    assert failed, "expected at least one SUPERVISOR_STEP_FAILED audit"
    assert all(e[1]["step"] == "boom" for e in failed)
    assert all(e[1]["exc_type"] == "ValueError" for e in failed)


def test_consecutive_failures_trigger_backoff_and_recovery_audit() -> None:
    """Three consecutive failures enter backoff; recovery writes audit.

    Drive ``_run_tick`` directly (no real-time loop) so the backoff /
    recovery audit chain is deterministic regardless of CI scheduler.
    """

    clock = FakeClock()
    audit = FakeAuditStore()
    supervisor = DaemonSupervisor(
        interval_seconds=0.01, clock=clock, audit_store=audit
    )

    failures_left = [CONSECUTIVE_FAILURE_THRESHOLD + 2]

    def _flaky(now: datetime) -> None:
        if failures_left[0] > 0:
            failures_left[0] -= 1
            raise RuntimeError("still flaky")

    supervisor.register("flaky", _flaky)

    # Tick 1, 2, 3: failures accumulate, 3rd triggers BACKOFF audit.
    # Tick 4..15: throttled (tslr 1..12, only 12 fires).
    # Tick 16: 4th fire, 5th failure (failures_left 2→1).
    # Tick 28: 5th fire, last failure (failures_left 1→0).
    # Tick 40: 6th fire, step succeeds → RECOVERED audit.
    total_ticks = 40 + 1
    for _ in range(total_ticks):
        supervisor._run_tick(clock())  # noqa: SLF001 — internal contract under test

    failed = [e for e in audit.events if e[0] == SUPERVISOR_STEP_FAILED]
    assert len(failed) >= CONSECUTIVE_FAILURE_THRESHOLD
    backoff = [e for e in audit.events if e[0] == SUPERVISOR_STEP_BACKOFF]
    assert backoff, "expected SUPERVISOR_STEP_BACKOFF audit after threshold"
    recovered = [e for e in audit.events if e[0] == SUPERVISOR_STEP_RECOVERED]
    assert recovered, "expected SUPERVISOR_STEP_RECOVERED audit after success"
    snapshot = supervisor.snapshot()
    flaky = next(s for s in snapshot.steps if s.name == "flaky")
    assert flaky.consecutive_failures == 0
    assert flaky.in_backoff is False


def test_backoff_actually_throttles_when_always_failing() -> None:
    """When a step keeps failing, it fires only every N ticks in backoff."""

    clock = FakeClock()
    supervisor = DaemonSupervisor(interval_seconds=0.01, clock=clock)
    calls: list[datetime] = []

    def _always_boom(now: datetime) -> None:
        calls.append(now)
        raise RuntimeError("never recovers")

    supervisor.register("always_boom", _always_boom)

    # 3 initial fires + 3 throttled fires = 6 total in 3 + 12 + 12 + 12 = 39 ticks.
    total_ticks = 3 + BACKOFF_EVERY_N_TICKS * 3
    for _ in range(total_ticks):
        supervisor._run_tick(clock())  # noqa: SLF001

    snapshot = supervisor.snapshot()
    state = next(s for s in snapshot.steps if s.name == "always_boom")
    assert state.in_backoff is True
    # Expect: initial 3 + 3 throttled fires = 6 calls.
    assert len(calls) == CONSECUTIVE_FAILURE_THRESHOLD + 3


def test_replacement_registration_replaces_step() -> None:
    """Re-registering a step name replaces the body cleanly."""

    clock = FakeClock()
    audit = FakeAuditStore()
    supervisor = DaemonSupervisor(interval_seconds=0.01, clock=clock, audit_store=audit)

    def _v1(_now: datetime) -> None:
        raise RuntimeError("old body")

    def _v2(_now: datetime) -> None:
        return None

    supervisor.register("step", _v1)
    supervisor._run_tick(clock())  # noqa: SLF001
    supervisor.register("step", _v2)
    supervisor._run_tick(clock())  # noqa: SLF001 — runs v2 cleanly

    snapshot = supervisor.snapshot()
    step = next(s for s in snapshot.steps if s.name == "step")
    assert step.consecutive_failures == 0
    assert step.in_backoff is False
    assert len(audit.events) == 1
    assert audit.events[0][0] == SUPERVISOR_STEP_FAILED


def test_warn_threshold_logs_slow_step_but_keeps_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A step slower than ``warn_threshold_seconds`` logs and continues."""

    supervisor = DaemonSupervisor(
        interval_seconds=0.01,
        clock=FakeClock(),
        warn_threshold_seconds=0.005,
    )

    def _slow(_now: datetime) -> None:
        time.sleep(0.05)

    supervisor.register("slow", _slow)

    stop = threading.Event()
    with caplog.at_level(logging.WARNING, logger="personal_ai_orchestrator.daemon_supervisor"):
        thread = threading.Thread(target=supervisor.run, args=(stop,), daemon=True)
        thread.start()
        time.sleep(0.3)
        stop.set()
        thread.join(timeout=2.0)

    warnings = [r for r in caplog.records if "took" in r.getMessage()]
    assert warnings, "expected a slow-step warning"
    snapshot = supervisor.snapshot()
    assert any(s.name == "slow" for s in snapshot.steps)


def test_naive_clock_is_rejected_at_construction() -> None:
    """A naive clock fails the supervisor at ``__init__``.

    Silently skipping ticks would leave the daemon alive but never
    advancing ``last_tick_at`` — the worst "false alive" failure mode.
    We surface the bad clock before the daemon thread starts.
    """

    with pytest.raises(ValueError, match="tz-aware"):
        DaemonSupervisor(
            interval_seconds=0.01,
            clock=NaiveClock(),  # type: ignore[arg-type]
        )


def test_naive_clock_is_rejected_at_runtime() -> None:
    """A clock that turns naive mid-run raises out of ``run()``.

    The construction-time check is the primary guard; this is the
    defensive belt-and-braces check inside ``run()`` itself, in case
    someone hot-swaps the clock for one that loses tzinfo.
    """

    good_calls = {"n": 0}

    def flaky_clock() -> datetime:
        good_calls["n"] += 1
        if good_calls["n"] == 1:
            return datetime.now(UTC)
        return datetime.now()  # naive, second call

    supervisor = DaemonSupervisor(interval_seconds=0.01, clock=flaky_clock)
    supervisor.register("noop", lambda _now: None)

    stop = threading.Event()
    with pytest.raises(ValueError, match="tz-aware"):
        supervisor.run(stop)


def test_snapshot_is_immutable_and_exposes_required_view() -> None:
    """``snapshot()`` returns frozen dataclasses ready for ``/v1/health``."""

    supervisor = DaemonSupervisor(interval_seconds=0.05, clock=FakeClock())
    supervisor.register("a", lambda _now: None)
    snapshot = supervisor.snapshot()
    assert isinstance(snapshot.steps[0], SupervisorStepSnapshot)
    # Frozen dataclass raises on assignment.
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        snapshot.steps[0].consecutive_failures = 99  # type: ignore[misc]


def test_default_supervisor_registers_heartbeat() -> None:
    """``build_default_supervisor`` registers exactly one heartbeat step."""

    supervisor = build_default_supervisor(interval_seconds=0.05)
    snapshot = supervisor.snapshot()
    assert [s.name for s in snapshot.steps] == [HEARTBEAT_STEP_NAME]
    assert supervisor.interval_seconds == 0.05


def test_real_audit_store_accepts_record_system_event(tmp_path: Path) -> None:
    """``SafetyKernelStore.record_system_event`` is the public audit entry."""

    db = tmp_path / "state.sqlite3"
    store = SafetyKernelStore(db)
    try:
        store.record_system_event("SUPERVISOR_TEST", {"answer": 42})
        store.audit_events(task_id="__missing__")
    finally:
        store.close()
    # ``audit_events(task_id)`` scopes by task_id and the system event
    # has ``task_id = NULL``; it is intentionally NOT returned by the
    # task-scoped reader. Verify the row landed by re-opening.
    store = SafetyKernelStore(db)
    try:
        count = store.connection.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE event_type=?",
            ("SUPERVISOR_TEST",),
        ).fetchone()
    finally:
        store.close()
    assert count["n"] == 1