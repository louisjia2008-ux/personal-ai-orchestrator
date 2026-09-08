from pathlib import Path

import pytest

from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
)


def _running_store(path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(path)
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    return store


def _run_status(store: SafetyKernelStore, run_id: str) -> str:
    row = store.connection.execute(
        "SELECT status FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row is not None
    return row["status"]


def test_durable_store_enforces_one_active_run_per_task_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    first = _running_store(db)
    second = SafetyKernelStore(db)
    first.start_run(run_id="run-1", task_id="t1", worker_id="worker-a")

    with pytest.raises(RuntimeError, match="active worker run"):
        second.start_run(run_id="run-2", task_id="t1", worker_id="worker-b")

    assert _run_status(first, "run-1") == "RUNNING"


def test_finish_run_rolls_back_state_if_audit_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = _running_store(tmp_path / "state.sqlite3")
    store.start_run(run_id="run-1", task_id="t1", worker_id="worker")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        store.finish_run(run_id="run-1", status="FINISHED", result={"ok": True})

    assert _run_status(store, "run-1") == "RUNNING"


def test_routing_decision_rolls_back_if_audit_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        store.record_routing_decision(
            decision_id="decision-1",
            request_id="request-1",
            task_id=None,
            payload={"decision_id": "decision-1"},
        )

    row = store.connection.execute(
        "SELECT decision_id FROM routing_decisions WHERE request_id='request-1'"
    ).fetchone()
    assert row is None


def test_owner_dispatched_worker_starts_run_and_running_state_atomically(
    tmp_path: Path,
) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch-1",
        request_id="dispatch-request-1",
        task_id="t1",
        task_state_version=0,
        execution_target_id="m3-sub",
        authority="OWNER_INITIATED_EXECUTION",
    )
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    store.acquire_writer("t1", "writer-1")
    ready = store.transition_task("t1", TaskState.READY, expected_version=0)

    running = store.start_dispatched_worker(
        dispatch_id="dispatch-1",
        task_id="t1",
        expected_task_version=ready.state_version,
        run_id="run-1",
        worker_id="m3-sub",
        writer_token="writer-1",
        pid=1234,
    )

    assert running.state is TaskState.RUNNING
    assert running.state_version == ready.state_version + 1
    assert _run_status(store, "run-1") == "RUNNING"
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request-1")
    assert dispatch.status is OwnerDispatchStatus.STARTED
    assert dispatch.started_at is not None
    store.assert_running_invariant("t1")


def test_owner_dispatched_worker_requires_writer_lock_before_running(
    tmp_path: Path,
) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch-1",
        request_id="dispatch-request-1",
        task_id="t1",
        task_state_version=0,
        execution_target_id="m3-sub",
        authority="OWNER_INITIATED_EXECUTION",
    )
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    ready = store.transition_task("t1", TaskState.READY, expected_version=0)

    with pytest.raises(RuntimeError, match="active writer lock"):
        store.start_dispatched_worker(
            dispatch_id="dispatch-1",
            task_id="t1",
            expected_task_version=ready.state_version,
            run_id="run-1",
            worker_id="m3-sub",
            writer_token="writer-1",
        )

    assert store.get_task("t1").state is TaskState.READY
    row = store.connection.execute("SELECT run_id FROM runs WHERE run_id='run-1'").fetchone()
    assert row is None
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request-1")
    assert dispatch.status is OwnerDispatchStatus.RESERVED


def test_running_invariant_rejects_running_task_without_active_run(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    store.acquire_writer("t1", "writer-1")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)

    with pytest.raises(RuntimeError, match="exactly one active run"):
        store.assert_running_invariant("t1")


def test_startup_reconciliation_rolls_back_as_one_unit_on_audit_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _running_store(tmp_path / "state.sqlite3")
    store.start_run(run_id="run-1", task_id="t1", worker_id="worker")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected reconciliation audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected reconciliation audit failure"):
        store.reconcile_startup()

    assert store.get_task("t1").state is TaskState.RUNNING
    assert _run_status(store, "run-1") == "RUNNING"


# --------------------------------------------------------------------------
# M1 WP5a-1 commit 3 — AUTO_PLANNED / AUTO_GRACE state machine.
#
# The contract frozen in docs/M1_WP5_SPEC.md §4:
#   READY → AUTO_PLANNED (tick promotes)
#   AUTO_PLANNED → AUTO_GRACE | READY
#   AUTO_GRACE → RUNNING | READY   (RUNNING promotion is WP5a-2; this commit
#                                   only pins the AUTO_GRACE → READY path)
#
# Test matrix:
#   A. legal transitions
#   B. illegal transitions fail
#   C. state_version increments exactly once per transition
#   D. transaction rollback after exception: state/version unchanged
#   E. terminal states do not enter AUTO
#   F. restart persistence
#   G. AUTO_PLANNED requires a non-NULL auto_decision_id (the frozen
#      RoutingDecision contract from §10)
# --------------------------------------------------------------------------


def test_legal_transition_ready_to_auto_planned(tmp_path) -> None:
    """READY → AUTO_PLANNED is allowed and bumps state_version once."""

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        auto_planned = store.transition_task(
            "t1",
            TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            reason="auto planning tick",
            auto_decision_id="auto-dec-1",
        )
        assert auto_planned.state is TaskState.AUTO_PLANNED
        assert auto_planned.state_version == ready.state_version + 1
        assert auto_planned.auto_decision_id == "auto-dec-1"
    finally:
        store.close()


def test_legal_transition_auto_planned_to_auto_grace(tmp_path) -> None:
    """AUTO_PLANNED → AUTO_GRACE is allowed; deadline set on entry."""

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
        )
        grace = store.transition_task(
            "t1", TaskState.AUTO_GRACE,
            expected_version=planned.state_version,
            reason="entering grace",
            auto_grace_deadline_at="2026-09-07T00:02:00+00:00",
        )
        assert grace.state is TaskState.AUTO_GRACE
        assert grace.auto_grace_deadline_at == "2026-09-07T00:02:00+00:00"
    finally:
        store.close()


def test_illegal_transition_auto_planned_to_running_fails(tmp_path) -> None:
    """AUTO_PLANNED → RUNNING is NOT allowed in WP5a-1. The
    ``AUTO_GRACE → RUNNING`` transition belongs to WP5a-2.
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
        )
        with pytest.raises((ValueError, RuntimeError)):
            store.transition_task(
                "t1", TaskState.RUNNING,
                expected_version=planned.state_version,
            )
        # State unchanged.
        assert store.get_task("t1").state is TaskState.AUTO_PLANNED
    finally:
        store.close()


def test_illegal_transition_auto_grace_to_running_fails(tmp_path) -> None:
    """AUTO_GRACE → RUNNING is NOT in WP5a-1's transition map. The
    autonomous dispatch path lands in WP5a-2.
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
        )
        grace = store.transition_task(
            "t1", TaskState.AUTO_GRACE,
            expected_version=planned.state_version,
        )
        with pytest.raises((ValueError, RuntimeError)):
            store.transition_task(
                "t1", TaskState.RUNNING,
                expected_version=grace.state_version,
            )
        assert store.get_task("t1").state is TaskState.AUTO_GRACE
    finally:
        store.close()


def test_terminal_states_do_not_enter_auto(tmp_path) -> None:
    """Terminal states (FAILED, CANCELLED, COMPLETED) cannot
    transition into AUTO_* from any source state in the map.
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        # Drive into FAILED.
        ready = store.transition_task("t1", TaskState.READY)
        running = store.transition_task(
            "t1", TaskState.RUNNING,
            expected_version=ready.state_version,
        )
        store.transition_task(
            "t1", TaskState.FAILED,
            expected_version=running.state_version,
            reason="forced",
        )
        with pytest.raises((ValueError, RuntimeError)):
            store.transition_task("t1", TaskState.AUTO_PLANNED)
        with pytest.raises((ValueError, RuntimeError)):
            store.transition_task("t1", TaskState.AUTO_GRACE)
        assert store.get_task("t1").state is TaskState.FAILED
    finally:
        store.close()


def test_state_version_increments_exactly_once(tmp_path) -> None:
    """A single transition increments state_version by exactly 1
    (the prior baseline invariant — pin so AUTO_* transitions
    honor the same contract).
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        task = store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        assert ready.state_version == task.state_version + 1
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
        )
        assert planned.state_version == ready.state_version + 1
    finally:
        store.close()


def test_transaction_rollback_preserves_state_and_version(tmp_path) -> None:
    """A transition that raises mid-way leaves state + state_version
    unchanged (the pre-existing rollback invariant — pin for AUTO_*
    transitions).
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)

        # Inject an audit failure inside the AUTO_PLANNED transition.
        original_audit = store._audit
        def fail_audit(*args, **kwargs):
            raise RuntimeError("injected auto_planned audit failure")
        store._audit = fail_audit  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="auto_planned audit failure"):
                store.transition_task(
                    "t1", TaskState.AUTO_PLANNED,
                    expected_version=ready.state_version,
                    auto_decision_id="auto-dec-1",
                )
        finally:
            store._audit = original_audit  # type: ignore[assignment]

        # State and version unchanged.
        after = store.get_task("t1")
        assert after.state is TaskState.READY
        assert after.state_version == ready.state_version
        assert after.auto_decision_id is None
    finally:
        store.close()


def test_restart_persistence_of_auto_state(tmp_path) -> None:
    """AUTO_PLANNED + AUTO_GRACE survive a SafetyKernelStore
    restart (the SQLite ``_ensure_column`` migration adds the
    four columns with no defaults; the row round-trips).
    """

    db_path = tmp_path / "safety.db"
    store = SafetyKernelStore(db_path)
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
            auto_reason="AUTO_PLANNED{target=m3-sub}",
        )
        _grace = store.transition_task(
            "t1", TaskState.AUTO_GRACE,
            expected_version=planned.state_version,
            auto_grace_deadline_at="2026-09-07T00:02:00+00:00",
        )
    finally:
        store.close()

    # Reopen the same SQLite file. AUTO_GRACE + auto_decision_id +
    # auto_grace_deadline_at + auto_reason must all round-trip.
    store2 = SafetyKernelStore(db_path)
    try:
        restored = store2.get_task("t1")
        assert restored.state is TaskState.AUTO_GRACE
        assert restored.auto_decision_id == "auto-dec-1"
        assert restored.auto_grace_deadline_at == "2026-09-07T00:02:00+00:00"
        assert restored.auto_reason == "AUTO_PLANNED{target=m3-sub}"
    finally:
        store2.close()


def test_auto_planned_veto_back_to_ready(tmp_path) -> None:
    """AUTO_PLANNED → READY (the veto path) clears
    ``auto_decision_id`` so a subsequent re-plan mints a fresh
    decision id. WP5a-2 owns the clear-on-veto helper; here we
    only pin that the *transition* is allowed.
    """

    store = SafetyKernelStore(tmp_path / "safety.db")
    try:
        store.register_project(
            project_id="p1",
            display_name="Fixture",
            canonical_repo_root=str(tmp_path),
            git_root=str(tmp_path),
            default_branch="main",
            last_known_head="abc",
        )
        store.submit_task(
            task_id="t1",
            request_id="r1",
            project_id="p1",
            intent="fix bug",
            base_sha="abc",
        )
        ready = store.transition_task("t1", TaskState.READY)
        planned = store.transition_task(
            "t1", TaskState.AUTO_PLANNED,
            expected_version=ready.state_version,
            auto_decision_id="auto-dec-1",
        )
        vetoed = store.transition_task(
            "t1", TaskState.READY,
            expected_version=planned.state_version,
            reason="owner veto",
        )
        assert vetoed.state is TaskState.READY
        # The auto_decision_id is NOT cleared by the transition
        # itself — the clear-on-veto helper is WP5a-2's job. Pin
        # the current behaviour so the helper's commit can
        # intentionally change it.
        assert vetoed.auto_decision_id == "auto-dec-1"
    finally:
        store.close()
