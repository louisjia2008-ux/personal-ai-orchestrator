"""Regression tests for pathname handling in the verifier's ``changed_paths``.

The previous implementation parsed newline-separated Git output. With the Git
default ``core.quotepath=true``, every non-ASCII path came back surrounded by
double quotes and octal-escaped (``"\\344\\270\\255..."``), and names that
contain literal newlines were split into bogus fragments. The scope check then
compared allowed-path prefixes against corrupted strings. These tests pin the
NUL-separated (``-z``) behavior against real temporary Git repositories.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
    changed_paths,
)

BASELINE = "src/baseline.txt"
TRICKY_STAGED = "src/已暂存 新文件.txt"
TRICKY_UNTRACKED = (
    "src/中文目录/文件 名.txt",
    "src/制表符\t名称.txt",
    "src/换行\n名称.txt",
    'src/引号"名称.txt',
    "src/反斜杠\\名称.txt",
)
GUARD_TRACKED = "outside/守卫.txt"
OUT_OF_SCOPE_UNTRACKED = "外部目录/越界 文件.txt"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path, quotepath: str) -> tuple[Path, str]:
    repo = tmp_path / f"repo-quotepath-{quotepath}"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "PAO Tests")
    _git(repo, "config", "core.quotepath", quotepath)
    _write(repo / BASELINE, "one\n")
    _write(repo / GUARD_TRACKED, "guard\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _make_tricky_changes(repo: Path) -> None:
    _write(repo / BASELINE, "two\n")  # modified tracked path
    _write(repo / TRICKY_STAGED)
    _git(repo, "add", "--", TRICKY_STAGED)  # newly tracked (staged) path
    for name in TRICKY_UNTRACKED:  # untracked paths
        _write(repo / name)


def _expected_tricky() -> tuple[str, ...]:
    return tuple(sorted({BASELINE, TRICKY_STAGED, *TRICKY_UNTRACKED}))


@pytest.mark.parametrize("quotepath", ["true", "false"])
def test_changed_paths_preserves_unicode_and_special_names(tmp_path: Path, quotepath: str) -> None:
    """Tracked and untracked paths survive byte-for-byte under both quotepath modes."""

    repo, base_sha = _init_repo(tmp_path, quotepath)
    _make_tricky_changes(repo)

    assert changed_paths(repo, base_sha=base_sha) == _expected_tricky()


@pytest.mark.parametrize("quotepath", ["true", "false"])
def test_changed_paths_fixture_actually_exercises_quoting(tmp_path: Path, quotepath: str) -> None:
    """Without ``-z`` and with ``core.quotepath=true`` Git really does quote.

    This guards the fixture: if Git stopped quoting non-ASCII paths, the
    regression scenario these tests pin would silently stop existing.
    """

    repo, base_sha = _init_repo(tmp_path, quotepath)
    _make_tricky_changes(repo)

    raw = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", base_sha, "--"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if quotepath == "true":
        assert '"' in raw
    else:
        assert '"' not in raw


def test_changed_paths_keeps_plain_ascii_deduplicated_and_sorted(tmp_path: Path) -> None:
    repo, base_sha = _init_repo(tmp_path, "true")
    _write(repo / BASELINE, "two\n")
    _write(repo / "src/zeta.txt")
    _write(repo / "src/alpha.txt")

    assert changed_paths(repo, base_sha=base_sha) == (
        "src/alpha.txt",
        "src/baseline.txt",
        "src/zeta.txt",
    )


@pytest.mark.parametrize("quotepath", ["true", "false"])
def test_verifier_accepts_tricky_paths_within_allowed_scope(tmp_path: Path, quotepath: str) -> None:
    """Unicode/space/tab/newline/quote/backslash paths under an allowed prefix pass."""

    repo, base_sha = _init_repo(tmp_path, quotepath)
    _make_tricky_changes(repo)

    profile = VerifierProfile(
        name="fixture",
        allowed_paths=("src",),
        commands=(VerifierCommand(name="noop", argv=(sys.executable, "-c", "pass")),),
    )
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is True
    assert result.changed_paths == _expected_tricky()
    assert result.unexpected_paths == ()
    assert result.failure_reason is None


@pytest.mark.parametrize("quotepath", ["true", "false"])
def test_verifier_still_rejects_out_of_scope_paths(tmp_path: Path, quotepath: str) -> None:
    """Paths outside the allowed prefix are rejected with exact, uncorrupted names."""

    repo, base_sha = _init_repo(tmp_path, quotepath)
    _write(repo / GUARD_TRACKED, "tampered\n")  # tracked change outside src/
    _write(repo / OUT_OF_SCOPE_UNTRACKED)  # untracked change outside src/

    profile = VerifierProfile(name="fixture", allowed_paths=("src",))
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is False
    assert result.failure_reason == "changed-file scope violation"
    assert result.unexpected_paths == tuple(sorted({GUARD_TRACKED, OUT_OF_SCOPE_UNTRACKED}))
    assert result.stages == ()
