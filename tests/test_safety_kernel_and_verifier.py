import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from personal_ai_orchestrator.worktree_manager import WorktreeManager


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "src" / "value.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_task_submission_is_idempotent_and_state_machine_is_explicit(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    first = store.submit_task(task_id="t1", request_id="r1", intent="implement")
    second = store.submit_task(task_id="t1", request_id="r1", intent="implement")
    assert first == second
    ready = store.transition_task("t1", TaskState.READY, expected_version=0)
    running = store.transition_task("t1", TaskState.RUNNING, expected_version=ready.state_version)
    finished = store.transition_task(
        "t1", TaskState.WORKER_FINISHED, expected_version=running.state_version
    )
    verifying = store.transition_task(
        "t1", TaskState.VERIFYING, expected_version=finished.state_version
    )
    verified = store.transition_task(
        "t1", TaskState.VERIFIED, expected_version=verifying.state_version
    )
    completed = store.transition_task(
        "t1", TaskState.COMPLETED, expected_version=verified.state_version
    )
    assert completed.state is TaskState.COMPLETED
    with pytest.raises(ValueError, match="invalid task transition"):
        store.transition_task("t1", TaskState.READY)


# M1 WP2 -----------------------------------------------------------


def test_task_min_tier_storage_round_trip(tmp_path: Path) -> None:
    """Submit + reopen + read returns the same ``min_tier``."""

    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    record = store.submit_task(
        task_id="t1", request_id="r1", intent="implement", min_tier="T0"
    )
    assert record.min_tier == "T0"
    store.close()

    reopened = SafetyKernelStore(tmp_path / "state.sqlite3")
    try:
        again = reopened.get_task("t1")
    finally:
        reopened.close()
    assert again.min_tier == "T0"


def test_task_min_tier_defaults_to_T1_when_omitted(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    record = store.submit_task(task_id="t1", request_id="r1", intent="implement")
    assert record.min_tier == "T1"


def test_task_min_tier_unknown_value_raises_at_storage(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError, match="min_tier must be one of"):
        store.submit_task(
            task_id="t1", request_id="r1", intent="implement", min_tier="T9"
        )


def test_task_min_tier_change_between_identical_submits_raises(tmp_path: Path) -> None:
    """Two submits with the same request_id but different min_tier conflict.

    The idempotency check now compares min_tier alongside the other
    fields — a typo in a second submission must not silently overwrite
    the first row.
    """

    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement", min_tier="T0")
    with pytest.raises(ValueError, match="request_id already belongs to a different task submission"):
        store.submit_task(
            task_id="t1", request_id="r1", intent="implement", min_tier="T3"
        )


def test_single_writer_lock_fails_closed(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path="/repo",
        worktree_path="/worktrees/t1",
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "worker-a")
    with pytest.raises(RuntimeError, match="active writer"):
        store.acquire_writer("t1", "worker-b")
    released = store.release_writer("t1", "worker-a")
    assert released.writer_token is None


def test_startup_reconciliation_blocks_uncertain_execution(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    store.start_run(run_id="run-1", task_id="t1", worker_id="minimax", pid=12345)
    assert store.reconcile_startup() == ("t1",)
    assert store.get_task("t1").state is TaskState.BLOCKED


def test_routing_decision_ledger_is_idempotent(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-r1", intent="implement")
    payload = {"decision_id": "d1", "request_id": "route-r1"}
    store.record_routing_decision(
        decision_id="d1", request_id="route-r1", task_id="t1", payload=payload
    )
    store.record_routing_decision(
        decision_id="d1", request_id="route-r1", task_id="t1", payload=payload
    )
    with pytest.raises(ValueError, match="conflicting routing decision"):
        store.record_routing_decision(
            decision_id="d2",
            request_id="route-r1",
            task_id="t1",
            payload={"decision_id": "d2"},
        )


def test_worktree_manager_keeps_source_checkout_head_unchanged(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    manager = WorktreeManager(tmp_path / "managed")
    managed = manager.create(repo_path=repo, task_id="PT-1", base_sha=base_sha)
    assert managed.worktree_path.exists()
    assert _git(repo, "rev-parse", "HEAD") == base_sha
    assert _git(managed.worktree_path, "rev-parse", "HEAD") == base_sha
    manager.remove("PT-1")
    assert not managed.worktree_path.exists()


def test_worktree_manager_can_adopt_persisted_worktree_after_restart(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    managed_root = tmp_path / "managed"
    first_manager = WorktreeManager(managed_root)
    created = first_manager.create(repo_path=repo, task_id="PT-restart", base_sha=base_sha)

    restarted_manager = WorktreeManager(managed_root)
    adopted = restarted_manager.adopt(
        repo_path=repo,
        task_id="PT-restart",
        worktree_path=created.worktree_path,
        branch=created.branch,
        base_sha=created.base_sha,
    )
    assert adopted == created
    restarted_manager.remove("PT-restart")
    assert not created.worktree_path.exists()


def test_worktree_manager_rejects_tampered_recovery_metadata(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    managed_root = tmp_path / "managed"
    first_manager = WorktreeManager(managed_root)
    created = first_manager.create(repo_path=repo, task_id="PT-tamper", base_sha=base_sha)

    restarted_manager = WorktreeManager(managed_root)
    with pytest.raises(ValueError, match="branch does not match"):
        restarted_manager.adopt(
            repo_path=repo,
            task_id="PT-tamper",
            worktree_path=created.worktree_path,
            branch="task/not-the-recorded-branch",
            base_sha=created.base_sha,
        )


def test_verifier_passes_trusted_argv_and_scope(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    manager = WorktreeManager(tmp_path / "managed")
    managed = manager.create(repo_path=repo, task_id="PT-verify", base_sha=base_sha)
    (managed.worktree_path / "src" / "value.txt").write_text("two\n", encoding="utf-8")
    profile = VerifierProfile(
        name="fixture",
        allowed_paths=("src",),
        commands=(
            VerifierCommand(
                name="content check",
                argv=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('src/value.txt').read_text() == 'two\\n'",
                ),
            ),
        ),
    )
    result = DeterministicVerifier().verify(
        managed.worktree_path, base_sha=base_sha, profile=profile
    )
    assert result.passed is True
    assert result.changed_paths == ("src/value.txt",)


def test_verifier_rejects_unexpected_changed_file(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    manager = WorktreeManager(tmp_path / "managed")
    managed = manager.create(repo_path=repo, task_id="PT-scope", base_sha=base_sha)
    (managed.worktree_path / "outside.txt").write_text("unexpected\n", encoding="utf-8")
    result = DeterministicVerifier().verify(
        managed.worktree_path,
        base_sha=base_sha,
        profile=VerifierProfile(name="fixture", allowed_paths=("src",)),
    )
    assert result.passed is False
    assert result.unexpected_paths == ("outside.txt",)


@pytest.mark.asyncio
async def test_process_supervisor_cancels_only_owned_process_group(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    child = await supervisor.start(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
    )
    assert child.pid in supervisor.owned_pids()
    await asyncio.sleep(0.05)
    returncode = await supervisor.cancel(child, grace_seconds=0.5)
    assert returncode != 0
    assert child.pid not in supervisor.owned_pids()
