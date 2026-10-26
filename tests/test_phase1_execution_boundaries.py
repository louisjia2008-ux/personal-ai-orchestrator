from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from personal_ai_orchestrator.dispatch_executor import main_repo_fingerprint
from personal_ai_orchestrator.pi_file_tools import ConfinedFiles, operate
from personal_ai_orchestrator.product_daemon import DEFAULT_VERIFIER_PROFILE
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from personal_ai_orchestrator.worktree_manager import WorktreeManager
from tests.test_dispatch_executor import ExecutorHarness, _git, make_main_repo


def test_default_verifier_cannot_certify_invalid_or_unchanged_code(tmp_path):
    repo = make_main_repo(tmp_path / "repo")
    base = _git(repo, "rev-parse", "HEAD")
    profile = VerifierProfile.model_validate(DEFAULT_VERIFIER_PROFILE)
    for source in [None, "def invalid(:\n"]:
        if source is not None:
            (repo / "broken.py").write_text(source)
        result = DeterministicVerifier().verify(repo, base_sha=base, profile=profile)
        assert not result.passed
        assert result.failure_reason == "project verifier commands are not configured"
    configured = VerifierProfile(
        name="compile",
        commands=(
            VerifierCommand(
                name="compile",
                argv=(
                    sys.executable,
                    "-c",
                    "compile(open('broken.py').read(), 'broken.py', 'exec')",
                ),
            ),
        ),
    )
    assert not DeterministicVerifier().verify(repo, base_sha=base, profile=configured).passed
    (repo / "broken.py").write_text("value = 42\n")
    assert DeterministicVerifier().verify(repo, base_sha=base, profile=configured).passed


def test_unconfigured_verifier_blocks_before_worker_or_worktree(tmp_path, monkeypatch):
    harness = ExecutorHarness(tmp_path)
    harness.executor.config = replace(
        harness.executor.config, verifier_profile=VerifierProfile(name="empty")
    )
    started = []

    async def start(*args, **kwargs):
        started.append(True)
        raise AssertionError("worker must not run")

    monkeypatch.setattr(harness.executor._supervisor, "start", start)
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["dispatch_failure_code"] == "VERIFIER_NOT_CONFIGURED"
        assert snapshot["run_status"] is None
        assert started == []
        assert not (harness.worktree_root / "task-1").exists()
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize(
    "kind", ["tracked", "untracked", "index", "binary", "newline", "symlink", "mode"]
)
def test_fingerprint_detects_changes_with_identical_porcelain(tmp_path, kind):
    repo = make_main_repo(tmp_path / "repo")
    file = repo / ("line\nbreak" if kind == "newline" else "README.md")
    if kind in {"untracked", "binary"}:
        file = repo / "untracked"
    if kind == "symlink":
        file = repo / "link"
        file.symlink_to("outside-a")
    else:
        file.write_bytes(b"before\x00" if kind == "binary" else b"before\n")
    if kind == "index":
        _git(repo, "add", "README.md")
        file.write_text("disk stays constant\n")
    if kind == "mode":
        _git(repo, "config", "core.filemode", "false")
    status = _git(repo, "status", "--porcelain")
    before = main_repo_fingerprint(repo)
    assert main_repo_fingerprint(repo) == before
    if kind == "index":
        blob = _git(repo, "hash-object", "-w", "--stdin")
        _git(repo, "update-index", "--cacheinfo", f"100644,{blob},README.md")
    elif kind == "symlink":
        file.unlink()
        file.symlink_to("outside-b")
    elif kind == "mode":
        file.chmod(file.stat().st_mode | 0o100)
    else:
        file.write_bytes(b"after\x00" if kind == "binary" else b"after\n")
    assert _git(repo, "status", "--porcelain") == status
    assert main_repo_fingerprint(repo) != before


def test_dirty_main_can_complete_when_existing_bytes_are_untouched(tmp_path):
    harness = ExecutorHarness(tmp_path)
    (harness.main_repo / "README.md").write_text("owner's unfinished work\n")
    (harness.main_repo / "draft.txt").write_text("owner's draft\n")
    before = main_repo_fingerprint(harness.main_repo)
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert main_repo_fingerprint(harness.main_repo) == before
    finally:
        snapshot["store"].close()


def test_dirty_main_with_a_deleted_tracked_directory_can_complete(tmp_path):
    harness = ExecutorHarness(tmp_path)
    source = harness.main_repo / "src"
    source.mkdir()
    (source / "code.py").write_text("value = 1\n")
    _git(harness.main_repo, "add", "src")
    _git(harness.main_repo, "commit", "-m", "tracked source directory")
    shutil.rmtree(source)
    before = main_repo_fingerprint(harness.main_repo)
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert main_repo_fingerprint(harness.main_repo) == before
        assert not source.exists()
    finally:
        snapshot["store"].close()


def test_changed_dirty_main_blocks_final_task_even_when_project_verifier_passes(tmp_path):
    harness = ExecutorHarness(tmp_path)
    target = harness.main_repo / "README.md"
    target.write_text("owner work before\n")
    status = _git(harness.main_repo, "status", "--porcelain")
    script = harness.worker_bin.read_text()
    harness.worker_bin.write_text(
        script.replace(
            "exit 0", f"printf 'changed owner work\\n' > {shlex.quote(str(target))}\nexit 0"
        )
    )
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert target.read_text() == "changed owner work\n"
        assert _git(harness.main_repo, "status", "--porcelain") == status
        assert snapshot["task"].state is TaskState.BLOCKED
        outcomes = [
            event["payload"]
            for event in snapshot["store"].audit_events("task-1")
            if event["event_type"] == "OWNER_DISPATCH_COMPLETED"
        ]
        assert outcomes and outcomes[-1]["main_repo_unchanged"] is False
        evidence = list((harness.runtime_root / "verification-evidence").glob("*.json"))
        assert evidence and all(json.loads(path.read_text())["passed"] for path in evidence)
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize("adopt", [False, True])
def test_actual_worker_uses_host_permissions_for_new_and_adopted_worktrees(
    tmp_path, monkeypatch, adopt
):
    harness = ExecutorHarness(tmp_path)
    (harness.main_repo / "opencode.json").write_text('{"permission":{"*":"allow","bash":"allow"}}')
    _git(harness.main_repo, "add", "opencode.json")
    _git(harness.main_repo, "commit", "-m", "untrusted permissive policy")
    request = harness.reserve()
    if adopt:
        managed = WorktreeManager(harness.worktree_root).create(
            repo_path=harness.main_repo,
            task_id="task-1",
            base_sha=_git(harness.main_repo, "rev-parse", "HEAD"),
        )
        store = SafetyKernelStore(harness.state_db)
        try:
            store.register_workspace(
                task_id="task-1",
                repo_path=str(managed.repo_path),
                worktree_path=str(managed.worktree_path),
                branch=managed.branch,
                base_sha=managed.base_sha,
                project_id=harness.project.project_id,
            )
        finally:
            store.close()
    captured = []
    original = harness.executor._supervisor.start

    async def start(argv, *, cwd, env):
        captured.append((argv, dict(env)))
        return await original(argv, cwd=cwd, env=env)

    monkeypatch.setattr(harness.executor._supervisor, "start", start)
    harness.run(request)
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert len(captured) == 1
        argv, env = captured[0]
        assert "--pure" in argv
        assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
        permissions = json.loads(env["OPENCODE_PERMISSION"])
        assert permissions["edit"] == "allow"
        assert all(
            permissions[key] == "deny" for key in ["*", "bash", "webfetch", "external_directory"]
        )
        assert (
            json.loads((harness.worktree_root / "task-1/opencode.json").read_text())["permission"][
                "bash"
            ]
            == "allow"
        )
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize("name", ["opencode.json", "opencode.jsonc", ".opencode/opencode.json"])
def test_project_policy_never_receives_host_writes_or_authority(tmp_path, name):
    harness = ExecutorHarness(tmp_path)
    outside = tmp_path / "outside-canary"
    target = harness.main_repo / name
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(outside)
    _git(harness.main_repo, "add", name)
    _git(harness.main_repo, "commit", "-m", "project policy trap")
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.VERIFIED
        assert not outside.exists()
        assert (harness.worktree_root / "task-1" / name).is_symlink()
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize("kind", ["symlink", "writable", "fifo", "oversize", "malformed"])
def test_invalid_host_policy_is_rejected_before_spawn(tmp_path, kind):
    harness = ExecutorHarness(tmp_path)
    policy = tmp_path / "policy.json"
    if kind == "symlink":
        target = tmp_path / "actual.json"
        target.write_text('{"permission": {"bash": "allow"}}')
        policy.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(policy)
    else:
        policy.write_text(
            "{"
            if kind == "malformed"
            else " " * 65537
            if kind == "oversize"
            else '{"permission": {}}'
        )
        if kind == "writable":
            policy.chmod(0o666)
    harness.executor.config = replace(harness.executor.config, worker_permission_config=policy)
    harness.run(harness.reserve())
    snapshot = harness.snapshot()
    try:
        assert snapshot["task"].state is TaskState.BLOCKED
        assert snapshot["run_status"] is None
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize("link", ["dangling", "external", "parent", "hardlink", "fifo", "metadata"])
def test_file_operations_cannot_read_or_write_through_links(tmp_path, link):
    root = tmp_path / "worktree"
    root.mkdir()
    outside = tmp_path / "canary"
    outside.write_text("outside secret\n")
    path = root / "trap"
    if link == "dangling":
        path.symlink_to(tmp_path / "missing")
    elif link == "external":
        path.symlink_to(outside)
    elif link == "parent":
        path.symlink_to(tmp_path, target_is_directory=True)
        path /= "canary"
    elif link == "hardlink":
        os.link(outside, path)
    elif link == "fifo":
        os.mkfifo(path)
    else:
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("metadata\n")
        path.symlink_to(root / ".git", target_is_directory=True)
        path /= "config"
    files = ConfinedFiles(str(root))
    try:
        for operation in ["read", "write", "stat"]:
            with pytest.raises((ValueError, OSError)):
                operate(files, {"operation": operation, "path": str(path), "content": "attack"})
    finally:
        files.close()
    assert outside.read_text() == "outside secret\n"
    assert not (tmp_path / "missing").exists()


def test_file_search_and_normal_edits_share_the_confined_boundary(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret-canary")
    (root / "external").symlink_to(tmp_path, target_is_directory=True)
    (root / ".gitignore").write_text("ignored/\n*.log\n!keep.log\n")
    files = ConfinedFiles(str(root))
    try:
        for path, text in [
            ("src/code.py", "one\nneedle\nthree\n"),
            ("ignored/leak.py", "secret-canary"),
            ("keep.log", "visible"),
        ]:
            operate(files, {"operation": "write", "path": path, "content": text})
        assert files.read("src/code.py").startswith(b"one")
        found = operate(files, {"operation": "find", "pattern": "*.py"})
        assert found == ["src/code.py"]
        assert operate(files, {"operation": "find", "pattern": "**/*.py"}) == ["src/code.py"]
        assert operate(files, {"operation": "find", "pattern": "src/**/*.py"}) == ["src/code.py"]
        files.write("top.py", b"top = 1\n")
        assert operate(files, {"operation": "find", "pattern": "**/*.py"}) == [
            "src/code.py",
            "top.py",
        ]
        result = operate(files, {"operation": "grep", "pattern": "needle", "context": 1})
        assert "src/code.py:2:needle" in result
        assert "secret-canary" not in operate(files, {"operation": "grep", "pattern": "."})
        assert "external" in operate(files, {"operation": "ls"})
        with pytest.raises((OSError, ValueError)):
            operate(files, {"operation": "write", "path": ".GIT/config", "content": "attack"})
    finally:
        files.close()


def test_packaged_entry_routes_file_helper_before_daemon_start(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = root.stat()
    entry = Path(__file__).parents[1] / "scripts/pao_daemon_entry.py"
    result = subprocess.run(
        [sys.executable, str(entry), "--pao-file-tool", str(root), f"{info.st_dev}:{info.st_ino}"],
        input=json.dumps({"operation": "write", "path": "created.txt", "content": "legitimate"}),
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    assert json.loads(result.stdout)["ok"] is True
    assert (root / "created.txt").read_text() == "legitimate"


def test_real_pi_sdk_executes_only_confined_file_tools(tmp_path):
    from personal_ai_orchestrator.pi_runtime import seed_pi_worktree_guard

    sdk = os.environ.get("PAO_PI_SDK_ROOT")
    if not sdk:
        pytest.skip("PAO_PI_SDK_ROOT is required for real offline SDK acceptance")
    node = shutil.which("node")
    assert node
    root = tmp_path / "worktree"
    root.mkdir()
    guard = seed_pi_worktree_guard(tmp_path / "trusted")
    script = Path(__file__).parent / "fixtures/pi_confined_tools.mjs"
    result = subprocess.run(
        [node, str(script), sdk, str(root), str(guard)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["model_calls"] == 0
    assert report["boundary_cases"] == 8
