import sys
from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import cancel_worker_run, start_worker_run
from personal_ai_orchestrator.opencode_contract import ModelRef, RoutingDecision, RoutingMode
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority, SwitchLeaseStatus


def _running_task(store: SafetyKernelStore, tmp_path: Path, *, pid: int) -> int:
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "writer-1")
    store.transition_task("t1", TaskState.READY)
    running = store.transition_task("t1", TaskState.RUNNING)
    start_worker_run(
        store,
        task_id="t1",
        run_id="run-1",
        worker_id="worker",
        writer_token="writer-1",
        pid=pid,
    )
    return running.state_version


@pytest.mark.asyncio
async def test_cancellation_closes_run_task_writer_and_switch_lease(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    child = await supervisor.start(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
    )
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    version = _running_task(store, tmp_path, pid=child.pid)

    decision = RoutingDecision(
        decision_id="decision-1",
        request_id="route-request",
        mode=RoutingMode.ACTIVE,
        selected_model=ModelRef(provider_id="minimax", model_id="m3"),
        switch_requested=True,
        task_state_version=version,
    )
    store.record_routing_decision(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        payload=decision.model_dump(mode="json"),
    )
    lease_authority = SwitchLeaseAuthority(store)
    lease = lease_authority.authorize(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        task_state_version=version,
        session_id="session-1",
    )

    state = await cancel_worker_run(
        store,
        supervisor,
        child,
        task_id="t1",
        run_id="run-1",
        writer_token="writer-1",
        grace_seconds=0.5,
    )
    assert state is TaskState.CANCELLED
    assert store.get_task("t1").state is TaskState.CANCELLED
    assert store.get_workspace("t1").writer_token is None
    run = store.connection.execute("SELECT status FROM runs WHERE run_id='run-1'").fetchone()
    assert run is not None and run["status"] == "CANCELLED"
    assert lease_authority.get(lease.lease_id).status is SwitchLeaseStatus.ABORTED
    assert child.pid not in supervisor.owned_pids()


@pytest.mark.asyncio
async def test_cancellation_refuses_pid_mismatch_without_signalling(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    child = await supervisor.start(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
    )
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    _running_task(store, tmp_path, pid=child.pid + 1)

    with pytest.raises(RuntimeError, match="persisted run PID"):
        await cancel_worker_run(
            store,
            supervisor,
            child,
            task_id="t1",
            run_id="run-1",
            writer_token="writer-1",
            grace_seconds=0.5,
        )
    assert child.pid in supervisor.owned_pids()
    assert store.get_task("t1").state is TaskState.RUNNING
    await supervisor.cancel(child, grace_seconds=0.5)
