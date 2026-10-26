"""Host-owned deterministic verification using trusted argv profiles."""

from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.patch_snapshot import PatchSnapshot, capture_patch


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerifierCommand(FrozenModel):
    name: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=300.0, gt=0.0)


class VerifierProfile(FrozenModel):
    name: str = Field(min_length=1)
    preparation_commands: tuple[VerifierCommand, ...] = ()
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
    evidence_id: str | None = None
    failure_reason: str | None = None
    patch_snapshot: PatchSnapshot | None = None


def _run(argv: tuple[str, ...], *, cwd: Path, timeout: float) -> VerificationStage:
    """Bound command cleanup to its created POSIX group.

    Other sessions have separate ownership; never enumerate system processes
    or follow descendants outside this group.
    """
    import os
    import signal

    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
        except BaseException as error:
            # setsid makes this PID the group ID even if its leader has exited.
            # Never discover or signal a caller group or unrelated process.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                captured_out, captured_err = process.communicate(timeout=2.0)
            except BaseException:
                # A pipe outside the owned group must not extend cleanup forever.
                # Preserve the original failure if draining or waiting also fails.
                try:
                    process.wait(timeout=2.0)
                except BaseException:
                    pass
            else:
                if isinstance(error, subprocess.TimeoutExpired):
                    error.output = captured_out
                    error.stderr = captured_err
            raise
        return VerificationStage(
            name=argv[0],
            argv=argv,
            returncode=process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace")[-20000:],
            stderr=stderr_bytes.decode("utf-8", errors="replace")[-20000:],
        )
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _git_paths(worktree: Path, *args: str) -> tuple[str, ...]:
    """Return Git-reported paths using NUL-separated (``-z``) output.

    The caller must pass ``-z`` inside ``args``. With ``-z`` Git never applies
    ``core.quotepath`` quoting and terminates every record with a literal NUL,
    so paths containing Unicode, spaces, tabs, newlines, quotes, and
    backslashes survive byte-for-byte. Output is decoded as UTF-8; undecodable
    names raise instead of being silently mis-scoped.
    """
    completed = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return tuple(record.decode("utf-8") for record in completed.stdout.split(b"\x00") if record)


def changed_paths(worktree: Path, *, base_sha: str) -> tuple[str, ...]:
    tracked = _git_paths(worktree, "diff", "--name-only", "--no-renames", "-z", base_sha, "--")
    untracked = _git_paths(worktree, "ls-files", "--others", "--exclude-standard", "-z")
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


def _evidence_id(
    *,
    profile: VerifierProfile,
    base_sha: str,
    changed: tuple[str, ...],
    unexpected: tuple[str, ...],
    stages: tuple[VerificationStage, ...],
    passed: bool,
    failure_reason: str | None,
    patch_snapshot: PatchSnapshot | None = None,
) -> str:
    payload = {
        "profile": profile.model_dump(mode="json"),
        "base_sha": base_sha,
        "changed_paths": changed,
        "unexpected_paths": unexpected,
        "stages": [stage.model_dump(mode="json") for stage in stages],
        "passed": passed,
        "failure_reason": failure_reason,
    }
    if patch_snapshot is not None:
        payload["patch_snapshot"] = patch_snapshot.model_dump(mode="json")
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"verify-{digest[:24]}"


def _result(
    *,
    profile: VerifierProfile,
    base_sha: str,
    passed: bool,
    changed: tuple[str, ...],
    unexpected: tuple[str, ...],
    stages: tuple[VerificationStage, ...],
    failure_reason: str | None = None,
    patch_snapshot: PatchSnapshot | None = None,
) -> VerificationResult:
    evidence_id = _evidence_id(
        profile=profile,
        base_sha=base_sha,
        changed=changed,
        unexpected=unexpected,
        stages=stages,
        passed=passed,
        failure_reason=failure_reason,
        patch_snapshot=patch_snapshot,
    )
    return VerificationResult(
        profile=profile.name,
        passed=passed,
        changed_paths=changed,
        unexpected_paths=unexpected,
        stages=stages,
        evidence_id=evidence_id,
        failure_reason=failure_reason,
        patch_snapshot=patch_snapshot,
    )


class DeterministicVerifier:
    """Run only host-configured argv commands; natural-language prompts cannot add commands."""

    def verify(
        self,
        worktree: Path,
        *,
        base_sha: str,
        profile: VerifierProfile,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> VerificationResult:
        paths = changed_paths(worktree, base_sha=base_sha)
        unexpected = tuple(path for path in paths if not _allowed(path, profile.allowed_paths))
        if unexpected:
            return _result(
                profile=profile,
                base_sha=base_sha,
                passed=False,
                changed=paths,
                unexpected=unexpected,
                stages=(),
                failure_reason="changed-file scope violation",
            )

        if not profile.commands:
            return _result(
                profile=profile,
                base_sha=base_sha,
                passed=False,
                changed=paths,
                unexpected=(),
                stages=(),
                failure_reason="project verifier commands are not configured",
            )

        stages: list[VerificationStage] = []
        if profile.require_diff_check:
            diff = _run(("git", "diff", "--check", base_sha, "--"), cwd=worktree, timeout=60)
            stages.append(diff.model_copy(update={"name": "git diff --check"}))
            if not diff.passed:
                return _result(
                    profile=profile,
                    base_sha=base_sha,
                    passed=False,
                    changed=paths,
                    unexpected=(),
                    stages=tuple(stages),
                    failure_reason="git diff --check failed",
                )
            for untracked_path in _git_paths(
                worktree, "ls-files", "--others", "--exclude-standard", "-z"
            ):
                untracked_check = _run(
                    (
                        "git",
                        "diff",
                        "--no-index",
                        "--check",
                        "--",
                        "/dev/null",
                        untracked_path,
                    ),
                    cwd=worktree,
                    timeout=60,
                )
                if untracked_check.returncode not in (0, 1):
                    stages.append(
                        untracked_check.model_copy(update={"name": "git diff --no-index --check"})
                    )
                    return _result(
                        profile=profile,
                        base_sha=base_sha,
                        passed=False,
                        changed=paths,
                        unexpected=(),
                        stages=tuple(stages),
                        failure_reason="git diff --check failed",
                    )

        if (task_id is None) != (run_id is None):
            raise ValueError("snapshot requires both task and run identity")
        before = capture_patch(worktree, base_sha) if task_id is not None else None
        for command in profile.commands:
            stage = _run(command.argv, cwd=worktree, timeout=command.timeout_seconds)
            stage = stage.model_copy(update={"name": command.name})
            stages.append(stage)
            if not stage.passed:
                return _result(
                    profile=profile,
                    base_sha=base_sha,
                    passed=False,
                    changed=paths,
                    unexpected=(),
                    stages=tuple(stages),
                    failure_reason=f"verifier command failed: {command.name}",
                )

        if not stages:
            return VerificationResult(
                profile=profile.name,
                passed=False,
                changed_paths=paths,
                unexpected_paths=(),
                stages=(),
                evidence_id=None,
                failure_reason="verifier profile produced no host evidence",
            )

        snapshot = None
        if before is not None:
            after = capture_patch(worktree, base_sha)
            if before != after:
                return _result(
                    profile=profile,
                    base_sha=base_sha,
                    passed=False,
                    changed=changed_paths(worktree, base_sha=base_sha),
                    unexpected=(),
                    stages=tuple(stages),
                    failure_reason="worktree changed during verification",
                )
            snapshot = PatchSnapshot.freeze(
                before,
                task_id=task_id,
                run_id=run_id,
                base_sha=base_sha,
            )
        return _result(
            profile=profile,
            base_sha=base_sha,
            passed=True,
            changed=paths,
            unexpected=(),
            stages=tuple(stages),
            patch_snapshot=snapshot,
        )


__all__ = [
    "DeterministicVerifier",
    "VerificationResult",
    "VerificationStage",
    "VerifierCommand",
    "VerifierProfile",
    "changed_paths",
]
