"""Real Git regressions for algorithm-independent patch snapshots."""

import base64
import subprocess
import sys
from hashlib import sha256

import pytest

from personal_ai_orchestrator.patch_snapshot import capture_patch
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from tests.test_dispatch_executor import _git, make_main_repo

ALGORITHMS = ("myers", "minimal", "patience", "histogram")


def _changed_repo(tmp_path):
    root = make_main_repo(tmp_path / "repo")
    (root / "repeat.txt").write_bytes(b"A\nB\nC\nA\nB\nB\nA\n")
    (root / "legacy.txt").write_bytes(b"old \xff\n")
    (root / "binary.bin").write_bytes(b"old\x00\xff")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "algorithm regression baseline")
    base = _git(root, "rev-parse", "HEAD")
    expected = {
        "repeat.txt": b"C\nB\nA\nB\nA\nC\n",
        "legacy.txt": b"new \xfe\n",
        "binary.bin": b"new\x00\xfe",
        "中文 新文件.txt": b"new \xff\n",
        "new.bin": b"new\x00\xfe",
    }
    for name, content in expected.items():
        (root / name).write_bytes(content)
    return root, base, expected


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_algorithm_preferences_do_not_change_patch_bytes(tmp_path, algorithm):
    root, base, _ = _changed_repo(tmp_path)
    default = capture_patch(root, base)
    _git(root, "config", "diff.algorithm", algorithm)
    assert capture_patch(root, base) == default


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_untracked_patch_is_stable_under_algorithm_preferences(tmp_path, algorithm):
    root, base, _ = _changed_repo(tmp_path)
    _git(root, "checkout", base, "--", "repeat.txt", "legacy.txt", "binary.bin")
    default = capture_patch(root, base)
    _git(root, "config", "diff.algorithm", algorithm)
    assert capture_patch(root, base) == default


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_configuration_only_change_during_verification_is_accepted(tmp_path, algorithm):
    root, base, _ = _changed_repo(tmp_path)
    before = capture_patch(root, base)
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        task_id="algorithm-task",
        run_id="algorithm-run",
        profile=VerifierProfile(
            name="display-only",
            commands=(
                VerifierCommand(
                    name="set algorithm",
                    argv=("git", "config", "diff.algorithm", algorithm),
                ),
            ),
        ),
    )
    assert result.passed
    assert result.patch_snapshot is not None
    assert result.patch_snapshot.sha256 == sha256(before).hexdigest()
    assert base64.b64decode(result.patch_snapshot.patch_base64, validate=True) == before


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_plain_apply_to_clean_consumer_preserves_exact_bytes(tmp_path, algorithm):
    root, base, expected = _changed_repo(tmp_path)
    _git(root, "config", "diff.algorithm", algorithm)
    patch = capture_patch(root, base)
    consumer = tmp_path / "consumer"
    _git(root, "clone", "-q", "--no-local", str(root), str(consumer))
    _git(consumer, "checkout", "-q", base)
    for args in (("apply", "--check", "-"), ("apply", "-")):
        subprocess.run(
            ["git", "-C", str(consumer), *args],
            input=patch,
            check=True,
            capture_output=True,
            timeout=30,
        )
    assert {name: (consumer / name).read_bytes() for name in expected} == expected


def test_actual_source_change_during_verification_is_rejected(tmp_path):
    root, base, _ = _changed_repo(tmp_path)
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        task_id="source-task",
        run_id="source-run",
        profile=VerifierProfile(
            name="actual-edit",
            commands=(
                VerifierCommand(
                    name="edit source",
                    argv=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        "Path('repeat.txt').write_text('changed source\\n')",
                    ),
                ),
            ),
        ),
    )
    assert not result.passed
    assert result.failure_reason == "worktree changed during verification"
    assert result.patch_snapshot is None


@pytest.mark.parametrize("algorithm", [None, "histogram"])
def test_capture_does_not_rewrite_git_configuration(tmp_path, algorithm):
    root, base, _ = _changed_repo(tmp_path)
    if algorithm is not None:
        _git(root, "config", "diff.algorithm", algorithm)
    before = (root / ".git/config").read_bytes()
    capture_patch(root, base)
    assert (root / ".git/config").read_bytes() == before
