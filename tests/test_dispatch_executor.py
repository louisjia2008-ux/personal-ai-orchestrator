"""Owner-dispatch executor lifecycle and failure-matrix tests.

Every failure path must prove:
- no ghost RUNNING task;
- no orphan active run;
- no orphan writer lock;
- main repository unchanged;
- durable sanitized failure evidence.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import (
    ControlPlaneServer,
    ControlPlaneService,
)
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.dispatch_executor import (
    WORKER_TRANSCRIPT_TAIL_BYTES,
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
    build_worker_env,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    build_execution_evidence,
)
from personal_ai_orchestrator.execution_probe import run_execution_probe
from personal_ai_orchestrator.model_registry import (
    Account,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
    QuotaSnapshot,
    QuotaState,
)
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    TaskState,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import (
    VerifierCommand,
    VerifierProfile,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
HELLO_CONTENT = "Personal AI Orchestrator real worker acceptance."


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_main_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "acceptance@example.invalid")
    _git(path, "config", "user.name", "Acceptance")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def write_worker_script(
    directory: Path,
    *,
    name: str = "fake-opencode",
    body: str | None = None,
    exit_code: int = 0,
    sleep_seconds: float = 0.0,
) -> Path:
    if body is None:
        body = HELLO_CONTENT
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    if sleep_seconds > 0:
        script.write_text(
            "#!/bin/sh\n"
            f"sleep {sleep_seconds}\n"
            f"printf '%s\\n' '{body}' > hello.txt\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
    else:
        script.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{body}' > hello.txt\nexit {exit_code}\n",
            encoding="utf-8",
        )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def make_registry() -> ModelRegistry:
    return ModelRegistry(
        providers={"zai-coding-plan": Provider(id="zai-coding-plan", display_name="GLM")},
        accounts={
            "zai-coding-plan": Account(
                id="zai-coding-plan",
                provider_id="zai-coding-plan",
                label="GLM",
            )
        },
        models={
            "zai-coding-plan/glm-5.3": ModelSKU(
                id="zai-coding-plan/glm-5.3",
                provider_id="zai-coding-plan",
                display_name="glm-5.3",
            )
        },
        execution_targets={
            "zai-coding-plan-glm-5.3": ExecutionTarget(
                id="zai-coding-plan-glm-5.3",
                model_sku_id="zai-coding-plan/glm-5.3",
                account_id="zai-coding-plan",
                runtime_id="opencode",
                execution_verified=True,
            )
        },
    )


def make_profile() -> VerifierProfile:
    return VerifierProfile(
        name="hello-acceptance",
        commands=(
            VerifierCommand(name="hello-exists", argv=("test", "-f", "hello.txt")),
            VerifierCommand(
                name="hello-content",
                argv=(
                    "sh",
                    "-c",
                    f'test "$(cat hello.txt)" = "{HELLO_CONTENT}"',
                ),
            ),
        ),
        allowed_paths=("hello.txt",),
    )


class FakeCollector:
    def __init__(self, result: QuotaCollectionResult) -> None:
        self._result = result

    def collect(self) -> QuotaCollectionResult:
        return self._result


def exhausted_result() -> QuotaCollectionResult:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        confidence=EvidenceConfidence.ESTIMATED,
    )
    snapshot = QuotaSnapshot(
        id="snap-exhausted",
        quota_pool_id="zai-coding-plan",
        provider_id="zai",
        plan_id="plan",
        observed_at=NOW,
        windows=(),
        state=QuotaState.EXHAUSTED,
        confidence=EvidenceConfidence.ESTIMATED,
        source=source,
    )
    return QuotaCollectionResult(status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot)


def available_result() -> QuotaCollectionResult:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        confidence=EvidenceConfidence.ESTIMATED,
    )
    snapshot = QuotaSnapshot(
        id="snap-available",
        quota_pool_id="zai-coding-plan",
        provider_id="zai",
        plan_id="plan",
        observed_at=NOW,
        windows=(),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.ESTIMATED,
        source=source,
    )
    return QuotaCollectionResult(status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot)


class ExecutorHarness:
    def __init__(self, tmp_path: Path, *, worker_bin: Path | None = None) -> None:
        self.tmp_path = tmp_path
        self.main_repo = make_main_repo(tmp_path / "main-repo")
        self.state_db = tmp_path / "state.sqlite3"
        self.runtime_root = tmp_path / "runtime-state"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.worktree_root = tmp_path / "worktrees"
        self.worker_bin = worker_bin or write_worker_script(tmp_path / "bin")
        self.registry = make_registry()
        self.project = None
        self.execution_evidence = ExecutionEvidenceJournal(self.runtime_root)
        self.executor = OwnerDispatchExecutor(
            state_db=self.state_db,
            config=DispatchExecutorConfig(
                repo_path=self.main_repo,
                worktree_root=self.worktree_root,
                opencode_bin=str(self.worker_bin),
                worker_timeout_seconds=30.0,
                verifier_profile=make_profile(),
            ),
            registry_provider=lambda: self.registry,
            verification_journal=VerificationEvidenceJournal(self.runtime_root),
            execution_evidence_journal=self.execution_evidence,
            quota_availability_journal=QuotaAvailabilityJournal(self.runtime_root),
            quota_collectors={},
        )

    def reserve(self, task_id: str = "task-1", request_id: str = "dispatch-1") -> str:
        store = SafetyKernelStore(self.state_db)
        try:
            base_sha = _git(self.main_repo, "rev-parse", "HEAD")
            self.project = store.register_project(
                project_id="project-main",
                display_name="Main Repo",
                canonical_repo_root=str(self.main_repo),
                git_root=str(self.main_repo),
                default_branch="main",
                last_known_head=base_sha,
            )
            store.submit_task(
                task_id=task_id,
                request_id=f"submit-{task_id}",
                project_id=self.project.project_id,
                base_sha=base_sha,
                intent=f"Create hello.txt with exactly: {HELLO_CONTENT}",
            )
            store.reserve_owner_dispatch(
                dispatch_id=f"owner-dispatch-{request_id}",
                request_id=request_id,
                task_id=task_id,
                task_state_version=0,
                execution_target_id="zai-coding-plan-glm-5.3",
                authority="OWNER_INITIATED_EXECUTION",
            )
            store.transition_task(
                task_id,
                TaskState.READY,
                expected_version=0,
                reason="test dispatch reserved",
            )
        finally:
            store.close()
        return request_id

    def run(self, request_id: str) -> None:
        self.executor.execute(request_id)

    def snapshot(self, task_id: str = "task-1") -> dict:
        store = SafetyKernelStore(self.state_db)
        try:
            task = store.get_task(task_id)
            run = store.connection.execute(
                "SELECT status FROM runs WHERE task_id=?", (task_id,)
            ).fetchone()
            try:
                workspace = store.get_workspace(task_id)
                writer_token = workspace.writer_token
            except KeyError:
                writer_token = None
            dispatch = store.connection.execute(
                "SELECT status,failure_code FROM owner_dispatches WHERE task_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return {
                "task": task,
                "run_status": run["status"] if run else None,
                "writer_token": writer_token,
                "dispatch_status": dispatch["status"] if dispatch else None,
                "dispatch_failure_code": dispatch["failure_code"] if dispatch else None,
                "store": store,
            }
        except Exception:
            store.close()
            raise

    def main_unchanged(self) -> bool:
        head = _git(self.main_repo, "rev-parse", "HEAD")
        status = _git(self.main_repo, "status", "--porcelain")
        return status == "" and len(head) == 40


def _close(snapshot: dict) -> None:
    snapshot["store"].close()


def test_worker_permission_config_seeded_into_worktree(tmp_path: Path) -> None:
    policy = tmp_path / "policy" / "opencode.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        '{"$schema": "https://opencode.ai/config.json", '
        '"permission": {"edit": "allow", "bash": "deny", "webfetch": "deny"}}',
        encoding="utf-8",
    )
    harness = ExecutorHarness(tmp_path)
    harness.executor.config = DispatchExecutorConfig(
        repo_path=harness.executor.config.repo_path,
        worktree_root=harness.executor.config.worktree_root,
        opencode_bin=harness.executor.config.opencode_bin,
        verifier_profile=VerifierProfile(
            name="seeded",
            commands=(VerifierCommand(name="hello-exists", argv=("test", "-f", "hello.txt")),),
            allowed_paths=("hello.txt", "opencode.json"),
        ),
        worker_permission_config=policy,
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
    finally:
        _close(snapshot)
    seeded = tmp_path / "worktrees" / "task-1" / "opencode.json"
    assert seeded.read_text(encoding="utf-8") == policy.read_text(encoding="utf-8")


def test_worker_env_excludes_credentials() -> None:
    os.environ.update(
        {
            "ZAI_API_KEY": "canary-zai",
            "MINIMAX_API_KEY": "canary-minimax",
            "OPENAI_API_KEY": "canary-openai",
        }
    )
    try:
        env = build_worker_env()
    finally:
        for name in ("ZAI_API_KEY", "MINIMAX_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
    assert "ZAI_API_KEY" not in env
    assert "MINIMAX_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert all("canary" not in value for value in env.values())
    assert "PATH" in env and "HOME" in env


def test_successful_dispatch_reaches_verified_with_durable_evidence(
    tmp_path: Path,
) -> None:
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert snapshot["run_status"] == "FINISHED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "FINISHED"
        assert snapshot["dispatch_failure_code"] is None
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)

    assert harness.main_unchanged()
    worktree = tmp_path / "worktrees" / "task-1"
    assert (worktree / "hello.txt").read_text(encoding="utf-8").strip() == HELLO_CONTENT
    evidence = harness.execution_evidence.latest_for_target("zai-coding-plan-glm-5.3")
    assert evidence is not None
    assert evidence.result.value == "VERIFIED"
    assert evidence.verification_method.value == "REAL_WORKER_INVOCATION"
    # UNKNOWN quota semantics stay durable and truthful even without a collector.
    quota = QuotaAvailabilityJournal(harness.runtime_root).load("zai-coding-plan-glm-5.3")
    assert quota is not None
    assert quota.state.value == "UNKNOWN"
    assert quota.confidence.value == "UNKNOWN"


def test_worker_spawn_failure_blocks_without_ghost_running(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path, worker_bin=tmp_path / "bin" / "missing-bin")
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "BLOCKED"
        assert snapshot["dispatch_failure_code"] == "EXECUTION_TARGET_NOT_LAUNCHABLE"
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


def test_post_create_spawn_adapter_failure_is_diagnosed_and_reaped(
    tmp_path: Path,
) -> None:
    class BrokenAccountingSupervisor(ProcessSupervisor):
        def __init__(self) -> None:
            super().__init__()
            self.created_pid: int | None = None
            self.start_calls = 0

        async def start(self, argv, *, cwd, env=None):
            self.start_calls += 1
            process = await super().start(argv, cwd=cwd, env=env)
            self.created_pid = process.pid
            # Faithful Campaign C shape: wrapper accounting raises after the
            # OS process is created but before the process is returned.
            next(item for item in [] if item)
            return process

    worker = write_worker_script(tmp_path / "bin", name="post-create-worker", sleep_seconds=30.0)
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    supervisor = BrokenAccountingSupervisor()
    harness.executor._supervisor = supervisor
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "BLOCKED"
        assert snapshot["dispatch_failure_code"] == "WORKER_SPAWN_FAILED"
        dispatch = snapshot["store"].get_owner_dispatch_by_request_id(request_id)
        assert "stage=SPAWN_ADAPTER_POST_CREATE" in (dispatch.failure_reason or "")
        assert "exception=RuntimeError" in (dispatch.failure_reason or "")
        assert "child_created=yes" in (dispatch.failure_reason or "")
        details = [
            event["payload"]
            for event in snapshot["store"].audit_events("task-1")
            if event["event_type"] == "WORKER_SPAWN_FAILED_DETAIL"
        ]
        assert len(details) == 1
        assert details[0]["spawn_diagnostics_version"] == "pao-spawn-diagnostics-v1"
        assert details[0]["spawn_stage"] == "SPAWN_ADAPTER_POST_CREATE"
        assert details[0]["child_created"] is True
        assert details[0]["child_pid_observed"] is True
        assert details[0]["child_exited_before_ownership"] is True
        assert details[0]["safe_exit_code"] == -9
        assert details[0]["durable_run_created"] is False
        assert details[0]["protocol_bootstrap_started"] is False
        assert details[0]["protocol_bootstrap_completed"] is False
        assert "PATH" in details[0]["env_key_names"]
        assert "private-prompt-value" not in json.dumps(details[0])
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)

    assert supervisor.created_pid is not None
    assert supervisor.start_calls == 1
    with pytest.raises(ProcessLookupError):
        os.kill(supervisor.created_pid, 0)
    assert supervisor.owned_pids() == ()
    assert harness.executor.execution_supervisor.owned_task_ids() == ()
    assert harness.main_unchanged()


def test_environment_construction_failure_is_precreate_and_releases_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()

    def fail_environment(*args: object, **kwargs: object) -> dict[str, str]:
        raise RuntimeError("injected environment construction failure")

    monkeypatch.setattr(
        "personal_ai_orchestrator.dispatch_executor.build_worker_env",
        fail_environment,
    )
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_failure_code"] == "WORKER_SPAWN_FAILED"
        details = [
            event["payload"]
            for event in snapshot["store"].audit_events("task-1")
            if event["event_type"] == "WORKER_SPAWN_FAILED_DETAIL"
        ]
        assert len(details) == 1
        assert details[0]["spawn_stage"] == "CONTRACT_VALIDATED"
        assert details[0]["exception_class"] == "RuntimeError"
        assert details[0]["child_created"] is False
        assert details[0]["env_key_names"] == []
    finally:
        _close(snapshot)
    assert harness.executor._supervisor.owned_pids() == ()


def test_happy_path_records_spawn_state_machine(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        events = [event["event_type"] for event in snapshot["store"].audit_events("task-1")]
        stages = [
            "WORKER_ARGV_BUILT",
            "PROCESS_CREATE_STARTED",
            "PROCESS_CREATED",
            "DURABLE_RUN_REGISTRATION_STARTED",
            "RUN_STARTED",
            "DURABLE_RUN_REGISTERED",
            "WORKER_RUNNING",
            "PROTOCOL_BOOTSTRAP_STARTED",
            "PROTOCOL_BOOTSTRAP_COMPLETED",
        ]
        assert [event for event in events if event in stages] == stages
        assert snapshot["task"].state is TaskState.VERIFIED
        assert snapshot["run_status"] == "FINISHED"
        assert snapshot["writer_token"] is None
    finally:
        _close(snapshot)


def test_run_registration_failure_has_stage_and_cleans_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = write_worker_script(
        tmp_path / "bin", name="run-registration-worker", sleep_seconds=30.0
    )
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()

    def fail_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected durable start failure")

    monkeypatch.setattr(SafetyKernelStore, "start_dispatched_worker", fail_start)
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_failure_code"] == "RUN_START_FAILED"
        details = [
            event["payload"]
            for event in snapshot["store"].audit_events("task-1")
            if event["event_type"] == "RUN_START_FAILED_DETAIL"
        ]
        assert len(details) == 1
        assert details[0]["spawn_stage"] == "DURABLE_RUN_REGISTRATION_STARTED"
        assert details[0]["exception_class"] == "RuntimeError"
        assert details[0]["child_created"] is True
        assert details[0]["child_exited_before_ownership"] is True
        assert details[0]["durable_run_created"] is False
    finally:
        _close(snapshot)
    assert harness.executor._supervisor.owned_pids() == ()


def test_ownership_registration_failure_repairs_durable_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = write_worker_script(
        tmp_path / "bin", name="ownership-registration-worker", sleep_seconds=30.0
    )
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()

    def fail_register(execution: object) -> None:
        raise RuntimeError("injected ownership registration failure")

    monkeypatch.setattr(harness.executor.execution_supervisor, "register", fail_register)
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FAILED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_failure_code"] == "EXECUTOR_INTERNAL_ERROR"
        details = [
            event["payload"]
            for event in snapshot["store"].audit_events("task-1")
            if event["event_type"] == "WORKER_OWNERSHIP_REGISTRATION_FAILED_DETAIL"
        ]
        assert len(details) == 1
        assert details[0]["spawn_stage"] == "DURABLE_RUN_REGISTERED"
        assert details[0]["exception_class"] == "RuntimeError"
        assert details[0]["durable_run_created"] is True
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.executor._supervisor.owned_pids() == ()
    assert harness.executor.execution_supervisor.owned_task_ids() == ()


def test_protocol_bootstrap_failure_records_started_not_completed(tmp_path: Path) -> None:
    worker = write_worker_script(
        tmp_path / "bin", name="protocol-bootstrap-worker", sleep_seconds=30.0
    )
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()

    async def fail_protocol(_supervised: object) -> tuple[int, bytes, bytes, bool]:
        raise RuntimeError("injected protocol bootstrap failure")

    harness.executor._wait_for_worker = fail_protocol  # type: ignore[method-assign]
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        events = snapshot["store"].audit_events("task-1")
        assert any(event["event_type"] == "PROTOCOL_BOOTSTRAP_STARTED" for event in events)
        assert not any(event["event_type"] == "PROTOCOL_BOOTSTRAP_COMPLETED" for event in events)
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FAILED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_failure_code"] == "EXECUTOR_INTERNAL_ERROR"
    finally:
        _close(snapshot)
    assert harness.executor._supervisor.owned_pids() == ()


def test_worker_nonzero_exit_blocks_and_releases_lock(tmp_path: Path) -> None:
    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(tmp_path / "bin", name="failing", exit_code=3),
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FAILED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "FINISHED"
        assert snapshot["dispatch_failure_code"] == "TASK_BLOCKED"
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


def test_verifier_failure_blocks_verified(tmp_path: Path) -> None:
    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(
            tmp_path / "bin", name="wrong-content", body="wrong content"
        ),
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FINISHED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_failure_code"] == "TASK_BLOCKED"
    finally:
        _close(snapshot)
    evidence = harness.execution_evidence.latest_for_target("zai-coding-plan-glm-5.3")
    assert evidence is not None
    # The real invocation itself succeeded (exit 0), so execution
    # capability stays VERIFIED even though the task is BLOCKED.
    assert evidence.result.value == "VERIFIED"
    assert evidence.reason_code == "REAL_WORKER_DISPATCH_SUCCEEDED"


def test_worktree_creation_failure_blocks_sanitized(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    # Pre-create the exact managed worktree path so allocation fails.
    (harness.worktree_root / "task-1").mkdir(parents=True, exist_ok=True)
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["dispatch_failure_code"] == "WORKTREE_ALREADY_EXISTS"
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


def test_writer_lock_conflict_blocks(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()
    store = SafetyKernelStore(harness.state_db)
    try:
        from personal_ai_orchestrator.worktree_manager import WorktreeManager

        base_sha = _git(harness.main_repo, "rev-parse", "HEAD")
        manager = WorktreeManager(harness.worktree_root)
        managed = manager.create(repo_path=harness.main_repo, task_id="task-1", base_sha=base_sha)
        store.register_workspace(
            task_id="task-1",
            repo_path=str(managed.repo_path),
            worktree_path=str(managed.worktree_path),
            branch=managed.branch,
            base_sha=managed.base_sha,
        )
        store.acquire_writer("task-1", "someone-else")
    finally:
        store.close()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["writer_token"] == "someone-else"  # not stolen
        assert snapshot["dispatch_failure_code"] == "WRITER_LOCK_UNAVAILABLE"
    finally:
        _close(snapshot)


def test_quota_exhausted_blocks_billable_launch(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(exhausted_result())
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["dispatch_failure_code"] == "QUOTA_EXHAUSTED"
    finally:
        _close(snapshot)
    journal = QuotaAvailabilityJournal(harness.runtime_root)
    evidence = journal.load("zai-coding-plan-glm-5.3")
    assert evidence.state.value in {"EXHAUSTED_OBSERVED", "COOLDOWN"}


def test_quota_unknown_blocks_when_certainty_required(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.executor.config = DispatchExecutorConfig(
        repo_path=harness.executor.config.repo_path,
        worktree_root=harness.executor.config.worktree_root,
        opencode_bin=harness.executor.config.opencode_bin,
        verifier_profile=make_profile(),
        require_quota_certainty=True,
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["dispatch_failure_code"] == "QUOTA_UNKNOWN"
    finally:
        _close(snapshot)


def test_quota_journal_exhausted_blocks_even_without_collector(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    from personal_ai_orchestrator.quota_availability import observe_exhaustion

    journal = QuotaAvailabilityJournal(harness.runtime_root)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="zai-coding-plan-glm-5.3",
            provider_id="zai-coding-plan",
            quota_pool_id="zai-coding-plan",
            observed_at=datetime.now(UTC),
            sanitized_reason_code="TEST_SETUP",
        )
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["dispatch_failure_code"] == "QUOTA_EXHAUSTED"
    finally:
        _close(snapshot)


def test_quota_uncertain_locked_blocks_after_three_consecutive_failures(
    tmp_path: Path,
) -> None:
    """A target whose journal has UNCERTAIN_LOCKED must be rejected.

    The lock fires after three consecutive failed quota collections
    (no collector → unknown_availability with previous=previous), so this
    test simulates that exact journal state and confirms admission rejects
    with QUOTA_UNKNOWN regardless of `` ``require_quota_certainty``.
    """

    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityState,
        unknown_availability,
    )

    harness = ExecutorHarness(tmp_path)
    journal = QuotaAvailabilityJournal(harness.runtime_root)
    streak = None
    for index in range(3):
        streak = unknown_availability(
            execution_target_id="zai-coding-plan-glm-5.3",
            provider_id="zai-coding-plan",
            quota_pool_id="zai-coding-plan",
            observed_at=datetime.now(UTC) + timedelta(seconds=index + 1),
            previous=streak,
        )
        journal.save(streak)
    assert streak.state_at(now=datetime.now(UTC)) is (QuotaAvailabilityState.UNCERTAIN_LOCKED)

    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["dispatch_failure_code"] == "QUOTA_UNKNOWN"
    finally:
        _close(snapshot)


def test_quota_require_quota_certainty_false_does_not_relax_uncertain_locked(
    tmp_path: Path,
) -> None:
    """``require_quota_certainty=False`` must not unlock UNCERTAIN_LOCKED.

    The lock fires for *the lack of evidence* (consecutive UNKNOWNs), not for
    the lack of certainty after evidence. With ``require_quota_certainty``
    at its default (``False``), a collector that keeps returning UNKNOWN
    must still drive the streak and trip the lock on the third dispatch.
    The rejection reason must carry the failure count so the owner can
    distinguish "host could not probe" from "probe said quota unknown".
    """

    from personal_ai_orchestrator.quota_availability import (
        UNCERTAIN_LOCKED_THRESHOLD,
        QuotaAvailabilityState,
    )

    harness = ExecutorHarness(tmp_path)
    # Force the default (False) explicitly so the test cannot silently
    # drift if the config default changes.
    assert harness.executor.config.require_quota_certainty is False
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN)
    )
    journal = QuotaAvailabilityJournal(harness.runtime_root)

    outcomes = []
    for index in range(UNCERTAIN_LOCKED_THRESHOLD):
        request_id = f"dispatch-uncert-{index}"
        harness.reserve(task_id=f"task-uncert-{index}", request_id=request_id)
        harness.run(request_id)
        snapshot = harness.snapshot(task_id=f"task-uncert-{index}")
        try:
            outcomes.append(
                {
                    "state": snapshot["task"].state,
                    "failure_code": snapshot["dispatch_failure_code"],
                }
            )
        finally:
            _close(snapshot)

    # First two dispatches admit even though the underlying state is UNKNOWN
    # (require_quota_certainty=False), but each one bumps the streak.
    assert outcomes[0]["state"] is TaskState.VERIFIED
    assert outcomes[0]["failure_code"] is None
    assert outcomes[1]["state"] is TaskState.VERIFIED
    assert outcomes[1]["failure_code"] is None

    # Third dispatch trips the lock; the rejection reason must carry the
    # failure count so the owner can read the streak directly off the
    # QuotaAdmission.evidence rather than only via the failure_code.
    assert outcomes[2]["state"] is TaskState.BLOCKED
    assert outcomes[2]["failure_code"] == "QUOTA_UNKNOWN"

    evidence = journal.load("zai-coding-plan-glm-5.3")
    assert evidence is not None
    assert evidence.consecutive_failures >= UNCERTAIN_LOCKED_THRESHOLD
    assert evidence.state_at(now=datetime.now(UTC)) is (QuotaAvailabilityState.UNCERTAIN_LOCKED)


def test_quota_unknown_collector_growth_stays_locked_without_relaxation(
    tmp_path: Path,
) -> None:
    """No-collector branch (branch A) must keep the lock, not bypass it.

    When the provider has no registered collector, ``_admit_quota`` falls
    through to ``unknown_availability(previous=...)`` on every call. The
    streak therefore grows exactly as it would for a real UNKNOWN-collecting
    provider, and the lock must fire after ``UNCERTAIN_LOCKED_THRESHOLD``
    consecutive calls. The lack of a collector must never be mistaken for
    a clean billable launch — the host must NOT relax the lock just
    because it has no probe.
    """

    from personal_ai_orchestrator.quota_availability import (
        UNCERTAIN_LOCKED_THRESHOLD,
        QuotaAvailabilityState,
    )

    harness = ExecutorHarness(tmp_path)
    # No collector registered for this provider — exercises the
    # ``collector is None`` branch of ``_admit_quota``.
    assert "zai-coding-plan" not in harness.executor._quota_collectors
    journal = QuotaAvailabilityJournal(harness.runtime_root)

    for index in range(UNCERTAIN_LOCKED_THRESHOLD):
        request_id = f"dispatch-no-collector-{index}"
        harness.reserve(task_id=f"task-no-collector-{index}", request_id=request_id)
        harness.run(request_id)

    # The third call's evidence must be UNCERTAIN_LOCKED and the task
    # must be BLOCKED, not silently admitted.
    final = harness.snapshot(task_id="task-no-collector-2")
    try:
        assert final["task"].state is TaskState.BLOCKED
        assert final["dispatch_failure_code"] == "QUOTA_UNKNOWN"
    finally:
        _close(final)

    # The journal must record consecutive_failures == threshold, NOT be
    # silently cleared because the provider has no collector.
    evidence = journal.load("zai-coding-plan-glm-5.3")
    assert evidence is not None
    assert evidence.consecutive_failures == UNCERTAIN_LOCKED_THRESHOLD
    assert evidence.state_at(now=datetime.now(UTC)) is (QuotaAvailabilityState.UNCERTAIN_LOCKED)
    assert evidence.confidence is EvidenceConfidence.UNKNOWN


def test_quota_uncertain_locked_clears_after_successful_collector_run(
    tmp_path: Path,
) -> None:
    """A single successful observation resets the streak and admits.

    Once the collector produces an AVAILABLE outcome the lock should
    release on the next dispatch, not stay sticky. The host must never
    need an owner action to clear it.
    """

    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityState,
        unknown_availability,
    )

    harness = ExecutorHarness(tmp_path)
    journal = QuotaAvailabilityJournal(harness.runtime_root)
    streak = None
    for index in range(3):
        streak = unknown_availability(
            execution_target_id="zai-coding-plan-glm-5.3",
            provider_id="zai-coding-plan",
            quota_pool_id="zai-coding-plan",
            observed_at=datetime.now(UTC) + timedelta(seconds=index + 1),
            previous=streak,
        )
        journal.save(streak)
    # A successful collector produces observe_success() which writes a
    # 0-streak AVAILABLE row and drops the lock.
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(available_result())
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert snapshot["dispatch_failure_code"] is None
    finally:
        _close(snapshot)
    evidence = journal.load("zai-coding-plan-glm-5.3")
    assert evidence is not None
    assert evidence.consecutive_failures == 0
    assert evidence.state_at(now=datetime.now(UTC)) is (QuotaAvailabilityState.AVAILABLE_OBSERVED)


def test_quota_two_failures_still_admits_below_threshold(tmp_path: Path) -> None:
    """Two consecutive UNKNOWNs is still below the threshold.

    Admission MUST allow the dispatch — the lock only fires after the third
    consecutive failure, not on the second.
    """

    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityState,
        unknown_availability,
    )

    harness = ExecutorHarness(tmp_path)
    journal = QuotaAvailabilityJournal(harness.runtime_root)
    streak = None
    for index in range(2):
        streak = unknown_availability(
            execution_target_id="zai-coding-plan-glm-5.3",
            provider_id="zai-coding-plan",
            quota_pool_id="zai-coding-plan",
            observed_at=datetime.now(UTC) + timedelta(seconds=index + 1),
            previous=streak,
        )
        journal.save(streak)
    assert streak.state_at(now=datetime.now(UTC)) is QuotaAvailabilityState.UNKNOWN

    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(available_result())
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
    finally:
        _close(snapshot)


def test_quota_available_collector_admits_and_is_journaled(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(available_result())
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert snapshot["dispatch_failure_code"] is None
    finally:
        _close(snapshot)
    journal = QuotaAvailabilityJournal(harness.runtime_root)
    evidence = journal.load("zai-coding-plan-glm-5.3")
    assert evidence.state.value == "AVAILABLE_OBSERVED"


def test_missing_verifier_profile_fails_closed(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.executor.config = DispatchExecutorConfig(
        repo_path=harness.executor.config.repo_path,
        worktree_root=harness.executor.config.worktree_root,
        opencode_bin=harness.executor.config.opencode_bin,
        verifier_profile=None,
    )
    request_id = harness.reserve()
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FINISHED"
        assert "no host verifier profile" in json.dumps(
            [e["payload"] for e in snapshot["store"].audit_events("task-1")]
        )
    finally:
        _close(snapshot)


def test_running_invariant_holds_during_execution(tmp_path: Path) -> None:
    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(tmp_path / "bin", name="slow-worker", sleep_seconds=5.0),
    )
    request_id = harness.reserve()

    result: dict = {}

    def _run() -> None:
        harness.run(request_id)

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = harness.snapshot()
            try:
                if snapshot["task"].state is TaskState.RUNNING:
                    snapshot["store"].assert_running_invariant("task-1")
                    assert snapshot["writer_token"] is not None
                    assert snapshot["run_status"] == "RUNNING"
                    result["running_seen"] = True
                    break
            finally:
                _close(snapshot)
            time.sleep(0.05)
        assert result.get("running_seen") is True
    finally:
        thread.join(timeout=60)
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
    finally:
        _close(snapshot)


def test_internal_executor_error_after_running_emergency_repairs_state(
    tmp_path: Path,
) -> None:
    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(tmp_path / "bin", name="repair-worker", sleep_seconds=30.0),
    )
    request_id = harness.reserve()

    async def broken_wait(_supervised):
        raise RuntimeError("injected post-running failure")

    harness.executor._wait_for_worker = broken_wait  # type: ignore[method-assign]
    harness.run(request_id)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] == "FAILED"
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "BLOCKED"
        assert snapshot["dispatch_failure_code"] == "EXECUTOR_INTERNAL_ERROR"
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.executor._supervisor.owned_pids() == ()
    assert harness.executor.execution_supervisor.owned_task_ids() == ()
    assert harness.main_unchanged()


def test_emergency_repair_persists_signal_in_run_result_json(tmp_path: Path) -> None:
    """SIGKILL emergency repair must produce a self-describing run result.

    Previously the run row was finished with ``{"emergency_repair": True}``,
    which gave the owner nothing actionable. The post-A3 path records the
    signal that killed the worker (SIGKILL == 9) plus the emergency_repair
    sentinel, so the UI can render a meaningful diagnosis.
    """

    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(tmp_path / "bin", name="signal-worker", sleep_seconds=30.0),
    )
    request_id = harness.reserve()

    async def broken_wait(_supervised):
        raise RuntimeError("injected post-running failure")

    harness.executor._wait_for_worker = broken_wait  # type: ignore[method-assign]
    harness.run(request_id)

    store = SafetyKernelStore(harness.state_db)
    try:
        run = store.connection.execute(
            "SELECT status, result_json FROM runs WHERE task_id='task-1'"
        ).fetchone()
        assert run is not None
        assert run["status"] == "FAILED"
        payload = json.loads(run["result_json"])
        # Signal is SIGKILL (the supervisor always uses killpg(SIGKILL)).
        assert payload["signal"] == 9
        assert payload["emergency_repair"] is True
        # Tails were never captured (supervisor does not drain in emergency),
        # but the field names are guaranteed for cross-path consistency.
        assert "stdout_tail" not in payload
        assert "stderr_tail" not in payload

        dispatch = store.connection.execute(
            "SELECT failure_reason FROM owner_dispatches "
            "WHERE task_id='task-1' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert dispatch is not None
        # The owner-facing reason names the signal so the dashboard banner
        # can show "executor emergency repair: worker exited unexpectedly
        # (signal 9)" without reaching into the run row.
        assert "signal 9" in dispatch["failure_reason"]
        assert "RuntimeError" in dispatch["failure_reason"]
    finally:
        store.close()


def test_dispatch_blocks_task_without_registered_project(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    store = SafetyKernelStore(harness.state_db)
    try:
        store.submit_task(
            task_id="legacy-task",
            request_id="submit-legacy-task",
            intent=f"Create hello.txt with exactly: {HELLO_CONTENT}",
        )
        store.reserve_owner_dispatch(
            dispatch_id="owner-dispatch-legacy",
            request_id="dispatch-legacy",
            task_id="legacy-task",
            task_state_version=0,
            execution_target_id="zai-coding-plan-glm-5.3",
            authority="OWNER_INITIATED_EXECUTION",
        )
        store.transition_task(
            "legacy-task",
            TaskState.READY,
            expected_version=0,
            reason="legacy direct state",
        )
    finally:
        store.close()

    harness.run("dispatch-legacy")
    snapshot = harness.snapshot("legacy-task")
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
        assert snapshot["writer_token"] is None
        assert snapshot["dispatch_status"] == "BLOCKED"
        assert snapshot["dispatch_failure_code"] == "MISSING_PROJECT_ID"
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


def test_full_product_path_over_http_with_cancellation(tmp_path: Path) -> None:
    import tempfile

    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(tmp_path / "bin", name="long-worker", sleep_seconds=30.0),
    )
    socket_dir = Path(tempfile.mkdtemp(prefix="pao-exec-"))
    socket_path = socket_dir / "control.sock"
    store = SafetyKernelStore(harness.state_db)
    store.close()
    service = ControlPlaneService(
        registry=harness.registry,
        store=SafetyKernelStore(harness.state_db),
        runtime_availability={"zai-coding-plan-glm-5.3": True},
        verification_journal=VerificationEvidenceJournal(harness.runtime_root),
        quota_availability_journal=QuotaAvailabilityJournal(harness.runtime_root),
        owner_execution=OwnerExecutionSettings(tmp_path / "owner-execution.json", initial=True),
        execution_evidence_journal=harness.execution_evidence,
        dispatch_executor=harness.executor,
    )
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path, timeout=30.0)
    try:
        project = client.register_project(path=str(harness.main_repo), display_name="Harness")
        task = client.submit(
            task_id="task-1",
            request_id="submit-task-1",
            project_id=project.project_id,
            intent=f"Create hello.txt with exactly: {HELLO_CONTENT}",
        )
        dispatch = client.dispatch(
            "task-1",
            request_id="dispatch-1",
            task_state_version=task.state_version,
            execution_target_id="zai-coding-plan-glm-5.3",
        )
        assert dispatch.accepted is True

        deadline = time.monotonic() + 15
        running = False
        while time.monotonic() < deadline:
            current = client.get_task("task-1")
            if current.state == "RUNNING":
                running = True
                break
            time.sleep(0.1)
        assert running, "task never reached RUNNING"

        cancel = client.cancel("task-1")
        assert cancel.task.state == "CANCELLED"

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            detail = client.get_dispatch("dispatch-1")
            if detail.status in {"CANCELLED", "FINISHED", "BLOCKED"}:
                break
            time.sleep(0.1)
        assert detail.status == "CANCELLED"
    finally:
        server.stop()
        service.store.close()
        import shutil as _shutil

        _shutil.rmtree(socket_dir, ignore_errors=True)

    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.CANCELLED
        assert snapshot["writer_token"] is None
        assert snapshot["run_status"] == "CANCELLED"
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


def test_execution_probe_establishes_verified_only_on_real_success(
    tmp_path: Path,
) -> None:
    probe_bin = write_worker_script(tmp_path, name="probe-ok")
    # The probe judges the exact marker, not merely nonempty worker output.
    probe_bin.write_text(
        "#!/bin/sh\necho PERSONAL-AI-ORCHESTRATOR-EXECUTION-PROBE-OK\n",
        encoding="utf-8",
    )
    probe_bin.chmod(probe_bin.stat().st_mode | stat.S_IXUSR)
    evidence_root = tmp_path / "evidence"
    rendered = run_execution_probe(
        opencode_bin=str(probe_bin),
        provider_id="zai-coding-plan",
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=evidence_root,
    )
    assert rendered["result"] == "VERIFIED"

    journal = ExecutionEvidenceJournal(evidence_root)
    assert journal.target_has_verified_evidence("zai-coding-plan-glm-5.3") is True

    # A later failing probe is newer adverse evidence: the target must
    # not remain launch-verified.
    failing_bin = write_worker_script(tmp_path, name="probe-fail", exit_code=7)
    rendered_fail = run_execution_probe(
        opencode_bin=str(failing_bin),
        provider_id="zai-coding-plan",
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=evidence_root,
    )
    assert rendered_fail["result"] == "UNKNOWN"
    assert journal.target_has_verified_evidence("zai-coding-plan-glm-5.3") is False


def test_execution_probe_rejects_non_marker_stdout(tmp_path: Path) -> None:
    probe_bin = write_worker_script(tmp_path, name="probe-no-marker")
    probe_bin.write_text("#!/bin/sh\necho COMPLETE\n", encoding="utf-8")
    probe_bin.chmod(probe_bin.stat().st_mode | stat.S_IXUSR)
    evidence_root = tmp_path / "evidence"

    rendered = run_execution_probe(
        opencode_bin=str(probe_bin),
        provider_id="zai-coding-plan",
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=evidence_root,
    )

    assert rendered["result"] == "UNKNOWN"
    journal = ExecutionEvidenceJournal(evidence_root)
    assert journal.target_has_verified_evidence("zai-coding-plan-glm-5.3") is False


def test_failed_probe_never_establishes_verified(tmp_path: Path) -> None:
    failing_bin = write_worker_script(tmp_path, name="probe-only-fail", exit_code=1)
    evidence_root = tmp_path / "evidence"
    rendered = run_execution_probe(
        opencode_bin=str(failing_bin),
        provider_id="zai-coding-plan",
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=evidence_root,
    )
    assert rendered["result"] == "UNKNOWN"
    journal = ExecutionEvidenceJournal(evidence_root)
    assert journal.target_has_verified_evidence("zai-coding-plan-glm-5.3") is False


def test_launch_gate_accepts_evidence_backed_unverified_target(tmp_path: Path) -> None:
    from personal_ai_orchestrator.execution_controller import (
        validate_execution_target_launch,
    )

    registry = make_registry()
    # Downgrade to the discovered default: False.
    registry = registry.model_copy(
        update={
            "execution_targets": {
                "zai-coding-plan-glm-5.3": ExecutionTarget(
                    id="zai-coding-plan-glm-5.3",
                    model_sku_id="zai-coding-plan/glm-5.3",
                    account_id="zai-coding-plan",
                    runtime_id="opencode",
                    execution_verified=False,
                )
            }
        }
    )
    journal = ExecutionEvidenceJournal(tmp_path)
    with pytest.raises(RuntimeError, match="not been runtime-verified"):
        validate_execution_target_launch(
            registry,
            execution_target_id="zai-coding-plan-glm-5.3",
            runtime_available=True,
            execution_evidence_journal=journal,
        )
    journal.append(
        build_execution_evidence(
            provider_id="zai-coding-plan",
            execution_target_id="zai-coding-plan-glm-5.3",
            model_sku_id="zai-coding-plan/glm-5.3",
            result=__import__(
                "personal_ai_orchestrator.execution_evidence", fromlist=["x"]
            ).ExecutionVerificationOutcome.VERIFIED,
            reason_code="TEST_REAL_PROBE",
        )
    )
    validate_execution_target_launch(
        registry,
        execution_target_id="zai-coding-plan-glm-5.3",
        runtime_available=True,
        execution_evidence_journal=journal,
    )


def test_launch_gate_rejects_stale_execution_evidence(tmp_path: Path) -> None:
    from personal_ai_orchestrator.execution_controller import (
        validate_execution_target_launch,
    )
    from personal_ai_orchestrator.execution_evidence import (
        ExecutionVerificationOutcome,
    )

    registry = make_registry().model_copy(
        update={
            "execution_targets": {
                "zai-coding-plan-glm-5.3": ExecutionTarget(
                    id="zai-coding-plan-glm-5.3",
                    model_sku_id="zai-coding-plan/glm-5.3",
                    account_id="zai-coding-plan",
                    runtime_id="opencode",
                    execution_verified=False,
                )
            }
        }
    )
    journal = ExecutionEvidenceJournal(tmp_path)
    journal.append(
        build_execution_evidence(
            provider_id="zai-coding-plan",
            execution_target_id="zai-coding-plan-glm-5.3",
            model_sku_id="zai-coding-plan/glm-5.3",
            observed_at=datetime.now(UTC) - timedelta(days=45),
            result=ExecutionVerificationOutcome.VERIFIED,
            reason_code="TEST_STALE_REAL_PROBE",
        )
    )

    with pytest.raises(RuntimeError, match="not been runtime-verified"):
        validate_execution_target_launch(
            registry,
            execution_target_id="zai-coding-plan-glm-5.3",
            runtime_available=True,
            execution_evidence_journal=journal,
        )


def test_host_result_envelope_carries_sanitized_transcript_tails() -> None:
    """The owner can finally see what the worker did, safely.

    Tails are bounded, ANSI-free, control-character-free; hashes still
    cover the full bytes. Worker text remains display evidence only.
    """

    executor = OwnerDispatchExecutor.__new__(OwnerDispatchExecutor)
    noisy = (
        "\x1b[93m\x1b[1m! \x1b[0mpermission requested: edit (README.md)"
        "；auto-rejecting\n→ Read README.md\n✗ Edit README.md failed\n"
        "Error: rejected\x00\x07\n"
    ).encode()
    big = b"x" * (WORKER_TRANSCRIPT_TAIL_BYTES + 4096) + b"|TAIL-MARK|"

    envelope = executor._host_result_envelope(0, stdout=b"", stderr=big + noisy, truncated=True)

    assert envelope["stdout_bytes"] == 0
    assert envelope["stderr_bytes"] == len(big) + len(noisy)
    assert envelope["output_truncated"] is True
    # ANSI and control characters are gone; the narration survives.
    assert "\x1b" not in envelope["stderr_tail"]
    assert "\x00" not in envelope["stderr_tail"]
    assert "permission requested: edit (README.md)" in envelope["stderr_tail"]
    assert "auto-rejecting" in envelope["stderr_tail"]
    # The narration lands at the end of the window and the tail stays
    # bounded by the transcript cap (bytes, not characters).
    assert envelope["stderr_tail"].endswith("Error: rejected\n")
    assert len(envelope["stderr_tail"].encode("utf-8")) <= WORKER_TRANSCRIPT_TAIL_BYTES
    assert envelope["stdout_tail"] == ""
