"""Host-owned deterministic verification using trusted argv profiles."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerifierCommand(FrozenModel):
    name: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=300.0, gt=0.0)


class VerifierProfile(FrozenModel):
    name: str = Field(min_length=1)
    commands: tuple[VerifierCommand, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    require_diff_check: bool = True


class VerificationStage(FrozenModel):
    name: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.returncode == 0


class VerificationResult(FrozenModel):
    profile: str
    passed: bool
    changed_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    stages: tuple[VerificationStage, ...]
    failure_reason: str | None = None


def _run(argv: tuple[str, ...], *, cwd: Path, timeout: float) -> VerificationStage:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return VerificationStage(
        name=argv[0],
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout[-20000:],
        stderr=completed.stderr[-20000:],
    )


def _git_lines(worktree: Path, *args: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return tuple(line for line in completed.stdout.splitlines() if line)


def changed_paths(worktree: Path, *, base_sha: str) -> tuple[str, ...]:
    tracked = _git_lines(worktree, "diff", "--name-only", base_sha, "--")
    untracked = _git_lines(worktree, "ls-files", "--others", "--exclude-standard")
    return tuple(sorted(set(tracked) | set(untracked)))


def _allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    if not prefixes:
        return True
    candidate = PurePosixPath(path)
    for prefix in prefixes:
        root = PurePosixPath(prefix.rstrip("/"))
        if candidate == root or root in candidate.parents:
            return True
    return False


class DeterministicVerifier:
    """Run only host-configured argv commands; natural-language prompts cannot add commands."""

    def verify(
        self,
        worktree: Path,
        *,
        base_sha: str,
        profile: VerifierProfile,
    ) -> VerificationResult:
        paths = changed_paths(worktree, base_sha=base_sha)
        unexpected = tuple(path for path in paths if not _allowed(path, profile.allowed_paths))
        if unexpected:
            return VerificationResult(
                profile=profile.name,
                passed=False,
                changed_paths=paths,
                unexpected_paths=unexpected,
                stages=(),
                failure_reason="changed-file scope violation",
            )

        stages: list[VerificationStage] = []
        if profile.require_diff_check:
            diff = _run(("git", "diff", "--check", base_sha, "--"), cwd=worktree, timeout=60)
            stages.append(diff.model_copy(update={"name": "git diff --check"}))
            if not diff.passed:
                return VerificationResult(
                    profile=profile.name,
                    passed=False,
                    changed_paths=paths,
                    unexpected_paths=(),
                    stages=tuple(stages),
                    failure_reason="git diff --check failed",
                )

        for command in profile.commands:
            stage = _run(command.argv, cwd=worktree, timeout=command.timeout_seconds)
            stage = stage.model_copy(update={"name": command.name})
            stages.append(stage)
            if not stage.passed:
                return VerificationResult(
                    profile=profile.name,
                    passed=False,
                    changed_paths=paths,
                    unexpected_paths=(),
                    stages=tuple(stages),
                    failure_reason=f"verifier command failed: {command.name}",
                )

        return VerificationResult(
            profile=profile.name,
            passed=True,
            changed_paths=paths,
            unexpected_paths=(),
            stages=tuple(stages),
        )


__all__ = [
    "DeterministicVerifier",
    "VerificationResult",
    "VerificationStage",
    "VerifierCommand",
    "VerifierProfile",
    "changed_paths",
]
