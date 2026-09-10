"""Multi-cycle dispatch reconciliation regressions (review round 2).

The reconciliation must correlate terminal SUPERVISED_AUTO dispatch
rows to the task's CURRENT auto cycle only, and must decide "this
dispatch produced a run" via the exact ``run-{dispatch_id}`` row —
never via a task-scoped historical run lookup. Deterministic setup:
dispatch rows, run rows and lifecycle moves are written directly; the
private reconciliation step is invoked in isolation so no other tick
stage can mask the verdict.
"""

from datetime import timedelta
from pathlib import Path

from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.supervised_auto_step import (
    supervised_auto_dispatch_request_id,
)
from tests.test_supervised_auto_step import NOW, _env, _plan

_TICK_LATER = NOW + timedelta(seconds=301)


def _blocked_dispatch(
    store, task, *, decision_id: str, failure_code: str = "QUOTA_EXHAUSTED"
) -> str:
    """Write a terminal pre-worker SUPERVISED_AUTO dispatch row."""

    request_id = supervised_auto_dispatch_request_id(decision_id)
    dispatch_id = f"owner-dispatch-{request_id}"
    store.reserve_owner_dispatch(
        dispatch_id=dispatch_id,
        request_id=request_id,
        task_id=task.task_id,
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )
    store.mark_owner_dispatch_blocked(
        request_id,
        failure_code=failure_code,
        failure_reason="test admission failure",
    )
    return dispatch_id


def _insert_run(store, task_id: str, run_id: str) -> None:
    """Write a finished run row (task-scoped history)."""

    store.connection.execute(
        "INSERT INTO runs (run_id, task_id, worker_id, pid, status, started_at,"
        " finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            task_id,
            "m3-sub",
            4711,
            "COMPLETED",
            NOW.isoformat(),
            (NOW + timedelta(seconds=60)).isoformat(),
        ),
    )
    store.connection.commit()


def _abort_events(store) -> list:
    return [
        e
        for e in store.audit_events("task-1")
        if e["event_type"] == "AUTO_ABORTED"
    ]


def test_old_run_does_not_hide_current_preworker_failure(tmp_path: Path) -> None:
    """§11/§14: a task-level historical run cannot mask the CURRENT
    dispatch's pre-worker failure — the verdict is dispatch-scoped."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    # Cycle 1: the dispatch truly launched a worker (exact run row),
    # then the lifecycle legitimately closed back to READY.
    _plan(env)
    task = env.store.get_task("task-1")
    cycle1_decision = task.auto_decision_id
    dispatch1_id = _blocked_dispatch(env.store, task, decision_id=cycle1_decision)
    _insert_run(env.store, "task-1", f"run-{dispatch1_id}")
    env.store.abort_auto_lifecycle("task-1", reason="cycle_1_closed")
    assert env.store.get_task("task-1").state is TaskState.READY

    # Cycle 2: new decision, new pending, new pre-worker BLOCKED
    # dispatch with NO run row. The cycle-1 run row is still there.
    env.tick(_TICK_LATER)
    task2 = env.store.get_task("task-1")
    assert task2.state is TaskState.AUTO_GRACE
    cycle2_decision = task2.auto_decision_id
    assert cycle2_decision != cycle1_decision
    _blocked_dispatch(env.store, task2, decision_id=cycle2_decision)

    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    step._reconcile_supervised_auto_dispatches(_TICK_LATER)  # noqa: SLF001

    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert after.auto_grace_deadline_at is None
    assert after.auto_acked_at is None
    assert any(
        e["payload"]["reason"].startswith("admission_failed:")
        for e in _abort_events(env.store)
    )
    assert env.shadow.load_pending_all() == ()
    assert env.executor.unique_calls == []
    env.store.close()


def test_old_blocked_dispatch_does_not_abort_new_cycle(tmp_path: Path) -> None:
    """§12: a stale BLOCKED dispatch row from an older cycle must not
    mutate (abort) the current AUTO cycle — no cross-cycle
    contamination."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    # Cycle 1: pre-worker failure (BLOCKED row, no run), then the old
    # lifecycle legitimately closed; the historical row REMAINS.
    _plan(env)
    task = env.store.get_task("task-1")
    old_decision = task.auto_decision_id
    _blocked_dispatch(env.store, task, decision_id=old_decision)
    env.store.abort_auto_lifecycle("task-1", reason="cycle_1_closed")

    # Cycle 2: fresh decision + pending, task sits in AUTO_GRACE.
    env.tick(_TICK_LATER)
    task2 = env.store.get_task("task-1")
    assert task2.state is TaskState.AUTO_GRACE
    new_decision = task2.auto_decision_id
    assert new_decision != old_decision
    assert [p.pending_id for p in env.shadow.load_pending_all()] == [new_decision]

    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    step._reconcile_supervised_auto_dispatches(_TICK_LATER)  # noqa: SLF001

    after = env.store.get_task("task-1")
    assert after.state is TaskState.AUTO_GRACE
    assert after.auto_decision_id == new_decision
    assert [p.pending_id for p in env.shadow.load_pending_all()] == [new_decision]
    # Only the legitimate cycle-1 close is audited — reconciliation
    # added nothing.
    aborts = _abort_events(env.store)
    assert [e["payload"]["reason"] for e in aborts] == ["cycle_1_closed"]
    assert env.executor.unique_calls == []
    env.store.close()


def test_current_dispatch_with_exact_run_is_not_preworker_aborted(
    tmp_path: Path,
) -> None:
    """§13: the CURRENT cycle's dispatch row is terminal, but its exact
    ``run-{dispatch_id}`` row exists → the worker truly launched → the
    executor/verifier lifecycle owns the outcome, no pre-worker abort."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    dispatch_id = _blocked_dispatch(env.store, task, decision_id=decision)
    _insert_run(env.store, "task-1", f"run-{dispatch_id}")

    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    step._reconcile_supervised_auto_dispatches(_TICK_LATER)  # noqa: SLF001

    after = env.store.get_task("task-1")
    assert after.state is TaskState.AUTO_GRACE
    assert after.auto_decision_id == decision
    assert [p.pending_id for p in env.shadow.load_pending_all()] == [decision]
    assert _abort_events(env.store) == []
    env.store.close()


def test_run_correlation_is_dispatch_scoped(tmp_path: Path) -> None:
    """§19: for dispatch X only ``run-X`` counts as X's run. A run row
    for the same task under a different id must NOT be treated as
    X's run — pinning the derivation so a future refactor of
    ``run-{dispatch_id}`` breaks this visibly instead of silently
    reverting to task-scoped lookups."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    _blocked_dispatch(env.store, task, decision_id=decision)
    # Same task, run row for ANOTHER dispatch id:
    _insert_run(env.store, "task-1", "run-owner-dispatch-somewhere-else")

    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    step._reconcile_supervised_auto_dispatches(_TICK_LATER)  # noqa: SLF001

    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert any(
        e["payload"]["reason"].startswith("admission_failed:")
        for e in _abort_events(env.store)
    )
    assert env.shadow.load_pending_all() == ()
    env.store.close()


def test_no_metadata_historical_dispatch_fails_closed(tmp_path: Path) -> None:
    """§15/§16: BLOCKED task with NO current auto metadata + a
    historical SUPERVISED_AUTO BLOCKED dispatch row → reconciliation
    must NOT force BLOCKED→READY. A historical row alone proves
    nothing about the current lifecycle; BLOCKED is the safer truth."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    _blocked_dispatch(env.store, task, decision_id=task.auto_decision_id)
    env.store.abort_auto_lifecycle("task-1", reason="cycle_1_closed")
    # The owner parks the (now metadata-free) task in BLOCKED; the
    # historical dispatch row is still present.
    ready = env.store.get_task("task-1")
    assert ready.auto_decision_id is None
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=ready.state_version
    )

    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    step._reconcile_supervised_auto_dispatches(_TICK_LATER)  # noqa: SLF001

    after = env.store.get_task("task-1")
    assert after.state is TaskState.BLOCKED
    assert [e["payload"]["reason"] for e in _abort_events(env.store)] == [
        "cycle_1_closed"
    ]
    env.store.close()
