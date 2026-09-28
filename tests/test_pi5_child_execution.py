from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.dispatch_initiator import initiate_owner_dispatch
from personal_ai_orchestrator.dispatch_recommendation_service import DispatchRecommendationService
from personal_ai_orchestrator.pi5_broker import (
    DelegationBrokerContext,
    DelegationBrokerSession,
    DelegationChildPlan,
    delegation_child_task_id,
)
from personal_ai_orchestrator.pi5_child_execution import PAODelegationChildPort
from personal_ai_orchestrator.pi5_contract import DelegationRequest
from personal_ai_orchestrator.pi5_identity import CampaignExecutionIdentityFactory
from personal_ai_orchestrator.pi5b3g_lineage import validate_pi5b3g_child_lineage
from personal_ai_orchestrator.pi_runtime import PiRuntimeConfig, build_pi_json_argv
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.safety_kernel import (
    AUTHORITY_DELEGATED_CHILD,
    SafetyKernelStore,
    TaskState,
    expected_source_state_for_dispatch_authority,
)
from tests.quota_identity_fixtures import snapshot
from tests.test_pi4_planning_projection import _ExecutionEvidence, _service
from tests.test_pi_dispatch_executor import (
    TARGET_ID,
    _executor,
    _git,
    _registry,
    _reserve,
)


def _recommendation_factory(executor):
    evidence = _ExecutionEvidence(datetime.now(UTC))
    quota = snapshot("zai-coding-plan", datetime.now(UTC))
    refresh = SimpleNamespace(
        observations=lambda: (SimpleNamespace(provider_id="zai-coding-plan"),),
        snapshot_for_pool=lambda pool: quota if pool == quota.quota_pool_id else None,
    )

    def recommendation(child_store):
        return DispatchRecommendationService(
            child_store,
            registry_provider=_registry,
            quota_refresh_service=refresh,
            execution_evidence_journal=evidence,
            quota_availability_journal=executor._quota_availability_journal,
            tier_table=None,
            runtime_availability=None,
            runtime_availability_fallback=lambda _: True,
        )

    return recommendation


def _setup(tmp_path, monkeypatch):
    executor, repo, state_db = _executor(tmp_path, complete=True)
    _reserve(repo, state_db)
    store = SafetyKernelStore(state_db)
    store.force_task_scheduling_policy("task-pi", scheduling_policy="BALANCED")
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-pi")
    workspace, error = executor._prepare_worktree(store, dispatch)
    assert workspace is not None and error is None
    store.acquire_writer("task-pi", "parent-writer")
    parent = store.get_task("task-pi")
    store.start_dispatched_worker(
        dispatch_id=dispatch.dispatch_id,
        task_id=parent.task_id,
        expected_task_version=parent.state_version,
        run_id="parent-run",
        worker_id=TARGET_ID,
        writer_token="parent-writer",
        pid=999999,
    )
    parent = store.get_task("task-pi")
    context = DelegationBrokerContext(
        parent_task_id=parent.task_id,
        parent_run_id="parent-run",
        project_id=parent.project_id,
        base_sha=parent.base_sha,
    )
    plan = DelegationChildPlan(
        **{k: v for k, v in vars(context).items() if k not in ("parent_depth", "max_children")},
        child_task_id=delegation_child_task_id(
            parent_task_id=parent.task_id,
            parent_run_id="parent-run",
            ordinal=1,
        ),
        ordinal=1,
        intent="Create hello.txt",
        reason="Independent child",
    )
    router = RuntimeDispatchExecutor(
        state_db=state_db,
        registry_provider=_registry,
        executors={"pi-json": executor},
    )
    port = PAODelegationChildPort(
        state_db=state_db,
        executor=router,
        recommendation_factory=_recommendation_factory(executor),
        registry_provider=_registry,
        runtime_available_provider=lambda _: True,
        timeout_seconds=5,
    )
    executor.pi_runtime = replace(executor.pi_runtime, delegation_enabled=True)
    original_spawn = executor._spawn_worker
    observations = []

    async def spawn(child_store, child_dispatch, managed):
        parent_ws = child_store.get_workspace("task-pi")
        child_ws = child_store.get_workspace(child_dispatch.task_id)
        assert parent_ws.writer_token == "parent-writer"
        assert child_ws.writer_token and child_ws.writer_token != parent_ws.writer_token
        assert child_ws.task_id != parent_ws.task_id
        assert Path(child_ws.worktree_path).resolve() != Path(parent_ws.worktree_path).resolve()
        worker = await original_spawn(child_store, child_dispatch, managed)
        assert worker.argv.count("-e") == 1  # child delegation disabled despite global flag
        assert "pao_delegate" not in worker.argv[worker.argv.index("--tools") + 1]
        assert not executor._delegation_brokers
        observations.append(child_dispatch.execution_target_id)
        return worker

    monkeypatch.setattr(executor, "_spawn_worker", spawn)
    return store, executor, port, context, plan, observations


def _setup_campaign_lineage(tmp_path, monkeypatch, *, corrupt_lineage=False):
    campaign_id = "delegation-campaign-cccccccccccccccccccccccccccccccc"
    identity = CampaignExecutionIdentityFactory(campaign_id).observation(1)
    child_identity = identity.child(ordinal=1)
    executor, repo, state_db = _executor(tmp_path, complete=True)
    store = SafetyKernelStore(state_db)
    base_sha = _git(repo, "rev-parse", "HEAD")
    project = store.register_project(
        project_id="project-pi",
        display_name="Pi Fixture",
        canonical_repo_root=str(repo),
        git_root=str(repo),
        default_branch="main",
        last_known_head=base_sha,
    )
    parent = store.submit_task(
        task_id=identity.parent_task_id,
        request_id=identity.parent_submit_request_id,
        project_id=project.project_id,
        base_sha=base_sha,
        intent="Create hello.txt",
        scheduling_policy="BALANCED",
    )
    dispatch, _ = store.reserve_owner_dispatch(
        dispatch_id=identity.parent_dispatch_id,
        request_id=identity.parent_dispatch_request_id,
        task_id=parent.task_id,
        task_state_version=parent.state_version,
        execution_target_id=TARGET_ID,
        authority="OWNER_INITIATED_EXECUTION",
    )
    parent = store.transition_task(
        parent.task_id,
        TaskState.READY,
        expected_version=parent.state_version,
        reason="campaign fixture ready",
    )
    workspace, error = executor._prepare_worktree(store, dispatch)
    assert workspace is not None and error is None
    store.acquire_writer(parent.task_id, "parent-writer")
    store.start_dispatched_worker(
        dispatch_id=dispatch.dispatch_id,
        task_id=parent.task_id,
        expected_task_version=parent.state_version,
        run_id=identity.parent_run_id,
        worker_id=TARGET_ID,
        writer_token="parent-writer",
        pid=999999,
    )
    context = DelegationBrokerContext(
        parent_task_id=parent.task_id,
        parent_run_id=identity.parent_run_id,
        project_id=project.project_id,
        base_sha=base_sha,
    )
    plan = DelegationChildPlan(
        **{
            key: value
            for key, value in vars(context).items()
            if key not in ("parent_depth", "max_children")
        },
        child_task_id=child_identity.task_id,
        ordinal=1,
        intent="Create hello.txt",
        reason="bounded child fixture",
    )
    router = RuntimeDispatchExecutor(
        state_db=state_db,
        registry_provider=_registry,
        executors={"pi-json": executor},
    )
    port = PAODelegationChildPort(
        state_db=state_db,
        executor=router,
        recommendation_factory=_recommendation_factory(executor),
        registry_provider=_registry,
        runtime_available_provider=lambda _: True,
        timeout_seconds=5,
    )
    executor.pi_runtime = replace(executor.pi_runtime, delegation_enabled=True)
    original_spawn = executor._spawn_worker
    spawned = []

    async def observed_spawn(child_store, child_dispatch, managed):
        supervised = await original_spawn(child_store, child_dispatch, managed)
        spawned.append(supervised)
        return supervised

    monkeypatch.setattr(executor, "_spawn_worker", observed_spawn)
    original_activate = executor._activate_worker_session
    lineage_results = []

    def observed_activate(request_id):
        observer_store = SafetyKernelStore(state_db)
        try:
            child_dispatch = observer_store.get_owner_dispatch_by_request_id(request_id)
            if corrupt_lineage:
                observer_store.connection.execute(
                    "DELETE FROM audit_events WHERE task_id=? AND event_type='TASK_SUBMITTED'",
                    (child_dispatch.task_id,),
                )
            validation = validate_pi5b3g_child_lineage(
                store=observer_store,
                child_task_id=child_dispatch.task_id,
                expected=identity,
                current_campaign_parents={identity.parent_task_id: identity},
            )
            lineage_results.append(validation)
            if not validation.allowed:
                raise RuntimeError(validation.rule_id)
            original_activate(request_id)
        finally:
            observer_store.close()

    monkeypatch.setattr(executor, "_activate_worker_session", observed_activate)
    return store, executor, port, plan, identity, spawned, lineage_results


def test_delegated_authority_and_unknown_fail_closed():
    expected = expected_source_state_for_dispatch_authority(AUTHORITY_DELEGATED_CHILD)
    assert expected is TaskState.READY
    with pytest.raises(ValueError):
        expected_source_state_for_dispatch_authority("not-an-authority")


@pytest.mark.asyncio
async def test_durable_verified_child_preserves_parent_and_idempotency(tmp_path, monkeypatch):
    store, executor, port, context, plan, observations = _setup(tmp_path, monkeypatch)
    try:
        before = store.get_task("task-pi")
        session = DelegationBrokerSession(context=context, child_port=port)
        request = DelegationRequest(
            tool_call_id="call", ordinal=1, intent=plan.intent, reason=plan.reason
        )
        result = await session.handle(request)
        assert result.verified, result
        assert result.selected_execution_target_id == TARGET_ID
        assert store.get_task(plan.child_task_id).state is TaskState.VERIFIED
        assert store.get_task("task-pi") == before
        assert (
            store.connection.execute(
                "SELECT status FROM runs WHERE run_id='parent-run'"
            ).fetchone()[0]
            == "RUNNING"
        )
        assert await session.handle(request) == result
        assert (await port.execute_child(plan)).verified
        assert observations == [TARGET_ID]
        assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        dispatch = store.get_owner_dispatch_by_request_id(
            f"pi5-child-dispatch-{plan.child_task_id}"
        )
        assert dispatch.authority == AUTHORITY_DELEGATED_CHILD
        assert store.get_workspace("task-pi").writer_token == "parent-writer"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fake_child_pipeline_reaches_lineage_ownership_and_protocol(tmp_path, monkeypatch):
    store, _, port, plan, identity, spawned, lineage_results = _setup_campaign_lineage(
        tmp_path, monkeypatch
    )
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert len(spawned) == 1
        assert spawned[0].process.returncode == 0
        assert len(lineage_results) == 1 and lineage_results[0].allowed
        assert lineage_results[0].resolved_parent_task_id == identity.parent_task_id
        events = tuple(event["event_type"] for event in store.audit_events(plan.child_task_id))
        assert events.count("PROCESS_CREATED") == 1
        assert events.count("DURABLE_RUN_REGISTERED") == 1
        assert events.count("WORKER_RUNNING") == 1
        assert events.count("PROTOCOL_BOOTSTRAP_STARTED") == 1
        assert events.count("PROTOCOL_BOOTSTRAP_COMPLETED") == 1
        assert store.get_task(plan.child_task_id).state is TaskState.VERIFIED
        assert store.get_workspace(plan.child_task_id).writer_token is None
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM runs WHERE task_id=?",
                (plan.child_task_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_verified_child_waits_for_writer_and_dispatch_cleanup(tmp_path, monkeypatch):
    store, _, port, plan, _, _, _ = _setup_campaign_lineage(tmp_path, monkeypatch)
    release_started = threading.Event()
    allow_release = threading.Event()
    original_release = SafetyKernelStore.release_writer

    def delayed_release(child_store, task_id, writer_token):
        if task_id == plan.child_task_id:
            release_started.set()
            assert allow_release.wait(timeout=5)
        return original_release(child_store, task_id, writer_token)

    monkeypatch.setattr(SafetyKernelStore, "release_writer", delayed_release)
    execution = asyncio.create_task(port.execute_child(plan))
    try:
        assert await asyncio.to_thread(release_started.wait, 5)
        await asyncio.sleep(0.1)
        assert not execution.done()
    finally:
        allow_release.set()

    result = await execution
    try:
        assert result.verified
        assert store.get_workspace(plan.child_task_id).writer_token is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_lineage_failure_after_durable_run_reaps_process_and_releases_writer(
    tmp_path, monkeypatch
):
    store, executor, port, plan, _, spawned, lineage_results = _setup_campaign_lineage(
        tmp_path, monkeypatch, corrupt_lineage=True
    )
    try:
        result = await port.execute_child(plan)
        assert not result.verified
        assert len(spawned) == 1
        assert spawned[0].process.returncode is not None
        assert len(lineage_results) == 1 and not lineage_results[0].allowed
        assert lineage_results[0].rule_id == "DELEGATED_LINEAGE_TASK_SUBMISSION_MISSING"
        child = store.get_task(plan.child_task_id)
        assert child.state is TaskState.BLOCKED
        assert store.get_workspace(plan.child_task_id).writer_token is None
        run = store.connection.execute(
            "SELECT status FROM runs WHERE task_id=?",
            (plan.child_task_id,),
        ).fetchone()
        assert run is not None and run["status"] != "RUNNING"
        assert executor.execution_supervisor.owned_task_ids() == ()
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE task_id=?",
                (plan.child_task_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "mismatch",
    [
        "missing_parent",
        "missing_run",
        "run_task",
        "run_status",
        "project",
        "base",
        "subpath",
        "manual",
        "cancelled",
    ],
)
@pytest.mark.asyncio
async def test_parent_gate_creates_no_child(tmp_path, monkeypatch, mismatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    try:
        updates = {
            "missing_parent": {"parent_task_id": "missing"},
            "missing_run": {"parent_run_id": "missing"},
            "project": {"project_id": "other"},
            "base": {"base_sha": "other"},
            "subpath": {"working_subpath": "other"},
        }
        if mismatch in updates:
            plan = plan.model_copy(update=updates[mismatch])
        elif mismatch == "manual":
            store.force_task_scheduling_policy("task-pi", scheduling_policy="MANUAL")
        elif mismatch == "run_task":
            store.submit_task(task_id="other", request_id="other", intent="fixture")
            store.connection.execute("UPDATE runs SET task_id='other' WHERE run_id='parent-run'")
            store.connection.commit()
        elif mismatch == "run_status":
            store.finish_run("parent-run", status="FINISHED", result={})
        else:
            task = store.get_task("task-pi")
            store.transition_task(
                task.task_id,
                TaskState.CANCELLED,
                expected_version=task.state_version,
                reason="test cancellation",
            )
        assert not (await port.execute_child(plan)).verified
        assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == (
            2 if mismatch == "run_task" else 1
        )
        assert observations == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_quota_blocks_before_child_dispatch(tmp_path, monkeypatch):
    from personal_ai_orchestrator.quota_availability import observe_exhaustion

    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    try:
        service, journal = _service(tmp_path / "quota", datetime.now(UTC))
        registry = service._registry_provider()
        # Keep only the Pi sibling candidate; the blocker was observed on OpenCode.
        registry.execution_targets.pop("alternate-plan-worker")
        registry.execution_targets.pop("minimax-cn-coding-plan-MiniMax-M3")
        service._registry_provider = lambda: registry
        journal.save(
            observe_exhaustion(
                None,
                execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
                provider_id="minimax-cn-coding-plan",
                quota_pool_id="minimax-cn-coding-plan",
                observed_at=datetime.now(UTC),
                sanitized_reason_code="USAGE_LIMIT",
            )
        )
        port.recommendation_factory = lambda _: service
        result = await port.execute_child(plan)
        assert result.final_state == "BLOCKED" and not result.verified
        assert store.get_task(plan.child_task_id).state is TaskState.BLOCKED
        assert observations == []
        assert store.connection.execute("SELECT COUNT(*) FROM owner_dispatches").fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_frozen_target_failure_never_falls_back(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)

    class RejectExecutor:
        def execute(self, request_id):
            child_store = SafetyKernelStore(port.state_db)
            try:
                child_store.mark_owner_dispatch_blocked(
                    request_id,
                    failure_code="TARGET_UNAVAILABLE",
                    failure_reason="synthetic",
                )
            finally:
                child_store.close()

    try:
        port.executor = RejectExecutor()
        result = await port.execute_child(plan)
        assert result.final_state == "BLOCKED"
        assert result.selected_execution_target_id == TARGET_ID
        port.recommendation_factory = lambda _: pytest.fail("frozen target must not be recomputed")
        assert (await port.execute_child(plan)).selected_execution_target_id == TARGET_ID
        assert observations == []
    finally:
        store.close()


def test_owner_cannot_impersonate_child_namespace(tmp_path):
    store = SafetyKernelStore(tmp_path / "state.db")
    try:
        task = store.submit_task(task_id="task", request_id="submit", intent="test")
        with pytest.raises(ControlPlaneError, match="reserved_dispatch_request_id_namespace"):
            initiate_owner_dispatch(
                store,
                None,
                task=task,
                request_id="pi5-child-dispatch-task",
                task_state_version=0,
                execution_target_id="target",
                project_provider=store.get_project,
                registry_provider=_registry,
                provider_registry_manager=None,
                runtime_available_provider=lambda _: True,
                execution_evidence_journal=None,
            )
        assert store.connection.execute("SELECT COUNT(*) FROM owner_dispatches").fetchone()[0] == 0
    finally:
        store.close()


def test_feature_flag_preserves_allowlist_and_explicit_extensions():
    kwargs = dict(model_ref="zai/glm-5.3", intent="test", guard_path=Path("/host/guard.ts"))
    off = build_pi_json_argv(config=PiRuntimeConfig(), **kwargs)
    assert off == build_pi_json_argv(
        config=PiRuntimeConfig(), delegation_tool_path=Path("/ignored"), **kwargs
    )
    assert off.count("-e") == 1 and "pao_delegate" not in str(off)
    on = build_pi_json_argv(
        config=PiRuntimeConfig(delegation_enabled=True),
        delegation_tool_path=Path("/host/tool.ts"),
        **kwargs,
    )
    assert on.count("-e") == 2 and "--no-extensions" in on
    assert on[on.index("--tools") + 1] == off[off.index("--tools") + 1] + ",pao_delegate"
    tools = on[on.index("--tools") + 1].split(",")
    assert not {"bash", "powershell", "webfetch"}.intersection(tools)
    with pytest.raises(ValueError):
        build_pi_json_argv(config=PiRuntimeConfig(delegation_enabled=True), **kwargs)


@pytest.mark.asyncio
async def test_parent_cancel_between_read_and_submit_fails_atomically(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    original_submit = SafetyKernelStore.submit_task

    def submit(child_store, **kwargs):
        parent = store.get_task("task-pi")
        store.transition_task(
            "task-pi",
            TaskState.CANCELLED,
            expected_version=parent.state_version,
            reason="race fixture",
        )
        return original_submit(child_store, **kwargs)

    monkeypatch.setattr(SafetyKernelStore, "submit_task", submit)
    try:
        assert not (await port.execute_child(plan)).verified
        assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert observations == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_worker_success_without_deterministic_proof_is_not_verified(tmp_path, monkeypatch):
    store, executor, port, _, plan, _ = _setup(tmp_path, monkeypatch)
    try:
        worker = Path(executor.pi_runtime.pi_bin)
        lines = worker.read_text().splitlines()
        worker.write_text("\n".join(line for line in lines if "> hello.txt" not in line) + "\n")
        result = await port.execute_child(plan)
        assert not result.verified
        assert store.get_task(plan.child_task_id).state is TaskState.BLOCKED
        assert store.get_task("task-pi").state is TaskState.RUNNING
    finally:
        store.close()


@pytest.mark.asyncio
async def test_child_wait_is_bounded_without_fabricating_terminal_state(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    try:
        port.executor = SimpleNamespace(execute=lambda _: None)
        port.timeout_seconds = 0.01
        result = await port.execute_child(plan)
        assert result.final_state == "READY" and not result.verified
        assert observations == []
    finally:
        store.close()


def test_child_workspace_alias_fails_before_spawn(tmp_path, monkeypatch):
    store, executor, _, _, _, _ = _setup(tmp_path, monkeypatch)
    try:
        parent = store.get_workspace("task-pi")
        with pytest.raises(ValueError, match="aliases"):
            executor._check_delegated_workspace(
                store,
                SimpleNamespace(authority=AUTHORITY_DELEGATED_CHILD, task_id="child"),
                SimpleNamespace(worktree_path=Path(parent.worktree_path)),
            )
    finally:
        store.close()
