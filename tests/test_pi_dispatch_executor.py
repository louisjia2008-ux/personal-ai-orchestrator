from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

from personal_ai_orchestrator.dispatch_executor import DispatchExecutorConfig
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.model_registry import (
    Account,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.pi_dispatch_executor import PiOwnerDispatchExecutor
from personal_ai_orchestrator.pi_runtime import PI_PROTOCOL_ERROR_EXIT, PiRuntimeConfig
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerifierCommand, VerifierProfile

HELLO = "Pi runtime acceptance"
TARGET_ID = "zai-coding-plan-glm-5.3"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "pi-spike@example.invalid")
    _git(path, "config", "user.name", "Pi Spike")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _fake_pi(
    path: Path,
    *,
    complete: bool,
    provider: str = "zai",
    model: str = "glm-5.3",
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    assistant: dict[str, object] = {
        "role": "assistant",
        "provider": provider,
        "model": model,
        "stopReason": stop_reason,
        "content": [],
    }
    if error_message is not None:
        assistant["errorMessage"] = error_message
    message_end = json.dumps({"type": "message_end", "message": assistant})
    session = json.dumps({"type": "session", "version": 3, "id": "s1", "cwd": "fixture"})
    lines = [
        "#!/bin/sh",
        f"printf '%s\\n' '{HELLO}' > hello.txt",
        f"printf '%s\\n' '{session}'",
        "printf '%s\\n' '{\"type\":\"agent_start\"}'",
        f"printf '%s\\n' '{message_end}'",
    ]
    if complete:
        lines.append('printf \'%s\\n\' \'{"type":"agent_end","messages":[]}\'')
    lines.append("exit 0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _registry() -> ModelRegistry:
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
            TARGET_ID: ExecutionTarget(
                id=TARGET_ID,
                model_sku_id="zai-coding-plan/glm-5.3",
                account_id="zai-coding-plan",
                runtime_id="pi-json",
                execution_verified=True,
            )
        },
    )


def _profile() -> VerifierProfile:
    return VerifierProfile(
        name="pi-spike",
        commands=(
            VerifierCommand(name="hello-exists", argv=("test", "-f", "hello.txt")),
            VerifierCommand(
                name="hello-content",
                argv=("sh", "-c", f'test "$(cat hello.txt)" = "{HELLO}"'),
            ),
        ),
        allowed_paths=("hello.txt",),
    )


def _executor(
    tmp_path: Path,
    *,
    complete: bool,
    provider: str = "zai",
    model: str = "glm-5.3",
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> tuple[PiOwnerDispatchExecutor, Path, Path]:
    repo = _make_repo(tmp_path / "repo")
    state_db = tmp_path / "state.sqlite3"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    pi_bin = _fake_pi(
        tmp_path / "bin" / "pi",
        complete=complete,
        provider=provider,
        model=model,
        stop_reason=stop_reason,
        error_message=error_message,
    )
    executor = PiOwnerDispatchExecutor(
        state_db=state_db,
        config=DispatchExecutorConfig(
            repo_path=repo,
            worktree_root=tmp_path / "worktrees",
            verifier_profile=_profile(),
            worker_timeout_seconds=10.0,
        ),
        pi_runtime=PiRuntimeConfig(pi_bin=str(pi_bin)),
        registry_provider=_registry,
        verification_journal=VerificationEvidenceJournal(runtime),
        execution_evidence_journal=ExecutionEvidenceJournal(runtime),
        quota_availability_journal=QuotaAvailabilityJournal(runtime),
        quota_collectors={},
    )
    return executor, repo, state_db


def _reserve(repo: Path, state_db: Path) -> None:
    store = SafetyKernelStore(state_db)
    try:
        base_sha = _git(repo, "rev-parse", "HEAD")
        project = store.register_project(
            project_id="project-pi",
            display_name="Pi Fixture",
            canonical_repo_root=str(repo),
            git_root=str(repo),
            default_branch="main",
            last_known_head=base_sha,
        )
        store.submit_task(
            task_id="task-pi",
            request_id="submit-pi",
            project_id=project.project_id,
            base_sha=base_sha,
            intent=f"Create hello.txt with exactly: {HELLO}",
        )
        store.reserve_owner_dispatch(
            dispatch_id="owner-dispatch-pi",
            request_id="dispatch-pi",
            task_id="task-pi",
            task_state_version=0,
            execution_target_id=TARGET_ID,
            authority="OWNER_INITIATED_EXECUTION",
        )
        store.transition_task(
            "task-pi",
            TaskState.READY,
            expected_version=0,
            reason="pi spike dispatch reserved",
        )
    finally:
        store.close()


def _run_row(store: SafetyKernelStore):
    return store.connection.execute(
        "SELECT status,result_json FROM runs WHERE task_id='task-pi'"
    ).fetchone()


def test_pi_adapter_reuses_existing_host_authority_to_verified(tmp_path: Path) -> None:
    executor, repo, state_db = _executor(tmp_path, complete=True)
    _reserve(repo, state_db)

    executor.execute("dispatch-pi")

    store = SafetyKernelStore(state_db)
    try:
        task = store.get_task("task-pi")
        run = _run_row(store)
        workspace = store.get_workspace("task-pi")
        assert task.state is TaskState.VERIFIED
        assert run is not None and run["status"] == "FINISHED"
        assert '"runtime":"pi-json"' in run["result_json"]
        assert '"pi_protocol_valid":true' in run["result_json"]
        assert '"pi_provider":"zai"' in run["result_json"]
        assert '"pi_model":"glm-5.3"' in run["result_json"]
        assert workspace.writer_token is None
        store.assert_running_invariant("task-pi")
    finally:
        store.close()

    assert _git(repo, "status", "--porcelain") == ""
    worktree = tmp_path / "worktrees" / "task-pi"
    assert (worktree / "hello.txt").read_text(encoding="utf-8").strip() == HELLO
    guard = tmp_path / "worktrees" / ".pao-runtime" / ".pao" / "pi-worktree-guard.ts"
    assert guard.exists()
    assert not (worktree / ".pao").exists()


def test_pi_adapter_fails_closed_on_incomplete_json_stream(tmp_path: Path) -> None:
    executor, repo, state_db = _executor(tmp_path, complete=False)
    _reserve(repo, state_db)

    executor.execute("dispatch-pi")

    store = SafetyKernelStore(state_db)
    try:
        task = store.get_task("task-pi")
        run = _run_row(store)
        assert task.state is TaskState.BLOCKED
        assert run is not None and run["status"] == "FAILED"
        assert f'"exit_code":{PI_PROTOCOL_ERROR_EXIT}' in run["result_json"]
        store.assert_running_invariant("task-pi")
    finally:
        store.close()

    assert _git(repo, "status", "--porcelain") == ""


def test_pi_adapter_fails_closed_when_runtime_reports_wrong_model(tmp_path: Path) -> None:
    executor, repo, state_db = _executor(
        tmp_path,
        complete=True,
        model="glm-5.2",
    )
    _reserve(repo, state_db)

    executor.execute("dispatch-pi")

    store = SafetyKernelStore(state_db)
    try:
        task = store.get_task("task-pi")
        run = _run_row(store)
        assert task.state is TaskState.BLOCKED
        assert run is not None and run["status"] == "FAILED"
        assert f'"exit_code":{PI_PROTOCOL_ERROR_EXIT}' in run["result_json"]
    finally:
        store.close()
    assert _git(repo, "status", "--porcelain") == ""


def test_pi_provider_error_is_failed_worker_and_quota_signal(tmp_path: Path) -> None:
    executor, repo, state_db = _executor(
        tmp_path,
        complete=True,
        stop_reason="error",
        error_message="429: usage limit reached for this plan",
    )
    _reserve(repo, state_db)

    executor.execute("dispatch-pi")

    store = SafetyKernelStore(state_db)
    try:
        task = store.get_task("task-pi")
        run = _run_row(store)
        assert task.state is TaskState.BLOCKED
        assert run is not None and run["status"] == "FAILED"
    finally:
        store.close()

    quota = executor._quota_availability_journal.load(TARGET_ID)
    assert quota is not None
    assert quota.state.value in {"EXHAUSTED_OBSERVED", "COOLDOWN"}
    assert _git(repo, "status", "--porcelain") == ""
