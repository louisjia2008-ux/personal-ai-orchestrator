"""Both worker runtimes receive the registered monorepo focus as context only."""

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.dispatch_executor import DispatchExecutorConfig, OwnerDispatchExecutor
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.pi_dispatch_executor import PiOwnerDispatchExecutor
from personal_ai_orchestrator.pi_runtime import PiRuntimeConfig
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.worktree_manager import ManagedWorktree
from tests.test_dispatch_initiator import _build_service, _git, _make_repo, _registry


class RecordingSupervisor(ProcessSupervisor):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def start(self, argv, *, cwd, env):
        self.calls.append((tuple(argv), Path(cwd), dict(env)))
        return object()


@pytest.mark.parametrize("runtime", ["opencode", "pi"])
@pytest.mark.parametrize("subpath", [None, "apps/desktop", 'apps/桌面 with "quotes"'])
@pytest.mark.asyncio
async def test_registered_project_focus_reaches_worker_without_changing_authority(
    tmp_path, monkeypatch, runtime, subpath
):
    repo = _make_repo(tmp_path / "repo")
    selected = repo if subpath is None else repo / subpath
    selected.mkdir(parents=True, exist_ok=True)
    (selected / "button.txt").write_text("button fixture\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add selected project fixture")
    head = _git(repo, "rev-parse", "HEAD")
    service = _build_service(tmp_path)
    store = service.store
    project = service.register_project({"path": str(selected), "display_name": "Desktop"})
    task = service.submit_task(
        {
            "task_id": "task-1",
            "request_id": "submit-1",
            "intent": "fix the button",
            "project_id": project.project_id,
        }
    )
    assert task.base_sha == head
    assert task.working_subpath == subpath
    dispatch, _ = store.reserve_owner_dispatch(
        dispatch_id="owner-dispatch-request-1",
        request_id="request-1",
        task_id=task.task_id,
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="OWNER_INITIATED_EXECUTION",
    )
    supervisor = RecordingSupervisor()
    journals = tmp_path / "journals"
    kwargs = dict(
        state_db=tmp_path / "safety.db",
        config=DispatchExecutorConfig(repo_path=repo, worktree_root=tmp_path / "worktrees"),
        registry_provider=_registry,
        verification_journal=VerificationEvidenceJournal(journals),
        execution_evidence_journal=ExecutionEvidenceJournal(journals),
        quota_availability_journal=QuotaAvailabilityJournal(journals),
        supervisor=supervisor,
    )
    safe_env = {"TERM": "dumb"}
    monkeypatch.setattr(
        "personal_ai_orchestrator.dispatch_executor.build_worker_env", lambda: safe_env
    )
    monkeypatch.setattr(
        "personal_ai_orchestrator.pi_dispatch_executor.build_worker_env", lambda: safe_env
    )
    executor = (
        PiOwnerDispatchExecutor(pi_runtime=PiRuntimeConfig(delegation_enabled=False), **kwargs)
        if runtime == "pi"
        else OwnerDispatchExecutor(**kwargs)
    )
    worktree = tmp_path / "task-worktree"
    worktree.mkdir()
    managed = ManagedWorktree(
        task_id=task.task_id,
        repo_path=repo,
        worktree_path=worktree,
        branch="test-branch",
        base_sha=head,
    )
    try:
        await executor._spawn_worker(store, dispatch, managed)
        assert len(supervisor.calls) == 1
        argv, cwd, env = supervisor.calls[0]
        assert cwd == worktree  # project focus does not move any confinement boundary
        assert env == safe_env
        assert argv[-2] == "--"
        if subpath is None:
            assert argv[-1] == task.intent
        else:
            assert json.dumps(subpath, ensure_ascii=False) in argv[-1]
            assert argv[-1].endswith("Task:\n" + task.intent)
            assert "Existing worktree and tool restrictions still apply." in argv[-1]
        assert store.get_task(task.task_id).intent == "fix the button"
        assert store.get_task(task.task_id).working_subpath == subpath
    finally:
        store.close()
