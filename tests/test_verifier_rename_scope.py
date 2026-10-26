"""Regression tests for rename handling in the verifier's ``changed_paths``.

By default, ``git diff --name-only`` applies rename detection when the index
matches a rename (same blob content, new path). Under that default the command
reports only the *new* path, so a staged rename of ``protected/original.py``
to ``allowed/new.py`` would hide the deletion of the out-of-scope source and
the allowed-path scope check would wrongly pass. These tests pin the
``--no-renames`` behavior against real temporary Git repositories.
"""

import subprocess
import sys
from pathlib import Path

from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
    changed_paths,
)

BASELINE = "src/baseline.txt"
SOURCE = "protected/original.py"
TARGET = "allowed/new.py"
TRICKY_SOURCE = "保护目录/原始 文件\t名.py"
TRICKY_TARGET = "允许目录/新 文件\t名.py"
NEWLINE_SOURCE = "保护目录/原始\n换行.py"
NEWLINE_TARGET = "允许目录/新\n换行.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_out(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _head_sha(repo: Path) -> str:
    return _git_out(repo, "rev-parse", "HEAD")


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path, *, quotepath: str = "true") -> Path:
    repo = tmp_path / f"repo-{quotepath}-{len(str(tmp_path))}"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    # Isolate the fixture from any owner-level hooks or signing configuration.
    hooks = repo / ".githooks"
    hooks.mkdir()
    _git(repo, "config", "core.hooksPath", str(hooks))
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "PAO Tests")
    _git(repo, "config", "core.quotepath", quotepath)
    _write(repo / BASELINE)
    _write(repo / SOURCE)
    _git(repo, "add", "--", BASELINE, SOURCE)
    _git(repo, "commit", "-m", "baseline")
    return repo


def _stage_rename(repo: Path, source: str = SOURCE, target: str = TARGET) -> None:
    _write(repo / target)
    (repo / source).unlink()
    _git(repo, "rm", "--cached", "--", source)
    _git(repo, "add", "--", target)


def test_staged_rename_lists_source_and_target(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _stage_rename(repo)

    paths = changed_paths(repo, base_sha=_head_sha(repo))

    assert SOURCE in paths
    assert TARGET in paths


def test_staged_rename_with_core_quotepath_false(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, quotepath="false")
    _stage_rename(repo)

    paths = changed_paths(repo, base_sha=_head_sha(repo))

    assert SOURCE in paths
    assert TARGET in paths


def test_staged_rename_of_tricky_paths(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo / TRICKY_SOURCE)
    _git(repo, "add", "--", TRICKY_SOURCE)
    _git(repo, "commit", "-m", "add tricky source")
    _stage_rename(repo, source=TRICKY_SOURCE, target=TRICKY_TARGET)

    paths = changed_paths(repo, base_sha=_head_sha(repo))

    assert TRICKY_SOURCE in paths
    assert TRICKY_TARGET in paths


def test_staged_rename_of_newline_paths(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo / NEWLINE_SOURCE)
    _git(repo, "add", "--", NEWLINE_SOURCE)
    _git(repo, "commit", "-m", "add newline source")
    _stage_rename(repo, source=NEWLINE_SOURCE, target=NEWLINE_TARGET)

    paths = changed_paths(repo, base_sha=_head_sha(repo))

    assert NEWLINE_SOURCE in paths
    assert NEWLINE_TARGET in paths


def test_rename_then_edit_still_lists_source_and_target(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _stage_rename(repo)
    # Modify the target after staging so content no longer matches the source.
    _write(repo / TARGET, "edited content\n")

    paths = changed_paths(repo, base_sha=_head_sha(repo))

    assert SOURCE in paths
    assert TARGET in paths


def test_out_of_scope_rename_source_blocks_before_commands(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _stage_rename(repo)

    # Use a command that leaves evidence on disk if it is ever executed; the
    # scope violation must block before any project command runs.
    sentinel = repo / "sentinel-ran"
    profile = VerifierProfile(
        name="scoped",
        commands=(
            VerifierCommand(
                name="sentinel",
                argv=(
                    sys.executable,
                    "-c",
                    f"open({str(sentinel)!r}, 'w').write('ran')",
                ),
            ),
        ),
        allowed_paths=("allowed/",),
        require_diff_check=False,
    )

    verifier = DeterministicVerifier()
    result = verifier.verify(repo, base_sha=_head_sha(repo), profile=profile)

    assert not result.passed
    assert result.failure_reason == "changed-file scope violation"
    assert SOURCE in result.unexpected_paths
    assert result.stages == ()
    assert not sentinel.exists()


def test_in_scope_rename_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    in_repo_source = "allowed/source.py"
    in_repo_target = "allowed/target.py"
    _write(repo / in_repo_source)
    _git(repo, "add", "--", in_repo_source)
    _git(repo, "commit", "-m", "add in-scope source")
    base_sha = _head_sha(repo)
    _stage_rename(repo, source=in_repo_source, target=in_repo_target)

    profile = VerifierProfile(
        name="scoped",
        commands=(
            VerifierCommand(
                name="ok",
                argv=(sys.executable, "-c", "print('ok')"),
            ),
        ),
        allowed_paths=("allowed/",),
        require_diff_check=False,
    )

    verifier = DeterministicVerifier()
    result = verifier.verify(repo, base_sha=base_sha, profile=profile)

    assert result.passed
    assert result.unexpected_paths == ()
    assert in_repo_source in result.changed_paths
    assert in_repo_target in result.changed_paths


def test_in_scope_rename_with_patch_snapshot(tmp_path: Path) -> None:
    from personal_ai_orchestrator.patch_snapshot import PatchSnapshot

    repo = _init_repo(tmp_path)
    in_repo_source = "allowed/source.py"
    in_repo_target = "allowed/target.py"
    _write(repo / in_repo_source)
    _git(repo, "add", "--", in_repo_source)
    _git(repo, "commit", "-m", "add in-scope source")
    # Snapshot identity must be a concrete commit SHA, resolved before the
    # rename so the alias "HEAD" never leaks into the attested base.
    base_sha = _head_sha(repo)
    _stage_rename(repo, source=in_repo_source, target=in_repo_target)

    profile = VerifierProfile(
        name="scoped",
        commands=(
            VerifierCommand(
                name="ok",
                argv=(sys.executable, "-c", "print('ok')"),
            ),
        ),
        allowed_paths=("allowed/",),
        require_diff_check=False,
    )

    verifier = DeterministicVerifier()
    result = verifier.verify(
        repo, base_sha=base_sha, profile=profile, task_id="task-1", run_id="run-1"
    )

    assert result.passed
    assert isinstance(result.patch_snapshot, PatchSnapshot)
    assert result.patch_snapshot.base_sha == base_sha
