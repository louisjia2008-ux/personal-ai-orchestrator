"""Patch bytes stay canonical under display-only git preference settings.

Covers diff.orderFile, core.quotePath and diff.suppressBlankEmpty (individually
and combined), Chinese paths, blank/empty context, binary and non-UTF-8
content, and the verification invariant: changing only these settings in the
user's repo config must not make an accepted snapshot look stale, while a real
source change must still be rejected.
"""

import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from personal_ai_orchestrator.patch_snapshot import PatchSnapshotError, capture_patch
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from tests.test_dispatch_executor import _git, make_main_repo

# display-only preferences that previously leaked into patch bytes
PREFERENCE_SETS = [
    ("diff.orderFile", "order.txt"),
    ("core.quotePath", "false"),
    ("diff.suppressBlankEmpty", "true"),
]


def _repo_with_changes(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    """A repo with tracked/untracked Chinese-path, blank-context, binary bytes."""
    root = make_main_repo(tmp_path / "repo")
    (root / "legacy.txt").write_bytes(b"old \xff\n")
    (root / "中文 file.txt").write_bytes(b"first line\n\nsecond line\n")
    lines = [f"line {i}\n".encode("ascii") for i in range(1, 101)]
    lines[0] = b"line 1 CHANGED\n"
    (root / "wide.txt").write_bytes(b"".join(lines))
    (root / "tracked.bin").write_bytes(b"before\x00\xff")
    _git(root, "add", "--", "legacy.txt", "中文 file.txt", "wide.txt", "tracked.bin")
    _git(root, "commit", "-qm", "base state")
    base = _git(root, "rev-parse", "HEAD")
    expected = {
        # non-UTF-8 tracked change next to an empty (blank) context line
        "legacy.txt": b"new \xfe\n",
        # tracked change keeps the blank context line inside the hunk
        "中文 file.txt": b"first changed\n\nsecond line\n",
        # tracked text change far from any blank line (ordering-sensitive file)
        "wide.txt": b"".join(
            [b"line 1 CHANGED\n"] + [f"line {i}\n".encode("ascii") for i in range(2, 101)]
        ),
        # tracked binary change
        "tracked.bin": b"after\x00\xfe",
        # untracked Chinese-path non-UTF-8 and binary files
        "中文 新文件.txt": b"new \xff\n",
        "新 binary.bin": b"\x00\x01\xfe\xff",
    }
    for name, content in expected.items():
        (root / name).write_bytes(content)
    # untracked custom diff order file (display-only preference input)
    (root / "order.txt").write_bytes("wide.txt\n中文 file.txt\nlegacy.txt\n".encode())
    return root, base, expected


def _apply_cleanly(tmp_path: Path, root: Path, patch: bytes, expected: dict[str, bytes]):
    clone = tmp_path / "consumer"
    environment = dict(os.environ)
    environment.update({"HOME": str(tmp_path / "home"), "GIT_CONFIG_NOSYSTEM": "1"})
    (tmp_path / "home").mkdir(exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(root), str(clone)],
        check=True,
        env=environment,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(clone), "apply", "-"],
        input=patch,
        check=True,
        capture_output=True,
        env=environment,
    )
    for name, content in expected.items():
        assert (clone / name).read_bytes() == content, name


@pytest.mark.parametrize("key,value", PREFERENCE_SETS)
def test_single_preference_does_not_change_patch_bytes(tmp_path, key, value):
    root, base, expected = _repo_with_changes(tmp_path)
    baseline_root, baseline_base, _ = _repo_with_changes(tmp_path / "baseline")
    baseline = capture_patch(baseline_root, baseline_base)
    _git(root, "config", key, value)
    patch = capture_patch(root, base)
    assert patch == baseline
    assert sha256(patch).hexdigest() == sha256(baseline).hexdigest()
    _apply_cleanly(tmp_path, root, patch, expected)


def test_combined_preferences_do_not_change_patch_bytes(tmp_path):
    root, base, expected = _repo_with_changes(tmp_path)
    baseline_root, baseline_base, _ = _repo_with_changes(tmp_path / "baseline")
    baseline = capture_patch(baseline_root, baseline_base)
    for key, value in PREFERENCE_SETS:
        _git(root, "config", key, value)
    patch = capture_patch(root, base)
    assert patch == baseline
    _apply_cleanly(tmp_path, root, patch, expected)


def test_preference_change_alone_is_not_a_source_change(tmp_path):
    """Display-only config flips must not look like the worktree changed."""
    root, base, _ = _repo_with_changes(tmp_path)
    frozen = capture_patch(root, base)
    _git(root, "config", "core.quotePath", "false")
    assert capture_patch(root, base) == frozen


def test_real_source_change_still_changes_the_patch(tmp_path):
    root, base, _ = _repo_with_changes(tmp_path)
    frozen = capture_patch(root, base)
    (root / "legacy.txt").write_bytes(b"actually changed \xff\n")
    assert capture_patch(root, base) != frozen


def test_untracked_paths_reject_symlinks(tmp_path):
    root, base, _ = _repo_with_changes(tmp_path)
    (root / "link.txt").symlink_to("legacy.txt")
    _git(root, "config", "core.quotePath", "false")
    with pytest.raises(PatchSnapshotError):
        capture_patch(root, base)


@pytest.mark.parametrize("key,value", PREFERENCE_SETS)
def test_display_only_change_during_verification_is_accepted(tmp_path, key, value):
    root, base, _ = _repo_with_changes(tmp_path)
    before_config = (root / ".git/config").read_bytes()
    frozen = capture_patch(root, base)
    assert (root / ".git/config").read_bytes() == before_config
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        profile=VerifierProfile(
            name="display-only",
            commands=(
                VerifierCommand(
                    name="change display preference", argv=("git", "config", key, value)
                ),
            ),
        ),
        task_id="task",
        run_id="run",
    )
    assert result.passed
    assert result.patch_snapshot is not None
    assert result.patch_snapshot.sha256 == sha256(frozen).hexdigest()
    after_setting = (root / ".git/config").read_bytes()
    assert capture_patch(root, base) == frozen
    assert (root / ".git/config").read_bytes() == after_setting
