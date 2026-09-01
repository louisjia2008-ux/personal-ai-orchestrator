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
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import (
    ControlPlaneServer,
    ControlPlaneService,
)
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.dispatch_executor import (
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
            "#!/bin/sh\n"
            f"printf '%s\\n' '{body}' > hello.txt\n"
            f"exit {exit_code}\n",
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
    return QuotaCollectionResult(
        status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot
    )


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
    return QuotaCollectionResult(
        status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot
    )


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
            store.submit_task(
                task_id=task_id,
                request_id=f"submit-{task_id}",
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
        assert snapshot["dispatch_failure_code"] == "WORKER_SPAWN_FAILED"
        snapshot["store"].assert_running_invariant("task-1")
    finally:
        _close(snapshot)
    assert harness.main_unchanged()


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
    assert evidence.result.value == "UNKNOWN"


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
        managed = manager.create(
            repo_path=harness.main_repo, task_id="task-1", base_sha=base_sha
        )
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
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(
        exhausted_result()
    )
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


def test_quota_available_collector_admits_and_is_journaled(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.executor._quota_collectors["zai-coding-plan"] = FakeCollector(
        available_result()
    )
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
        worker_bin=write_worker_script(
            tmp_path / "bin", name="slow-worker", sleep_seconds=5.0
        ),
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


def test_full_product_path_over_http_with_cancellation(tmp_path: Path) -> None:
    import tempfile

    harness = ExecutorHarness(
        tmp_path,
        worker_bin=write_worker_script(
            tmp_path / "bin", name="long-worker", sleep_seconds=30.0
        ),
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
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json", initial=True
        ),
        execution_evidence_journal=harness.execution_evidence,
        dispatch_executor=harness.executor,
    )
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path, timeout=30.0)
    try:
        task = client.submit(
            task_id="task-1",
            request_id="submit-task-1",
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
    # The probe judges real worker output, so make it print a line.
    probe_bin.write_text(
        "#!/bin/sh\necho probe-ok\n", encoding="utf-8"
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
    failing_bin = write_worker_script(
        tmp_path, name="probe-fail", exit_code=7
    )
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
