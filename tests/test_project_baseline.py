"""Real Git and control-plane contracts for explicit task source selection."""

import os
import subprocess
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import ControlPlaneError, ControlPlaneService
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.project_baseline import (
    BaselineError,
    preview_baseline,
    select_baseline,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.worktree_manager import WorktreeManager


def git(root, *args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), *args], stderr=subprocess.DEVNULL
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "code.py").write_bytes(b"value = 1\n")
    (root / "deleted.txt").write_bytes(b"remove me\n")
    (root / ".gitignore").write_text(".env\ncache/\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def identity(root):
    index = Path(
        git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip()
    )
    return (
        git(root, "rev-parse", "HEAD"),
        index.read_bytes(),
        git(root, "status", "--porcelain", "-z"),
    )


def test_snapshot_is_current_disk_bytes_and_never_changes_owner_index(repo, tmp_path):
    (repo / "code.py").write_bytes(b"value = 2\n")
    git(repo, "add", "code.py")
    (repo / "code.py").write_bytes(b"value = 3\n")
    (repo / "deleted.txt").unlink()
    names = ["新 文件.bin", "tab\tfile.txt", "line\nbreak.txt"]
    for name in names:
        (repo / name).write_bytes(b"\xff\0data\r\n")
    (repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "run.sh").chmod(0o755)
    (repo / ".env").write_text("ignored-local-value\n")
    before = identity(repo)
    preview = preview_baseline(repo)
    assert preview.snapshot_available
    assert preview.untracked_file_count == 4
    receipt = select_baseline(
        repo,
        mode="WORKING_TREE_SNAPSHOT",
        expected_fingerprint=preview.fingerprint,
        request_id="snapshot-1",
    )
    assert identity(repo) == before
    assert receipt.base_sha != receipt.source_head
    assert git(repo, "show", f"{receipt.base_sha}:code.py") == b"value = 3\n"
    assert git(repo, "show", ":code.py") == b"value = 2\n"
    for name in names:
        assert git(repo, "show", f"{receipt.base_sha}:{name}") == b"\xff\0data\r\n"
    assert "deleted.txt" not in git(repo, "ls-tree", "-r", "--name-only", receipt.base_sha).decode()
    assert ".env" not in git(repo, "ls-tree", "-r", "--name-only", receipt.base_sha).decode()
    managed = WorktreeManager(tmp_path / "managed").create(
        repo_path=repo, task_id="real-task", base_sha=receipt.base_sha
    )
    assert (managed.worktree_path / "code.py").read_bytes() == b"value = 3\n"
    assert os.access(managed.worktree_path / "run.sh", os.X_OK)
    for name in names:
        assert (managed.worktree_path / name).read_bytes() == b"\xff\0data\r\n"
    assert not git(managed.worktree_path, "status", "--porcelain")
    assert identity(repo) == before
    assert (
        select_baseline(
            repo,
            mode="WORKING_TREE_SNAPSHOT",
            expected_fingerprint=preview.fingerprint,
            request_id="snapshot-1",
        )
        == receipt
    )


def test_head_mode_excludes_dirty_work_and_stale_preview_has_no_ref(repo):
    preview = preview_baseline(repo)
    (repo / "code.py").write_text("value = 7\n")
    before = identity(repo)
    with pytest.raises(BaselineError, match="baseline_changed"):
        select_baseline(
            repo,
            mode="WORKING_TREE_SNAPSHOT",
            expected_fingerprint=preview.fingerprint,
            request_id="stale",
        )
    assert not git(repo, "for-each-ref", "refs/pao/task-baselines")
    assert identity(repo) == before
    head = select_baseline(
        repo, mode="COMMITTED_HEAD", expected_fingerprint=None, request_id="head"
    )
    assert git(repo, "show", f"{head.base_sha}:code.py") == b"value = 1\n"


@pytest.mark.parametrize("unsafe", ["link", "hardlink", "secret", "conversion", "filter"])
def test_unsafe_snapshots_are_refused_but_head_remains_available(repo, unsafe):
    if unsafe == "link":
        (repo / "link").symlink_to("code.py")
    elif unsafe == "hardlink":
        os.link(repo / "code.py", repo / "alias")
    elif unsafe == "secret":
        (repo / "credentials.json").write_text("local-only")
    elif unsafe == "conversion":
        git(repo, "config", "core.autocrlf", "true")
    else:
        (repo / ".gitattributes").write_text("*.py filter=fixture\n")
    preview = preview_baseline(repo)
    assert not preview.snapshot_available
    before = identity(repo)
    with pytest.raises(BaselineError):
        select_baseline(
            repo,
            mode="WORKING_TREE_SNAPSHOT",
            expected_fingerprint=preview.fingerprint,
            request_id="unsafe",
        )
    assert (
        select_baseline(
            repo, mode="COMMITTED_HEAD", expected_fingerprint=preview.fingerprint, request_id="head"
        ).base_sha
        == preview.source_head
    )
    assert identity(repo) == before
    assert not git(repo, "for-each-ref", "refs/pao/task-baselines")


def test_submission_archives_baseline_atomically_and_replays_original_after_source_changes(
    repo, tmp_path
):
    store = SafetyKernelStore(tmp_path / "store.sqlite3")
    service = ControlPlaneService(registry=ModelRegistry(), store=store)
    try:
        project = service.register_project({"path": str(repo)})
        (repo / "code.py").write_text("value = 8\n")
        preview = service.project_baseline(project.project_id)
        payload = dict(
            task_id="task-1",
            request_id="request-1",
            project_id=project.project_id,
            intent="fix real code",
            baseline_mode="WORKING_TREE_SNAPSHOT",
            expected_baseline_fingerprint=preview.fingerprint,
        )
        first = service.submit_task(payload)
        assert first.baseline.mode == "WORKING_TREE_SNAPSHOT"
        assert first.base_sha == first.baseline.base_sha
        assert service.get_task(first.task_id).baseline == first.baseline
        assert service.list_tasks().tasks[0].baseline == first.baseline
        assert store.task_baseline(first.task_id) == first.baseline.model_dump()
        (repo / "code.py").write_text("value = 9\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "owner change")
        assert service.submit_task(payload).baseline == first.baseline
        assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        for change in (
            {"baseline_mode": "COMMITTED_HEAD"},
            {"intent": "different"},
            {"expected_baseline_fingerprint": "0" * 64},
        ):
            with pytest.raises(ControlPlaneError, match="conflicting_request_id"):
                service.submit_task(payload | change)
        with pytest.raises(ControlPlaneError, match="baseline_changed"):
            service.submit_task(payload | {"task_id": "task-2", "request_id": "request-2"})
        assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    finally:
        store.close()
