from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from personal_ai_orchestrator.pi5_child_execution import PAODelegationChildPort
from personal_ai_orchestrator.pi5_contract import DelegationRequest
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_pi5_broker import FakeChildPort
from tests.test_pi5_child_execution import _recommendation_factory
from tests.test_pi_dispatch_executor import (
    HELLO,
    _executor,
    _registry,
    _reserve,
)


def _enabled(tmp_path):
    executor, repo, db = _executor(tmp_path, complete=True)
    _reserve(repo, db)
    executor.pi_runtime = replace(executor.pi_runtime, delegation_enabled=True)
    executor.delegation_child_port = FakeChildPort()
    return executor, db


@pytest.mark.parametrize("failure", ["exit", "spawn", "run_start", "emergency", "cancel"])
@pytest.mark.asyncio
async def test_broker_activation_and_every_cleanup_boundary(tmp_path, monkeypatch, failure):
    executor, db = _enabled(tmp_path)
    child_port = executor.delegation_child_port
    captured = []
    original_spawn = executor._spawn_worker
    request = DelegationRequest(tool_call_id="early", ordinal=1, intent="test", reason="test")

    async def spawn(store, dispatch, worktree):
        process = await original_spawn(store, dispatch, worktree)
        broker = executor._delegation_brokers[dispatch.request_id]
        captured.append(broker)
        assert broker.socket_path.exists()
        assert not broker.session.active
        assert (await broker.session.handle(request)).reason_code == "PARENT_SESSION_INACTIVE"
        assert store.get_task("task-pi").state is TaskState.READY
        return process

    monkeypatch.setattr(executor, "_spawn_worker", spawn)
    if failure == "spawn":

        async def fail_start(*args, **kwargs):
            captured.extend(executor._delegation_brokers.values())
            raise OSError("synthetic spawn failure")

        monkeypatch.setattr(executor._supervisor, "start", fail_start)
    if failure == "run_start":

        def fail_running(*args, **kwargs):
            raise RuntimeError("synthetic RUNNING failure")

        monkeypatch.setattr(SafetyKernelStore, "start_dispatched_worker", fail_running)
    original_wait = executor._wait_for_worker

    async def wait(process):
        broker = captured[0]
        assert broker.session.active
        store = SafetyKernelStore(db)
        try:
            assert store.get_task("task-pi").state is TaskState.RUNNING
            assert (
                store.connection.execute(
                    "SELECT status FROM runs WHERE run_id=?",
                    (broker.session.context.parent_run_id,),
                ).fetchone()[0]
                == "RUNNING"
            )
        finally:
            store.close()
        if failure == "emergency":
            raise RuntimeError("synthetic lifecycle failure")
        if failure == "cancel":
            execution = executor.execution_supervisor.get("task-pi")
            await executor._cancel_on_loop(execution)
            assert not broker.session.active and not broker.socket_path.exists()
        return await original_wait(process)

    monkeypatch.setattr(executor, "_wait_for_worker", wait)
    if failure == "cancel":
        Path(executor.pi_runtime.pi_bin).write_text("#!/bin/sh\nsleep 20\n")
    await executor.execute_async("dispatch-pi")
    assert captured
    assert executor._delegation_brokers == {}
    for broker in captured:
        assert not broker.session.active
        assert not broker.socket_path.exists()
        assert not broker.socket_path.parent.exists()
        assert (await broker.session.handle(request)).reason_code == "PARENT_SESSION_INACTIVE"
    if failure in {"spawn", "run_start"}:
        assert child_port.plans == []


@pytest.mark.asyncio
async def test_fake_parent_node_tool_real_broker_and_verified_child(tmp_path, monkeypatch):
    node = shutil.which("node")
    assert node, "Node is required for the trusted socket tool synthetic E2E"
    executor, db = _enabled(tmp_path)
    router = RuntimeDispatchExecutor(
        state_db=db,
        registry_provider=_registry,
        executors={"pi-json": executor},
    )
    port = PAODelegationChildPort(
        state_db=db,
        executor=router,
        recommendation_factory=_recommendation_factory(executor),
        registry_provider=_registry,
        runtime_available_provider=lambda _: True,
        timeout_seconds=5,
    )
    child_results = []
    original_child = port.execute_child

    async def execute_child(plan):
        result = await original_child(plan)
        store = SafetyKernelStore(db)
        try:
            assert result.verified
            assert store.get_task(plan.parent_task_id).state is TaskState.RUNNING
            assert store.get_workspace(plan.parent_task_id).writer_token is not None
            child_results.append(result)
        finally:
            store.close()
        return result

    monkeypatch.setattr(port, "execute_child", execute_child)
    executor.delegation_child_port = port
    runner = tmp_path / "tool-runner.mjs"
    runner.write_text("""import { pathToFileURL } from "node:url";
let tool;
(await import(pathToFileURL(process.argv[2]).href)).default({ registerTool(t) { tool = t; } });
const args = { intent: "Create hello.txt", reason: "Independent fixture" };
const denied = await tool.execute("forbidden", { ...args, model: "worker-choice" });
const first = await tool.execute("child-call", args);
const replay = await tool.execute("child-call", args);
console.log(JSON.stringify({ first, replay, denied }));
""")
    response_path = tmp_path / "tool-response.json"
    worker = Path(executor.pi_runtime.pi_bin)
    # Fake Pi harness executes the actual seeded TypeScript tool through Node.
    # Child runs have only the guard extension, so they cannot request grandchildren.
    worker.write_text(f"""#!/usr/bin/env python3
import json, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
extensions = [args[i+1] for i, value in enumerate(args) if value == "-e"]
if len(extensions) == 2:
    result = subprocess.run([{node!r}, "--experimental-strip-types", {str(runner)!r},
                             extensions[1]], check=True, capture_output=True, text=True)
    Path({str(response_path)!r}).write_text(result.stdout)
Path("hello.txt").write_text({HELLO!r} + "\\n")
print(json.dumps({{"type": "session", "version": 3, "id": "fixture", "cwd": "fixture"}}))
print(json.dumps({{"type": "agent_start"}}))
print(json.dumps({{"type": "message_end", "message": {{"role": "assistant", "provider": "zai",
    "model": "glm-5.3", "stopReason": "stop", "content": []}}}}))
print(json.dumps({{"type": "agent_end", "messages": []}}))
""")
    await executor.execute_async("dispatch-pi")
    assert len(child_results) == 1
    response = json.loads(response_path.read_text())
    assert response["denied"]["content"][0]["text"] == "PAO delegation unavailable."
    assert response["first"] == response["replay"]
    assert json.loads(response["first"]["content"][0]["text"]) == {
        "child_state": "VERIFIED",
        "verified": True,
    }
    store = SafetyKernelStore(db)
    try:
        assert store.get_task("task-pi").state is TaskState.VERIFIED
        assert store.get_task(child_results[0].child_task_id).state is TaskState.VERIFIED
        assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        parent_events = store.audit_events("task-pi")
        assert any(e["event_type"] == "OWNER_DISPATCH_COMPLETED" for e in parent_events)
    finally:
        store.close()
    assert executor._delegation_brokers == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_daemon_injects_same_router_and_host_recommendation(tmp_path, enabled):
    from types import SimpleNamespace

    from personal_ai_orchestrator.daemon import build_control_service
    from personal_ai_orchestrator.pi_runtime import PiRuntimeConfig
    from personal_ai_orchestrator.runtime_config import RuntimeConfig

    manager = SimpleNamespace(
        registry=_registry,
        pi_runtime_manager=lambda: object(),
        set_verified_execution_lookup=lambda _: None,
        connected_provider_ids=lambda: (),
        runtime_available=lambda _: True,
    )
    kwargs = {"pi_runtime": PiRuntimeConfig(delegation_enabled=True)} if enabled else {}
    service = build_control_service(
        config=RuntimeConfig(catalog_snapshot_id="synthetic", registry=_registry()),
        state_db=tmp_path / "state.db",
        runtime_state_root=tmp_path / "runtime",
        execution_repo=tmp_path / "repo",
        provider_registry_manager=manager,
        **kwargs,
    )
    try:
        pi_executor = service.dispatch_executor._executors["pi"]
        assert pi_executor.pi_runtime.delegation_enabled is enabled
        port = pi_executor.delegation_child_port
        assert isinstance(port, PAODelegationChildPort)
        assert port.executor is service.dispatch_executor
        recommendation = port.recommendation_factory(service.store)
        assert recommendation._quota_refresh_service is service.quota_refresh_service
        assert recommendation._tier_table is service.tier_table
        assert port.provider_registry_manager is manager
        assert pi_executor._delegation_brokers == {}
    finally:
        service.store.close()
