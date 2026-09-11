"""Daemon tick host: a deterministic periodic step registry.

The supervisor is the heartbeat for any host-owned periodic work. It runs
in its own thread and drives registered callables (``steps``) on a fixed
interval. The contract is deliberately small:

- each step gets the current tz-aware ``datetime`` from the injected
  ``clock``; the supervisor itself never reads wall-clock, so tests can
  freeze and advance time deterministically;
- each step is isolated: an exception in step ``B`` does not skip step
  ``C`` and does not stop the loop;
- a step that raises three times in a row is throttled to once every
  ``BACKOFF_EVERY_N_TICKS`` (=12) ticks until it succeeds once;
- a step whose runtime exceeds the supervisor ``interval`` triggers a
  warning log but never an audit event (warnings are not durability);
- the only events written to ``SafetyKernelStore.audit_events`` are
  ``SUPERVISOR_STEP_FAILED`` (each failure), ``SUPERVISOR_STEP_BACKOFF``
  (entry into throttled mode) and ``SUPERVISOR_STEP_RECOVERED`` (exit
  from throttled mode). Heartbeats do not write audit by design;
- the heartbeat step is registered automatically and updates an
  in-memory ``DaemonSupervisorSnapshot`` that ``/v1/health`` reads. No
  SQLite touch on the hot path;
- the injected ``clock`` must return a tz-aware ``datetime`` in UTC;
  the supervisor validates it **at construction** and re-validates on
  every tick. A naive clock is fatal in both places — silently
  skipping ticks would leave the daemon alive but never tick, which
  is the worst "false alive" failure mode for the dashboard.

WP0 keeps a single ``heartbeat`` step registered; WP1 will add the
quota-pressure + backoff + supervised-auto periodic steps behind the
same registry.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from personal_ai_orchestrator.safety_kernel import SafetyKernelStore

LOG = logging.getLogger(__name__)

#: Audit event names the supervisor emits. Heartbeat does not appear here
#: on purpose — every 5 s audit on every daemon is pollution, not truth.
SUPERVISOR_STEP_FAILED = "SUPERVISOR_STEP_FAILED"
SUPERVISOR_STEP_BACKOFF = "SUPERVISOR_STEP_BACKOFF"
SUPERVISOR_STEP_RECOVERED = "SUPERVISOR_STEP_RECOVERED"

#: A step that has failed ``CONSECUTIVE_FAILURE_THRESHOLD`` times in a row
#: enters backoff and runs only every ``BACKOFF_EVERY_N_TICKS`` ticks.
CONSECUTIVE_FAILURE_THRESHOLD = 3
BACKOFF_EVERY_N_TICKS = 12

#: Public step name the heartbeat registers under. ``/v1/health``
#: surfaces this so the dashboard can prove the daemon is alive without
#: a separate readiness probe.
HEARTBEAT_STEP_NAME = "heartbeat"


def _require_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a tz-aware datetime in UTC")
    return now.astimezone(UTC)


@dataclass(frozen=True)
class SupervisorStepSnapshot:
    """Read-only per-step status the control plane can surface."""

    name: str
    last_run_at: datetime | None
    last_duration_ms: float | None
    consecutive_failures: int
    in_backoff: bool


@dataclass(frozen=True)
class DaemonSupervisorSnapshot:
    """Aggregate view the supervisor exposes to ``/v1/health``.

    ``last_tick_at`` is the timestamp at which the supervisor last *ran*
    its loop iteration, regardless of which steps fired. ``None`` means
    the supervisor has not ticked yet (e.g. between daemon boot and the
    first interval elapse).
    """

    last_tick_at: datetime | None
    interval_seconds: float
    steps: tuple[SupervisorStepSnapshot, ...] = ()


StepFn = Callable[[datetime], None]


def _heartbeat(now: datetime) -> None:
    """Default step registered automatically.

    Does nothing besides prove the loop is alive; the heartbeat
    timestamp is the supervisor's own ``last_tick_at``. The step body
    is intentionally trivial — the supervisor contract is "every step
    is a callable; nothing more".
    """
    return


@dataclass
class _StepState:
    name: str
    fn: StepFn
    consecutive_failures: int = 0
    total_failures: int = 0
    in_backoff: bool = False
    last_run_at: datetime | None = None
    last_duration_ms: float | None = None
    ticks_since_last_run: int = 0


class DaemonSupervisor:
    """Per-step isolation registry with deterministic timing.

    The supervisor does not own any state besides what is needed to
    answer ``/v1/health``. Step bodies must own their own durable
    persistence (audit, journal, snapshot, etc.).
    """

    def __init__(
        self,
        *,
        interval_seconds: float = 5.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        audit_store: SafetyKernelStore | None = None,
        warn_threshold_seconds: float | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        # Fail-fast on a naive clock at construction. A supervisor that
        # silently drops every tick because its clock is broken leaves
        # the daemon alive but never advancing ``last_tick_at`` — that
        # is the worst "false alive" failure mode the dashboard can
        # surface, so we reject it before the daemon thread starts.
        _require_utc(clock())
        self._interval = float(interval_seconds)
        self._clock: Callable[[], datetime] = clock
        self._audit_store = audit_store
        self._warn_threshold = warn_threshold_seconds
        self._lock = threading.Lock()
        self._steps: dict[str, _StepState] = {}
        self._last_tick_at: datetime | None = None

    # -- registration ----------------------------------------------------

    def register(self, name: str, step: StepFn) -> None:
        """Register a step. Re-registering replaces the previous callable.

        Registering during ``run()`` is allowed but the new step will only
        start ticking on the next loop iteration; the supervisor never
        holds the step body across an in-flight call.
        """
        if not name or any(c in name for c in ("/", "\n", "\r")):
            raise ValueError("step name must be non-empty and shell-safe")
        with self._lock:
            self._steps[name] = _StepState(name=name, fn=step)

    # -- run ---------------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        """Drive registered steps every ``interval_seconds`` until ``stop``.

        The loop sleeps in bounded slices so the supervisor can stop
        within ``interval_seconds`` rather than waiting up to a full
        hour. The clock is read once per tick and threaded through every
        step call so deterministic tests can freeze and advance time.
        """
        while not stop.is_set():
            tick_started_monotonic = time.monotonic()
            # Defensive: ``__init__`` already validated ``clock``, but a
            # clock that later flips to naive (e.g. someone replaced
            # the default with a wall-clock that loses tz) must surface
            # as a daemon failure rather than a silent tick skip.
            tick_at = _require_utc(self._clock())
            self._run_tick(tick_at)
            self._sleep_remaining(stop, tick_started_monotonic)

    def _sleep_remaining(self, stop: threading.Event, tick_started: float) -> None:
        elapsed = time.monotonic() - tick_started
        remaining = self._interval - elapsed
        if remaining <= 0:
            return
        # Sleep in small slices so SIGINT/SIGTERM honours ``stop`` quickly.
        deadline = time.monotonic() + remaining
        while not stop.is_set():
            wait = deadline - time.monotonic()
            if wait <= 0:
                return
            stop.wait(timeout=min(wait, 0.5))

    def _run_tick(self, tick_at: datetime) -> None:
        with self._lock:
            self._last_tick_at = tick_at
            steps = list(self._steps.values())
        for state in steps:
            if state.in_backoff:
                state.ticks_since_last_run += 1
                if state.ticks_since_last_run < BACKOFF_EVERY_N_TICKS:
                    continue
                state.ticks_since_last_run = 0
            self._invoke_step(state, tick_at)

    def _invoke_step(self, state: _StepState, now: datetime) -> None:
        start = time.monotonic()
        try:
            state.fn(now)
        except Exception as exc:  # noqa: BLE001 — surface, do not propagate
            duration_ms = (time.monotonic() - start) * 1000.0
            state.last_run_at = now
            state.last_duration_ms = duration_ms
            state.consecutive_failures += 1
            state.total_failures += 1
            if self._audit_store is not None:
                self._audit_store.record_system_event(
                    SUPERVISOR_STEP_FAILED,
                    {
                        "step": state.name,
                        "exc_type": type(exc).__name__,
                        "message": str(exc)[:512],
                        "consecutive_failures": state.consecutive_failures,
                    },
                )
            if (
                state.consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD
                and not state.in_backoff
            ):
                state.in_backoff = True
                state.ticks_since_last_run = 0
                if self._audit_store is not None:
                    self._audit_store.record_system_event(
                        SUPERVISOR_STEP_BACKOFF,
                        {
                            "step": state.name,
                            "consecutive_failures": state.consecutive_failures,
                            "every_n_ticks": BACKOFF_EVERY_N_TICKS,
                        },
                    )
            return
        duration_ms = (time.monotonic() - start) * 1000.0
        state.last_run_at = now
        state.last_duration_ms = duration_ms
        if (
            state.in_backoff
            or state.consecutive_failures > 0
        ):
            if self._audit_store is not None:
                self._audit_store.record_system_event(
                    SUPERVISOR_STEP_RECOVERED,
                    {
                        "step": state.name,
                        "total_failures": state.total_failures,
                    },
                )
        state.consecutive_failures = 0
        state.in_backoff = False
        state.ticks_since_last_run = 0
        if (
            self._warn_threshold is not None
            and duration_ms / 1000.0 > self._warn_threshold
        ):
            LOG.warning(
                "supervisor step %s took %.0fms (threshold %.0fms)",
                state.name,
                duration_ms,
                self._warn_threshold * 1000.0,
            )

    # -- read-only snapshot ---------------------------------------------

    def snapshot(self) -> DaemonSupervisorSnapshot:
        with self._lock:
            steps = tuple(
                SupervisorStepSnapshot(
                    name=state.name,
                    last_run_at=state.last_run_at,
                    last_duration_ms=state.last_duration_ms,
                    consecutive_failures=state.consecutive_failures,
                    in_backoff=state.in_backoff,
                )
                for state in self._steps.values()
            )
            return DaemonSupervisorSnapshot(
                last_tick_at=self._last_tick_at,
                interval_seconds=self._interval,
                steps=steps,
            )

    # -- accessors used by tests ---------------------------------------

    @property
    def interval_seconds(self) -> float:
        return self._interval

def build_default_supervisor(
    *,
    interval_seconds: float = 5.0,
    clock: Callable[[], datetime] | None = None,
    audit_store: SafetyKernelStore | None = None,
    supervised_auto_step: StepFn | None = None,
) -> DaemonSupervisor:
    """Build the default supervisor: heartbeat (+ optional auto tick).

    WP5a-2 adds the optional ``supervised_auto_step`` registration under
    the ``supervised-auto`` step name. The daemon wires it ONLY for the
    non-control-only runtime — a ``--control-only`` daemon must never
    execute autonomous steps (§19), so its caller simply omits the
    parameter (the product daemon's ``--control-only`` path passes
    nothing, which registers heartbeat only).
    """

    supervisor = DaemonSupervisor(
        interval_seconds=interval_seconds,
        clock=clock or (lambda: datetime.now(UTC)),
        audit_store=audit_store,
        warn_threshold_seconds=interval_seconds,
    )
    supervisor.register(HEARTBEAT_STEP_NAME, _heartbeat)
    if supervised_auto_step is not None:
        from personal_ai_orchestrator.supervised_auto_step import (
            SUPERVISED_AUTO_STEP_NAME,
        )

        supervisor.register(SUPERVISED_AUTO_STEP_NAME, supervised_auto_step)
    return supervisor


__all__ = [
    "BACKOFF_EVERY_N_TICKS",
    "CONSECUTIVE_FAILURE_THRESHOLD",
    "DaemonSupervisor",
    "DaemonSupervisorSnapshot",
    "HEARTBEAT_STEP_NAME",
    "SUPERVISOR_STEP_BACKOFF",
    "SUPERVISOR_STEP_FAILED",
    "SUPERVISOR_STEP_RECOVERED",
    "SupervisorStepSnapshot",
    "build_default_supervisor",
]