"""Regression tests for untracked-file whitespace checks in ``DeterministicVerifier.verify``.

``git diff --check <base> --`` only inspects tracked changes, so trailing
whitespace in brand-new (untracked) files previously slipped through the diff
gate. ``verify`` now enumerates untracked files with NUL-separated
``git ls-files --others`` and runs ``git diff --no-index --check -- /dev/null
<path>`` on each. A clean new file (including binary and non-UTF-8 content)
exits 1, while whitespace errors exit 3; only 0/1 are accepted and the
original failure exit code is preserved in the evidence stages. The blocking
reason stays the stable ``"git diff --check failed"`` shared with the tracked
gate, so callers cannot distinguish (or depend on) which file tripped it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerificationStage,
    VerifierCommand,
    VerifierProfile,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="git diff --no-index -- /dev/null requires POSIX"
)

FIXTURES = Path(__file__).parent / "fixtures"
NON_UTF8_FIXTURE = FIXTURES / "non_utf8_whitespace.txt"

BASELINE = "baseline.txt"

WHITESPACE_UNTRACKED = (
    "src/中文 空白.txt",
    "src/trailing space.txt",
    "src/trailing\ttab.txt",
    "src/换行\n名称.txt",
    "src/--开头 文件.txt",
)

CLEAN_UNTRACKED = (
    "src/clean 中文.txt",
    "src/clean binary.bin",
    "src/clean non-utf8.txt",
)

FAILING_COMMAND = VerifierCommand(
    name="should-not-run",
    argv=(sys.executable, "-c", "import sys; sys.exit(2)"),
)
NOOP_COMMAND = VerifierCommand(name="noop", argv=(sys.executable, "-c", "pass"))


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "PAO Tests")
    _git(repo, "config", "core.quotepath", "true")
    (repo / BASELINE).write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, name: str, content: bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_whitespace_errors(repo: Path) -> None:
    for index, name in enumerate(WHITESPACE_UNTRACKED):
        _write(repo, name, f"line {index} with trailing whitespace \n".encode())


def _make_clean_files(repo: Path) -> None:
    _write(repo, CLEAN_UNTRACKED[0], "干净的中文内容\n".encode())
    _write(repo, CLEAN_UNTRACKED[1], bytes(range(256)))
    shutil.copyfile(NON_UTF8_FIXTURE, repo / CLEAN_UNTRACKED[2])


@pytest.mark.parametrize("quotepath", ["true", "false"])
def test_untracked_whitespace_errors_block_project_commands(tmp_path: Path, quotepath: str) -> None:
    """Trailing whitespace in new files fails the diff gate before any command runs."""

    repo, base_sha = _init_repo(tmp_path)
    _git(repo, "config", "core.quotepath", quotepath)
    _make_whitespace_errors(repo)

    profile = VerifierProfile(name="fixture", commands=(FAILING_COMMAND,))
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is False
    assert result.failure_reason == "git diff --check failed"
    assert [stage.name for stage in result.stages] == [
        "git diff --check",
        "git diff --no-index --check",
    ]
    untracked_stage = result.stages[1]
    assert isinstance(untracked_stage, VerificationStage)
    assert untracked_stage.returncode not in (0, 1)
    assert untracked_stage.returncode == 3
    assert not untracked_stage.passed
    # The offending path is carried in the stage argv, not the stable reason.
    assert untracked_stage.argv[0:5] == ("git", "diff", "--no-index", "--check", "--")
    assert untracked_stage.argv[5] == "/dev/null"
    assert untracked_stage.argv[6] in WHITESPACE_UNTRACKED
    # The blocking command never ran.
    assert not any(stage.name == "should-not-run" for stage in result.stages)


def test_every_whitespace_file_is_individually_flagged(tmp_path: Path) -> None:
    """Each whitespace-carrying untracked file produces its own blocking stage."""

    repo, base_sha = _init_repo(tmp_path)
    profile = VerifierProfile(name="fixture", commands=(FAILING_COMMAND,))
    for name in WHITESPACE_UNTRACKED:
        for existing in repo.rglob("*"):
            if ".git" in existing.relative_to(repo).parts:
                continue
            if existing.is_file() and existing.name != BASELINE:
                existing.unlink()
        _write(repo, name, b"trailing space \n")

        result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

        assert result.passed is False, name
        assert result.failure_reason == "git diff --check failed"
        assert result.stages[-1].name == "git diff --no-index --check"
        assert result.stages[-1].returncode == 3


def test_clean_untracked_text_binary_and_non_utf8_pass(tmp_path: Path) -> None:
    """Clean new files (text, binary, non-UTF-8) exit 1 and are accepted."""

    repo, base_sha = _init_repo(tmp_path)
    _make_clean_files(repo)

    raw = NON_UTF8_FIXTURE.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")

    profile = VerifierProfile(name="fixture", commands=(NOOP_COMMAND,))
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is True
    assert result.failure_reason is None
    assert set(CLEAN_UNTRACKED) <= set(result.changed_paths)
    # No failed stages in a passing result.
    assert [stage.name for stage in result.stages] == ["git diff --check", "noop"]
    assert all(stage.passed for stage in result.stages)
    assert result.evidence_id is not None
    assert result.evidence_id.startswith("verify-")


def test_mixed_clean_and_whitespace_files_still_block(tmp_path: Path) -> None:
    repo, base_sha = _init_repo(tmp_path)
    _make_clean_files(repo)
    _make_whitespace_errors(repo)

    profile = VerifierProfile(name="fixture", commands=(NOOP_COMMAND,))
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is False
    assert result.failure_reason == "git diff --check failed"
    assert result.stages[-1].returncode == 3


def test_require_diff_check_false_skips_untracked_whitespace_gate(tmp_path: Path) -> None:
    """Without the diff gate, whitespace in untracked files no longer blocks."""

    repo, base_sha = _init_repo(tmp_path)
    _make_whitespace_errors(repo)

    profile = VerifierProfile(
        name="fixture",
        commands=(NOOP_COMMAND,),
        require_diff_check=False,
    )
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is True
    assert [stage.name for stage in result.stages] == ["noop"]


def test_scope_violation_takes_priority_over_whitespace(tmp_path: Path) -> None:
    """An out-of-scope untracked file fails before any whitespace checking."""

    repo, base_sha = _init_repo(tmp_path)
    _make_whitespace_errors(repo)

    profile = VerifierProfile(
        name="fixture",
        allowed_paths=("elsewhere",),
        commands=(NOOP_COMMAND,),
    )
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is False
    assert result.failure_reason == "changed-file scope violation"
    assert result.stages == ()
    assert set(WHITESPACE_UNTRACKED) <= set(result.unexpected_paths)


def test_failure_result_carries_evidence_snapshot(tmp_path: Path) -> None:
    """A blocked verification still records verifiable evidence stages."""

    repo, base_sha = _init_repo(tmp_path)
    _make_whitespace_errors(repo)

    profile = VerifierProfile(name="fixture", commands=(NOOP_COMMAND,))
    result = DeterministicVerifier().verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed is False
    assert result.evidence_id is not None
    assert result.evidence_id.startswith("verify-")
    assert result.stages[0].argv == ("git", "diff", "--check", base_sha, "--")
    assert result.stages[1].argv[0:5] == ("git", "diff", "--no-index", "--check", "--")
    assert result.stages[1].argv[5] == "/dev/null"
    assert result.stages[1].argv[6] in WHITESPACE_UNTRACKED
