"""Round 6 — authority-bound source state + terminal recovery classification.

P0-1: the legal worker source state is a property of the DURABLE
dispatch authority (OWNER_INITIATED_EXECUTION → READY,
SUPERVISED_AUTO → AUTO_GRACE). A stale SUPERVISED_AUTO reservation
whose task was vetoed / mode-aborted back to READY can never start, and
its pre-worker failure can never mutate the owner-controlled READY
task.

P0-2: terminal recovery classifies a run as AUTO execution evidence
only through the canonical exact current-AUTO dispatch proof — a
foreign OWNER row (or wrong task / target / dispatch id) occupying the
deterministic namespace with its own run is a CONFLICT, fail closed,
never ordinary pre-worker cleanup.

P1: the tick's threading contract is documented truthfully.
"""

from datetime import timedelta

import pytest

from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    TaskState,
    expected_source_state_for_dispatch_authority,
)
from personal_ai_orchestrator.supervised_auto_step import (
    AutoExecutionCorrelation,
    correlate_supervised_auto_execution,
    current_supervised_auto_dispatch,
    real_execution_recovery_proof,
    supervised_auto_dispatch_request_id,
)
from tests.test_supervised_auto_step import NOW, _env, _plan

_TICK_LATER = NOW + timedelta(seconds=301)


# ---------------------------------------------------------------------------
# §5 — canonical authority → source-state mapping
# ---------------------------------------------------------------------------


def test_authority_to_source_state_mapping() -> None:
    assert (
        expected_source_state_for_dispatch_authority("OWNER_INITIATED_EXECUTION") is TaskState.READY
    )
    assert expected_source_state_for_dispatch_authority("SUPERVISED_AUTO") is TaskState.AUTO_GRACE
    with pytest.raises(ValueError):
        expected_source_state_for_dispatch_authority("GHOST_AUTHORITY")


def _store_with_auto_grace_task(tmp_path) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "store.db")
    store.submit_task(task_id="t1", request_id="submit-1", intent="fixture")
    store.transition_task("t1", TaskState.READY)
    planned = store.transition_task("t1", TaskState.AUTO_PLANNED)
    store.transition_task("t1", TaskState.AUTO_GRACE, expected_version=planned.state_version)
    return store


def test_store_rejects_caller_state_weakening_for_auto_authority(tmp_path) -> None:
    """§8/§42-B: a caller passing READY for a SUPERVISED_AUTO reservation
    cannot make the store start the worker — the durable authority owns
    the source state inside the transaction."""

    store = _store_with_auto_grace_task(tmp_path)
    store.reserve_owner_dispatch(
        dispatch_id="owner-dispatch-dr-1",
        request_id="dr-1",
        task_id="t1",
        task_state_version=3,
        execution_target_id="target-a",
        authority="SUPERVISED_AUTO",
    )

    with pytest.raises(ValueError, match="legal source state"):
        store.start_dispatched_worker(
            dispatch_id="owner-dispatch-dr-1",
            task_id="t1",
            expected_task_version=3,
            run_id="run-owner-dispatch-dr-1",
            worker_id="target-a",
            writer_token="writer-x",
            expected_state=TaskState.READY,  # the weakening attempt
        )
    runs = store.connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    assert runs["n"] == 0
    store.close()


def test_store_unknown_authority_fails_closed_before_any_run(tmp_path) -> None:
    store = _store_with_auto_grace_task(tmp_path)
    store.connection.execute(
        "INSERT INTO owner_dispatches(dispatch_id, request_id, task_id,"
        " task_state_version, execution_target_id, authority, status,"
        " created_at, started_at, finished_at, failure_code, failure_reason)"
        " VALUES('owner-dispatch-ghost','ghost','t1',3,'target-a',"
        " 'GHOST_AUTHORITY','RESERVED',?,?,NULL,NULL,NULL)",
        (NOW.isoformat(), None),
    )
    store.connection.commit()

    with pytest.raises(ValueError, match="unknown dispatch authority"):
        store.start_dispatched_worker(
            dispatch_id="owner-dispatch-ghost",
            task_id="t1",
            expected_task_version=3,
            run_id="run-owner-dispatch-ghost",
            worker_id="target-a",
            writer_token="writer-x",
        )
    runs = store.connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    assert runs["n"] == 0
    store.close()


# ---------------------------------------------------------------------------
# §41 AUTHORITY-1..5 — executor entry enforcement (real executor)
# ---------------------------------------------------------------------------


def _reserve_with(
    harness,
    *,
    authority: str,
    auto_grace: bool,
    request_id: str = "dispatch-auth-1",
) -> str:
    from tests.test_dispatch_executor import _git

    store = SafetyKernelStore(harness.state_db)
    try:
        base_sha = _git(harness.main_repo, "rev-parse", "HEAD")
        store.register_project(
            project_id="project-main",
            display_name="Main Repo",
            canonical_repo_root=str(harness.main_repo),
            git_root=str(harness.main_repo),
            default_branch="main",
            last_known_head=base_sha,
        )
        store.submit_task(
            task_id="task-1",
            request_id="submit-task-1",
            project_id="project-main",
            base_sha=base_sha,
            intent="Create hello.txt",
        )
        store.transition_task("task-1", TaskState.READY)
        version = 1
        if auto_grace:
            planned = store.transition_task(
                "task-1",
                TaskState.AUTO_PLANNED,
                expected_version=version,
                auto_decision_id="auto-task-1-v0",
            )
            store.transition_task(
                "task-1",
                TaskState.AUTO_GRACE,
                expected_version=planned.state_version,
                auto_grace_deadline_at="1970-01-01T00:00:00+00:00",
            )
            version = store.get_task("task-1").state_version
        store.reserve_owner_dispatch(
            dispatch_id=f"owner-dispatch-{request_id}",
            request_id=request_id,
            task_id="task-1",
            task_state_version=version,
            execution_target_id="zai-coding-plan-glm-5.3",
            authority=authority,
        )
    finally:
        store.close()
    return request_id


def _runs_count(store) -> int:
    return store.connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]


def test_supervised_auto_authority_cannot_start_from_ready(tmp_path) -> None:
    """AUTHORITY-1 / RACE-1: stale SUPERVISED_AUTO reservation after veto."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="SUPERVISED_AUTO", auto_grace=True)
    # The veto / mode abort won: AUTO_GRACE → READY, metadata cleared,
    # MANUAL lock — the owner owns the task again.
    store = SafetyKernelStore(harness.state_db)
    store.abort_auto_lifecycle("task-1", reason="owner_veto", force_manual=True)
    store.close()

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.READY  # NOT reactivated, NOT BLOCKED
        assert task.auto_decision_id is None
        assert _runs_count(store) == 0  # no worker
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "BLOCKED"  # stale row inert
    finally:
        store.close()


def test_owner_authority_cannot_start_from_auto_grace(tmp_path) -> None:
    """AUTHORITY-2: an OWNER row may never start from AUTO_GRACE."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="OWNER_INITIATED_EXECUTION", auto_grace=True)

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.AUTO_GRACE  # untouched
        assert _runs_count(store) == 0
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "BLOCKED"
        assert dispatch.failure_code == "TASK_NOT_READY"
    finally:
        store.close()


def test_unknown_authority_never_spawns_a_worker(tmp_path) -> None:
    """AUTHORITY-5: unknown authority fails closed with a sanitized code."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="GHOST_AUTHORITY", auto_grace=False)

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.READY  # unchanged
        assert _runs_count(store) == 0
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "BLOCKED"
        assert dispatch.failure_code == "UNKNOWN_DISPATCH_AUTHORITY"
    finally:
        store.close()


def test_supervised_auto_from_auto_grace_still_starts(tmp_path) -> None:
    """AUTHORITY-3: the valid AUTO start must keep working (§14)."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="SUPERVISED_AUTO", auto_grace=True)

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.VERIFIED
        assert _runs_count(store) == 1
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "FINISHED"
    finally:
        store.close()


def test_owner_from_ready_still_starts(tmp_path) -> None:
    """AUTHORITY-4: the valid owner start must keep working (§13)."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="OWNER_INITIATED_EXECUTION", auto_grace=False)

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.VERIFIED
        assert _runs_count(store) == 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# §41 RACE-2 / PREWORKER-1 — veto wins after the child exists
# ---------------------------------------------------------------------------


def test_veto_race_after_spawn_cancels_child_and_keeps_ready(tmp_path) -> None:
    """RACE-2: the executor reaches spawn; the veto then moves the task to
    READY; the atomic start rejects, the child is cancelled exactly once,
    no RUNNING, no run row, the task stays READY."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="SUPERVISED_AUTO", auto_grace=True)

    cancelled: list[object] = []
    original_spawn = harness.executor._spawn_worker
    original_cleanup = harness.executor._supervisor.cleanup

    async def spawn_then_veto(store, dispatch, worktree):
        # Use an exact supervised fixture child: a fabricated PID cannot prove
        # cleanup under the process/group receipt contract.
        supervised = await original_spawn(store, dispatch, worktree)
        veto_store = SafetyKernelStore(harness.state_db)
        try:
            veto_store.abort_auto_lifecycle("task-1", reason="owner_veto")
        finally:
            veto_store.close()
        return supervised

    async def observed_cleanup(supervised, **kwargs):
        cancelled.append(supervised)
        return await original_cleanup(supervised, **kwargs)

    harness.executor._spawn_worker = spawn_then_veto  # type: ignore[method-assign]
    harness.executor._supervisor.cleanup = observed_cleanup  # type: ignore[method-assign]

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        assert len(cancelled) == 1  # the exact child, cleaned exactly once
        assert cancelled[0].cleanup_result.confirmed
        assert cancelled[0].cleanup_result.child_reaped
        assert store.get_workspace("task-1").writer_token is None
        task = store.get_task("task-1")
        assert task.state is TaskState.READY  # veto result respected
        assert task.auto_decision_id is None
        assert _runs_count(store) == 0  # no RUNNING, no run row
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "BLOCKED"
        assert dispatch.failure_code == "RUN_START_FAILED"
    finally:
        store.close()


def test_stale_auto_preworker_failure_does_not_block_ready(tmp_path) -> None:
    """PREWORKER-1: a pre-worker failure of a stale AUTO reservation must
    NOT mutate the vetoed READY task (§11)."""

    from tests.test_dispatch_executor import ExecutorHarness

    harness = ExecutorHarness(tmp_path)
    request_id = _reserve_with(harness, authority="SUPERVISED_AUTO", auto_grace=True)

    async def failing_spawn(store, dispatch, worktree):
        veto_store = SafetyKernelStore(harness.state_db)
        veto_store.abort_auto_lifecycle("task-1", reason="owner_veto")
        veto_store.close()
        raise RuntimeError("simulated spawn failure")

    harness.executor._spawn_worker = failing_spawn  # type: ignore[method-assign]

    harness.executor.execute(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        task = store.get_task("task-1")
        assert task.state is TaskState.READY  # NOT READY→BLOCKED
        assert task.auto_decision_id is None
        assert _runs_count(store) == 0
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        assert dispatch.status.value == "BLOCKED"
        assert dispatch.failure_code == "WORKER_SPAWN_FAILED"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# §41 CORRELATION-1..6 — terminal classification via the canonical proof
# ---------------------------------------------------------------------------


def _terminal_env(
    tmp_path,
    *,
    authority: str = "OWNER_INITIATED_EXECUTION",
    target: str = "TARGET_B",
    row_task_id: str | None = None,
    with_run: bool = True,
    task_verified: bool = False,
):
    """Terminal residue + a (possibly foreign) row on the deterministic
    AUTO request id, with (or without) its exact run row."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    dispatch_id = f"owner-dispatch-{request_id}"
    if row_task_id is not None and row_task_id != "task-1":
        env.store.submit_task(
            task_id=row_task_id,
            request_id=f"submit-{row_task_id}",
            project_id="p1",
            intent="foreign task",
            base_sha=env.store.get_project("p1").last_known_head,
        )
        env.store.transition_task(row_task_id, TaskState.READY)
    env.store.connection.execute(
        "INSERT INTO owner_dispatches(dispatch_id, request_id, task_id,"
        " task_state_version, execution_target_id, authority, status,"
        " created_at, started_at, finished_at, failure_code, failure_reason)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            dispatch_id,
            request_id,
            row_task_id or "task-1",
            task.state_version,
            target,
            authority,
            "FINISHED" if with_run else "RESERVED",
            NOW.isoformat(),
            NOW.isoformat(),
            (NOW + timedelta(seconds=60)).isoformat(),
            None,
            None,
        ),
    )
    running = env.store.transition_task(
        "task-1", TaskState.RUNNING, expected_version=task.state_version
    )
    finished = env.store.transition_task(
        "task-1", TaskState.WORKER_FINISHED, expected_version=running.state_version
    )
    verifying = env.store.transition_task(
        "task-1", TaskState.VERIFYING, expected_version=finished.state_version
    )
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=verifying.state_version,
        target=TaskState.VERIFIED if task_verified else TaskState.BLOCKED,
        reason="test: terminal residue",
        finalize=None,
    )
    if with_run:
        env.store.connection.execute(
            "INSERT INTO runs (run_id, task_id, worker_id, pid, status,"
            " started_at, finished_at) VALUES(?, 'task-1', ?, 4711,"
            " 'FINISHED', ?, ?)",
            (
                f"run-{dispatch_id}",
                target,
                NOW.isoformat(),
                (NOW + timedelta(seconds=60)).isoformat(),
            ),
        )
    env.store.connection.commit()
    return env, decision


def _conflict_events(store) -> list:
    return store.connection.execute(
        "SELECT payload_json FROM audit_events"
        " WHERE event_type='AUTO_EXECUTION_CORRELATION_CONFLICT'"
        " AND task_id IS NULL"
    ).fetchall()


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="foreign-owner-row"),
        pytest.param({"authority": "SUPERVISED_AUTO", "target": "TARGET_B"}, id="wrong-target"),
        pytest.param({"row_task_id": "task-2"}, id="wrong-task"),
    ],
)
def test_foreign_run_is_never_auto_execution_evidence(tmp_path, kwargs) -> None:
    """CORRELATION-1/2/3 + CORRELATION-6: a non-exact row occupying the
    deterministic namespace — even with its own exact run — is a CONFLICT,
    never AUTO real execution, never ordinary pre-worker cleanup."""

    env, decision = _terminal_env(tmp_path, **kwargs)

    correlation = correlate_supervised_auto_execution(
        env.store, task_id="task-1", auto_decision_id=decision
    )
    assert correlation is AutoExecutionCorrelation.CONFLICT
    assert (
        current_supervised_auto_dispatch(env.store, task_id="task-1", auto_decision_id=decision)
        is None
    )

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.BLOCKED
    assert task.auto_decision_id == decision  # metadata PRESERVED
    assert env.shadow.load_all() == ()  # no fake observation
    finalize = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM auto_shadow_finalize_outbox"
    ).fetchone()["n"]
    assert finalize == 0  # no reconstructed finalize intent
    cleanup = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM auto_shadow_cleanup_outbox"
    ).fetchone()["n"]
    assert cleanup == 0  # never the pre-worker discard path
    assert len(_conflict_events(env.store)) == 1
    # The metadata-clear recovery guard also fails closed for a conflict
    # (§26: CONFLICT is NOT pre-worker safe).
    assert (
        real_execution_recovery_proof(env.store, env.shadow, task_id="task-1", pending_id=decision)
        is False
    )


def test_exact_auto_run_is_recognized_and_recovers(tmp_path) -> None:
    """CORRELATION-4 (§25): the exact reservation + exact run still
    classifies as real AUTO execution and recovers through the finalize
    outbox — no duplicate observation."""

    env, decision = _terminal_env(
        tmp_path,
        authority="SUPERVISED_AUTO",
        target="m3-sub",
        with_run=True,
    )

    correlation = correlate_supervised_auto_execution(
        env.store, task_id="task-1", auto_decision_id=decision
    )
    assert correlation is AutoExecutionCorrelation.EXACT_REAL_RUN

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.auto_decision_id is None  # cleared AFTER durable proof
    observations = env.shadow.load_all()
    assert len(observations) == 1  # exactly one, no duplicate
    assert observations[0].verified is False  # task is BLOCKED
    env.tick(_TICK_LATER + timedelta(seconds=5))
    assert len(env.shadow.load_all()) == 1


def test_exact_auto_no_run_is_preworker_cleanup(tmp_path) -> None:
    """CORRELATION-5 (§29): exact reservation without a run stays the
    ordinary pre-worker discard path — metadata does NOT stay stuck."""

    env, decision = _terminal_env(
        tmp_path,
        authority="SUPERVISED_AUTO",
        target="m3-sub",
        with_run=False,
    )

    correlation = correlate_supervised_auto_execution(
        env.store, task_id="task-1", auto_decision_id=decision
    )
    assert correlation is AutoExecutionCorrelation.EXACT_PREWORKER

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.auto_decision_id is None  # pre-worker close is legitimate
    assert env.shadow.load_pending_all() == ()  # pending discarded
    assert env.shadow.load_all() == ()  # no observation owed
