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

import pytest

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


# ---------------------------------------------------------------------------
# Round 7 — reconciliation gates every mutation on the canonical
# AutoExecutionCorrelation classifier (review matrix A–G).
# ---------------------------------------------------------------------------

from personal_ai_orchestrator.supervised_auto_step import (  # noqa: E402
    AutoExecutionCorrelation,
    correlate_supervised_auto_execution,
)


def _count_rows(store, table: str) -> int:
    return store.connection.execute(
        f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608 — test fixture table
    ).fetchone()["n"]


def _conflict_events(store) -> list:
    return store.connection.execute(
        "SELECT payload_json FROM audit_events"
        " WHERE event_type='AUTO_EXECUTION_CORRELATION_CONFLICT'"
        " AND task_id IS NULL"
    ).fetchall()


def _insert_dispatch_row(
    store,
    *,
    request_id: str,
    dispatch_id: str,
    version: int,
    target: str,
    authority: str = "SUPERVISED_AUTO",
    task_id: str = "task-1",
    status: str = "BLOCKED",
) -> None:
    store.connection.execute(
        "INSERT INTO owner_dispatches(dispatch_id, request_id, task_id,"
        " task_state_version, execution_target_id, authority, status,"
        " created_at, started_at, finished_at, failure_code, failure_reason)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            dispatch_id,
            request_id,
            task_id,
            version,
            target,
            authority,
            status,
            NOW.isoformat(),
            None,
            None,
            None,
            None,
        ),
    )
    store.connection.commit()


def _assert_conflict_preserved(env, decision: str) -> None:
    task = env.store.get_task("task-1")
    assert task.state is TaskState.BLOCKED  # never READY — not aborted
    assert task.auto_decision_id == decision  # metadata PRESERVED
    assert env.shadow.load_pending(decision) is not None  # pending PRESERVED
    assert env.shadow.load_all() == ()  # no fake observation
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 0
    assert _count_rows(env.store, "auto_shadow_finalize_outbox") == 0
    # Sanitized event, deduped across reconciliation + terminal sweep.
    assert len(_conflict_events(env.store)) == 1
    assert _abort_events(env.store) == []


def test_reconciliation_wrong_target_conflict_preserves_everything(tmp_path) -> None:
    """Matrix A (§12): frozen TARGET_A, row TARGET_B, no run → CONFLICT."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    _insert_dispatch_row(
        env.store,
        request_id=request_id,
        dispatch_id=f"owner-dispatch-{request_id}",
        version=task.state_version,
        target="TARGET_B",
    )
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )

    assert (
        correlate_supervised_auto_execution(
            env.store, task_id="task-1", auto_decision_id=decision
        )
        is AutoExecutionCorrelation.CONFLICT
    )
    env.tick(_TICK_LATER)
    _assert_conflict_preserved(env, decision)
    env.store.close()


def test_reconciliation_wrong_dispatch_id_conflict_preserves(tmp_path) -> None:
    """Matrix B (§13): deterministic request id, foreign dispatch id."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    _insert_dispatch_row(
        env.store,
        request_id=request_id,
        dispatch_id="owner-dispatch-foreign",
        version=task.state_version,
        target="m3-sub",
    )
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )

    assert (
        correlate_supervised_auto_execution(
            env.store, task_id="task-1", auto_decision_id=decision
        )
        is AutoExecutionCorrelation.CONFLICT
    )
    env.tick(_TICK_LATER)
    _assert_conflict_preserved(env, decision)
    env.store.close()


@pytest.mark.parametrize("delta", [1, -1])
def test_reconciliation_wrong_live_version_conflict_preserves(tmp_path, delta) -> None:
    """Matrix C case 1 (§14): AUTO_GRACE task, reservation version V±1."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    _insert_dispatch_row(
        env.store,
        request_id=request_id,
        dispatch_id=f"owner-dispatch-{request_id}",
        version=task.state_version + delta,
        target="m3-sub",
    )

    assert (
        correlate_supervised_auto_execution(
            env.store,
            task_id="task-1",
            auto_decision_id=decision,
            expected_task_state_version=task.state_version,
        )
        is AutoExecutionCorrelation.CONFLICT
    )
    # Before the grace deadline: only reconciliation can act.
    env.tick(NOW + timedelta(seconds=5))
    task = env.store.get_task("task-1")
    assert task.state is TaskState.AUTO_GRACE  # never aborted
    assert task.auto_decision_id == decision
    assert env.shadow.load_pending(decision) is not None
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 0
    assert _abort_events(env.store) == []
    env.store.close()


def test_reconciliation_invalid_terminal_version_conflict_preserves(tmp_path) -> None:
    """Matrix C case 2 (§14): BLOCKED task, reservation version NOT
    strictly below the terminal version → ordering contract violated."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    # Reservation version 4; the task's BLOCKED version will also be 4 —
    # not strictly below → CONFLICT under terminal semantics.
    _insert_dispatch_row(
        env.store,
        request_id=request_id,
        dispatch_id=f"owner-dispatch-{request_id}",
        version=task.state_version + 1,
        target="m3-sub",
    )
    blocked = env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )
    assert blocked.state_version == task.state_version + 1

    assert (
        correlate_supervised_auto_execution(
            env.store, task_id="task-1", auto_decision_id=decision
        )
        is AutoExecutionCorrelation.CONFLICT
    )
    env.tick(_TICK_LATER)
    _assert_conflict_preserved(env, decision)
    env.store.close()


def test_reconciliation_exact_preworker_auto_grace_still_aborts(tmp_path) -> None:
    """Matrix D (§15): exact current BLOCKED reservation, no run →
    legitimate pre-worker abort (not frozen by the fix)."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    _blocked_dispatch(env.store, task, decision_id=decision)

    assert (
        correlate_supervised_auto_execution(
            env.store,
            task_id="task-1",
            auto_decision_id=decision,
            expected_task_state_version=task.state_version,
        )
        is AutoExecutionCorrelation.EXACT_PREWORKER
    )
    env.tick(NOW + timedelta(seconds=5))

    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert env.shadow.load_pending_all() == ()  # cleanup intent drained
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 1
    assert _conflict_events(env.store) == []
    env.store.close()


def test_reconciliation_exact_preworker_blocked_still_aborts(tmp_path) -> None:
    """Matrix E (§16): terminal BLOCKED + exact reservation (version
    strictly below) + no run → allow_blocked crash-atomic abort."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    _blocked_dispatch(env.store, task, decision_id=decision)
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )

    assert (
        correlate_supervised_auto_execution(
            env.store, task_id="task-1", auto_decision_id=decision
        )
        is AutoExecutionCorrelation.EXACT_PREWORKER
    )
    env.tick(_TICK_LATER)

    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert env.shadow.load_pending_all() == ()
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 1
    assert _conflict_events(env.store) == []
    env.store.close()


def test_reconciliation_exact_real_run_is_never_preworker_aborted(tmp_path) -> None:
    """Matrix F (§17): exact reservation + exact run → the executor /
    terminal recovery owns the outcome; no abort, no discard."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    dispatch_id = _blocked_dispatch(env.store, task, decision_id=decision)
    _insert_run(env.store, "task-1", f"run-{dispatch_id}")
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )

    assert (
        correlate_supervised_auto_execution(
            env.store, task_id="task-1", auto_decision_id=decision
        )
        is AutoExecutionCorrelation.EXACT_REAL_RUN
    )
    env.tick(_TICK_LATER)

    after = env.store.get_task("task-1")
    assert after.state is TaskState.BLOCKED  # NOT aborted to READY
    # Real-execution close owns it: finalize evidence, never discard.
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 0
    assert _count_rows(env.store, "auto_shadow_finalize_outbox") == 1
    assert len(env.shadow.load_all()) == 1
    assert after.auto_decision_id is None  # cleared AFTER durable proof
    env.store.close()


def test_reconciliation_no_dispatch_ignores_historical_rows(tmp_path) -> None:
    """Matrix G (§18): no current-cycle dispatch row — an unrelated
    historical SUPERVISED_AUTO BLOCKED row must not abort the cycle."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    # An OLD-cycle terminal row for the same task (different request id).
    _insert_dispatch_row(
        env.store,
        request_id="supervised-auto-dispatch-auto-task-1-v0",
        dispatch_id="owner-dispatch-supervised-auto-dispatch-auto-task-1-v0",
        version=1,
        target="m3-sub",
    )

    assert (
        correlate_supervised_auto_execution(
            env.store,
            task_id="task-1",
            auto_decision_id=decision,
            expected_task_state_version=task.state_version,
        )
        is AutoExecutionCorrelation.NO_DISPATCH
    )
    env.tick(NOW + timedelta(seconds=5))

    after = env.store.get_task("task-1")
    assert after.state is TaskState.AUTO_GRACE  # untouched
    assert after.auto_decision_id == decision
    assert env.shadow.load_pending(decision) is not None
    assert _count_rows(env.store, "auto_shadow_cleanup_outbox") == 0
    assert _abort_events(env.store) == []
    assert _conflict_events(env.store) == []
    env.store.close()
