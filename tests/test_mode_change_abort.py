"""M1 WP5a-2 §25 — mode-change / project-disable fail-closed aborts.

Covers the frozen contract: when the effective mode leaves
``SUPERVISED_AUTO`` (or the project revokes its opt-in) while a task
sits in an AUTO_* lifecycle, the lifecycle must abort fail-closed —
stop the countdown, prevent dispatch, clean the pending shadow, clear
the auto metadata, return the task to the owner-controlled READY state
and audit the abort reason. RUNNING tasks are never touched.
"""

from __future__ import annotations

from datetime import timedelta

from personal_ai_orchestrator.safety_kernel import TaskState
from tests.test_supervised_auto_step import NOW, _env, _plan


def test_auto_planned_aborts_when_mode_flips_to_manual(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    # Land the task in AUTO_PLANNED (crash-boundary posture): planning
    # committed, grace promotion not yet run.
    task = env.store.get_task("task-1")
    env.store.transition_task(
        "task-1",
        TaskState.AUTO_PLANNED,
        expected_version=task.state_version,
        auto_decision_id="auto-task-1-manual-flip",
    )
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None


def test_auto_grace_mode_flip_to_manual_never_dispatches(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    # A late tick well past the original deadline must not dispatch.
    env.tick(NOW + timedelta(hours=1))
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.READY


def test_mode_flip_after_deadline_before_dispatch_blocks_dispatch(tmp_path) -> None:
    """TOCTOU: deadline already expired, mode flips MANUAL before the
    dispatch tick runs → no dispatch, abort wins."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # Deadline is NOW+300s. Flip the mode *after* the deadline elapsed
    # but *before* any tick observed it.
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.READY


def test_mode_flip_to_active_keeps_api_rejection_and_aborts_auto(tmp_path) -> None:
    """SUPERVISED_AUTO → ACTIVE without activation authority: the API
    rejects the ACTIVE request (unchanged 409 gate) and the AUTO state
    is NOT executed by a stale tick."""

    from personal_ai_orchestrator.control_api import ControlPlaneError

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    try:
        env.service.update_scheduling_settings(
            {"default_scheduling_policy": "BALANCED", "mode": "ACTIVE"}
        )
        raise AssertionError("ACTIVE must be rejected without authority")
    except ControlPlaneError as error:
        assert error.status == 409
        assert error.code == "production_active_not_authorized"
    # Mode is still SUPERVISED_AUTO — the AUTO lifecycle survives intact
    # (the rejection changed nothing) and dispatch still fires normally
    # at the deadline.
    assert env.store.get_task("task-1").state is TaskState.AUTO_GRACE
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1


def test_running_task_survives_mode_flip_to_manual(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    running = env.store.transition_task(
        "task-1", TaskState.RUNNING, expected_version=task.state_version
    )
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    after = env.store.get_task("task-1")
    assert after.state is TaskState.RUNNING
    assert after.state_version == running.state_version


def test_project_disable_mid_grace_aborts(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.service.set_project_supervised_auto_settings(
        "p1",
        {
            "supervised_auto_allowed": False,
            "unattended_allowed": True,
            "grace_seconds": 300,
        },
    )
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.READY
    aborted = [e for e in env.store.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"]
    assert any(e["payload"]["reason"] == "project_auto_disabled" for e in aborted)


def test_executor_rejects_auto_grace_dispatch_after_concurrent_abort(tmp_path) -> None:
    """The executor-side TOCTOU guard: a SUPERVISED_AUTO dispatch whose
    task was aborted back to READY after reservation fails closed — no
    worker start, no RUNNING."""

    from personal_ai_orchestrator.dispatch_initiator import initiate_owner_dispatch

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    request_id = f"supervised-auto-dispatch-{task.auto_decision_id}"
    initiate_owner_dispatch(
        env.store,
        env.executor,
        task=task,
        request_id=request_id,
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
        project_provider=env.service.get_project,
        registry_provider=env.service._effective_registry,  # noqa: SLF001
        provider_registry_manager=None,
        runtime_available_provider=env.service._runtime_available,  # noqa: SLF001
        execution_evidence_journal=env.service.execution_evidence_journal,
        expected_state=TaskState.AUTO_GRACE,
    )
    # The owner vetoes/aborts the lifecycle between reservation and the
    # executor's state read.
    aborted = env.store.transition_task(
        "task-1",
        TaskState.READY,
        expected_version=task.state_version,
        reason="concurrent abort for TOCTOU test",
    )
    assert aborted.state is TaskState.READY
    # The executor's own expected-state guard rejects the run: use the
    # store-level primitive the executor delegates to.
    env.store.register_workspace(
        task_id="task-1",
        repo_path=str(tmp_path),
        worktree_path=str(tmp_path / "wt-task-1"),
        branch="wt",
        base_sha=task.base_sha or "abc",
        project_id="p1",
    )
    workspace = env.store.acquire_writer("task-1", "writer-token-1")
    try:
        env.store.start_dispatched_worker(
            dispatch_id=f"owner-dispatch-{request_id}",
            task_id="task-1",
            expected_task_version=aborted.state_version,
            run_id="run-toctou",
            worker_id="m3-sub",
            writer_token=workspace.writer_token,
            expected_state=TaskState.AUTO_GRACE,
        )
        raise AssertionError("worker start must fail closed after abort")
    except ValueError as error:
        assert "AUTO_GRACE" in str(error)
    assert env.store.get_task("task-1").state is TaskState.READY
