"""Patch bytes stay canonical regardless of the user's git diff context settings."""

import base64
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from personal_ai_orchestrator.patch_snapshot import PatchSnapshot, capture_patch
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from tests.test_dispatch_executor import _git, make_main_repo

CONTEXT_SETTINGS = [
    ("0", "0"),
    ("1", "0"),
    ("12", "40"),
    ("0", "40"),
    ("12", "0"),
]


def _wide_file_bytes() -> bytes:
    lines = [f"line {i}\n".encode("ascii") for i in range(1, 101)]
    lines[2] = b"line 3 CHANGED\n"
    lines[49] = b"line 50 CHANGED\n"
    return b"".join(lines)


def _repo_with_changes(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    """Build a repository whose worktree has tracked, untracked and raw-byte changes."""
    root = make_main_repo(tmp_path / "repo")
    (root / "legacy.txt").write_bytes(b"old \xff\n")
    (root / "中文 file.txt").write_bytes(b"old line 1\nold line 2\n")
    (root / "dir").mkdir()
    (root / "dir" / "tab\tname.txt").write_bytes(b"tab old\n")
    (root / "dir" / "newline\nname.txt").write_bytes(b"newline old\n")
    wide_lines = [f"line {i}\n".encode("ascii") for i in range(1, 101)]
    (root / "wide.txt").write_bytes(b"".join(wide_lines))
    (root / "tracked.bin").write_bytes(b"before\x00\xff")
    _git(
        root,
        "add",
        "--",
        "legacy.txt",
        "中文 file.txt",
        "dir/tab\tname.txt",
        "dir/newline\nname.txt",
        "wide.txt",
        "tracked.bin",
    )
    _git(root, "commit", "-qm", "legacy inputs")
    base = _git(root, "rev-parse", "HEAD")
    expected = {
        # tracked text change
        "legacy.txt": b"new \xfe\n",
        "中文 file.txt": b"new line 1\nold line 2\n",
        "dir/tab\tname.txt": b"tab new\n",
        "dir/newline\nname.txt": b"newline new\n",
        # two changed regions 46 unchanged lines apart: interHunkContext=40
        # would merge them into one hunk, while the canonical 0 keeps two.
        "wide.txt": _wide_file_bytes(),
        # tracked binary change
        "tracked.bin": b"after\x00\xfe",
        # untracked binary and non-UTF-8 files
        "中文 新文件.txt": b"new \xff\n",
        "binary.bin": b"after\x00\xfe",
    }
    for name, content in expected.items():
        (root / name).write_bytes(content)
    return root, base, expected


def _clean_consumer(tmp_path: Path, root: Path) -> Path:
    clone = tmp_path / "consumer"
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(root), str(clone)],
        check=True,
        env=environment,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "diff.context", "0"],
        check=True,
        env=environment,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "diff.interHunkContext", "0"],
        check=True,
        env=environment,
    )
    # Isolate the consumer from any host hooks or signing configuration.
    for name in ("core.hooksPath", "commit.gpgsign", "tag.gpgsign"):
        subprocess.run(
            ["git", "-C", str(clone), "config", "--unset", name],
            capture_output=True,
            env=environment,
        )
    return clone


def _assert_clean_apply(tmp_path: Path, root: Path, patch: bytes, expected: dict[str, bytes]):
    clone = _clean_consumer(tmp_path, root)
    subprocess.run(
        ["git", "-C", str(clone), "apply", "-"],
        input=patch,
        check=True,
        capture_output=True,
    )
    for name, content in expected.items():
        assert (clone / name).read_bytes() == content, name


@pytest.mark.parametrize("diff_context,inter_hunk_context", CONTEXT_SETTINGS)
def test_patch_bytes_are_identical_across_user_context_settings(
    tmp_path, diff_context, inter_hunk_context
):
    root, base, _ = _repo_with_changes(tmp_path)
    baseline_root, baseline_base, _ = _repo_with_changes(tmp_path / "baseline")
    _git(root, "config", "diff.context", diff_context)
    _git(root, "config", "diff.interHunkContext", inter_hunk_context)
    patch = capture_patch(root, base)
    baseline = capture_patch(baseline_root, baseline_base)
    assert patch == baseline
    assert sha256(patch).hexdigest() == sha256(baseline).hexdigest()


@pytest.mark.parametrize("diff_context,inter_hunk_context", CONTEXT_SETTINGS)
def test_patch_applies_on_clean_consumer_with_host_settings(
    tmp_path, diff_context, inter_hunk_context
):
    root, base, expected = _repo_with_changes(tmp_path)
    _git(root, "config", "diff.context", diff_context)
    _git(root, "config", "diff.interHunkContext", inter_hunk_context)
    patch = capture_patch(root, base)
    _assert_clean_apply(tmp_path, root, patch, expected)


def test_verifier_snapshot_bytes_match_capture_and_apply_plainly(tmp_path):
    root, base, expected = _repo_with_changes(tmp_path)
    _git(root, "config", "diff.context", "0")
    _git(root, "config", "diff.interHunkContext", "40")
    command = VerifierCommand(
        name="read-only check",
        argv=(sys.executable, "-c", "print('ok')"),
    )
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        profile=VerifierProfile(name="p", commands=(command,)),
        task_id="t",
        run_id="r",
    )
    assert result.passed
    assert result.patch_snapshot is not None
    raw = base64.b64decode(result.patch_snapshot.patch_base64, validate=True)
    assert sha256(raw).hexdigest() == result.patch_snapshot.sha256
    assert raw == capture_patch(root, base)
    _assert_clean_apply(tmp_path, root, raw, expected)
    snapshot = PatchSnapshot.freeze(raw, task_id="t", run_id="r", base_sha=base)
    assert snapshot == result.patch_snapshot
