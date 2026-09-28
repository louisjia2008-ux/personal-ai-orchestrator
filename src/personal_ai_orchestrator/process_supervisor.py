"""Exact child-process supervision for worker commands.

Only process groups created by this supervisor are signalled. There is no killall/pkill path.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import shutil
import signal
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

SPAWN_DIAGNOSTICS_VERSION = "pao-spawn-diagnostics-v1"


class SpawnStage(StrEnum):
    CONTRACT_VALIDATED = "CONTRACT_VALIDATED"
    PROCESS_CREATE_STARTED = "PROCESS_CREATE_STARTED"
    PROCESS_CREATED = "PROCESS_CREATED"
    SPAWN_ADAPTER_POST_CREATE = "SPAWN_ADAPTER_POST_CREATE"
    DURABLE_RUN_REGISTRATION_STARTED = "DURABLE_RUN_REGISTRATION_STARTED"
    DURABLE_RUN_REGISTERED = "DURABLE_RUN_REGISTERED"
    WORKER_RUNNING = "WORKER_RUNNING"
    PROTOCOL_BOOTSTRAP_STARTED = "PROTOCOL_BOOTSTRAP_STARTED"
    PROTOCOL_BOOTSTRAP_COMPLETED = "PROTOCOL_BOOTSTRAP_COMPLETED"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _argv_shape(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Describe switches and arity without retaining arguments or values."""

    shape = ["executable"]
    positional_count = 0
    options = True
    for token in argv[1:]:
        if options and token == "--":
            shape.append("option:--")
            options = False
        elif options and token.startswith("--"):
            shape.append(f"option:{token.partition('=')[0]}")
        elif options and token.startswith("-") and token != "-":
            shape.append(f"option:{token[:2]}")
        else:
            positional_count += 1
    shape.append(f"positional-count:{positional_count}")
    return tuple(shape)


@dataclass(frozen=True)
class SpawnDiagnostics:
    """Sanitized process-launch facts safe for durable audit evidence."""

    spawn_stage: SpawnStage
    executable_path_hash: str
    executable_exists: bool
    executable_executable: bool
    cwd_hash: str
    cwd_exists: bool
    argv_count: int
    argv_shape: tuple[str, ...]
    env_key_names: tuple[str, ...]
    process_group_setup_stage: str
    stdout_pipe_created: bool
    stderr_pipe_created: bool
    child_created: bool = False
    child_pid_observed: bool = False
    child_exited_before_ownership: bool = False
    safe_exit_code: int | None = None
    exception_class: str | None = None
    safe_errno: int | None = None
    durable_run_created: bool = False
    protocol_bootstrap_started: bool = False
    protocol_bootstrap_completed: bool = False
    auth_bootstrap_stage: str = "NOT_OBSERVED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "spawn_diagnostics_version": SPAWN_DIAGNOSTICS_VERSION,
            "spawn_stage": self.spawn_stage.value,
            "exception_class": self.exception_class,
            "safe_errno": self.safe_errno,
            "child_created": self.child_created,
            "child_pid_observed": self.child_pid_observed,
            "child_exited_before_ownership": self.child_exited_before_ownership,
            "safe_exit_code": self.safe_exit_code,
            "executable_path_hash": self.executable_path_hash,
            "executable_exists": self.executable_exists,
            "executable_executable": self.executable_executable,
            "cwd_hash": self.cwd_hash,
            "cwd_exists": self.cwd_exists,
            "argv_count": self.argv_count,
            "argv_shape": list(self.argv_shape),
            "env_key_names": list(self.env_key_names),
            "process_group_setup_stage": self.process_group_setup_stage,
            "stdout_pipe_created": self.stdout_pipe_created,
            "stderr_pipe_created": self.stderr_pipe_created,
            "durable_run_created": self.durable_run_created,
            "protocol_bootstrap_started": self.protocol_bootstrap_started,
            "protocol_bootstrap_completed": self.protocol_bootstrap_completed,
            "auth_bootstrap_stage": self.auth_bootstrap_stage,
        }

    def evolved(self, **changes: Any) -> SpawnDiagnostics:
        values = self.__dict__ | changes
        return SpawnDiagnostics(**values)


class ProcessSpawnError(RuntimeError):
    """Process creation failed with transcript-free structured diagnostics."""

    def __init__(self, diagnostics: SpawnDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            f"process spawn failed at {diagnostics.spawn_stage.value}: "
            f"{diagnostics.exception_class or 'unknown'}"
        )


_OBSERVED_PROCESS: contextvars.ContextVar[tuple[ProcessSupervisor, SupervisedProcess] | None]


@dataclass
class SupervisedProcess:
    process: asyncio.subprocess.Process
    argv: tuple[str, ...]
    cwd: Path
    spawn_diagnostics: SpawnDiagnostics

    @property
    def pid(self) -> int:
        if self.process.pid is None:
            raise RuntimeError("child process has no pid")
        return self.process.pid


class ProcessSupervisor:
    def __init__(self) -> None:
        self._children: dict[int, SupervisedProcess] = {}

    @staticmethod
    def inspect_contract(
        argv: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None
    ) -> SpawnDiagnostics:
        if not argv:
            raise ValueError("argv must not be empty")
        executable = argv[0]
        search_path = os.pathsep.join(os.get_exec_path(env))
        resolved = (
            shutil.which(executable, path=search_path)
            if "/" not in executable
            else str(Path(executable).expanduser().absolute())
        )
        executable_path = resolved or executable
        executable_file = Path(executable_path)
        return SpawnDiagnostics(
            spawn_stage=SpawnStage.CONTRACT_VALIDATED,
            executable_path_hash=_sha256_text(executable_path),
            executable_exists=executable_file.is_file(),
            executable_executable=os.access(executable_file, os.X_OK),
            cwd_hash=_sha256_text(str(cwd.absolute())),
            cwd_exists=cwd.is_dir(),
            argv_count=len(argv),
            argv_shape=_argv_shape(argv),
            env_key_names=tuple(sorted((env or {}).keys())),
            process_group_setup_stage="REQUESTED_NEW_SESSION",
            stdout_pipe_created=False,
            stderr_pipe_created=False,
        )

    def begin_spawn_observation(
        self,
    ) -> contextvars.Token[tuple[ProcessSupervisor, SupervisedProcess] | None]:
        """Start one task-local observation window around a spawn adapter."""

        return _OBSERVED_PROCESS.set(None)

    def observed_process(self) -> SupervisedProcess | None:
        observed = _OBSERVED_PROCESS.get()
        if observed is None or observed[0] is not self:
            return None
        return observed[1]

    @staticmethod
    def end_spawn_observation(
        token: contextvars.Token[tuple[ProcessSupervisor, SupervisedProcess] | None],
    ) -> None:
        _OBSERVED_PROCESS.reset(token)

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> SupervisedProcess:
        diagnostics = self.inspect_contract(argv, cwd=cwd, env=env).evolved(
            spawn_stage=SpawnStage.PROCESS_CREATE_STARTED
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as error:
            safe_errno = error.errno if isinstance(error, OSError) else None
            raise ProcessSpawnError(
                diagnostics.evolved(exception_class=type(error).__name__, safe_errno=safe_errno)
            ) from error
        diagnostics = diagnostics.evolved(
            spawn_stage=SpawnStage.PROCESS_CREATED,
            child_created=True,
            child_pid_observed=process.pid is not None,
            child_exited_before_ownership=process.returncode is not None,
            safe_exit_code=process.returncode,
            process_group_setup_stage="NEW_SESSION_CREATED",
            stdout_pipe_created=process.stdout is not None,
            stderr_pipe_created=process.stderr is not None,
        )
        supervised = SupervisedProcess(
            process=process,
            argv=argv,
            cwd=cwd,
            spawn_diagnostics=diagnostics,
        )
        _OBSERVED_PROCESS.set((self, supervised))
        self._children[supervised.pid] = supervised
        return supervised

    async def wait(self, supervised: SupervisedProcess) -> tuple[int, bytes, bytes]:
        stdout, stderr = await supervised.process.communicate()
        self._children.pop(supervised.pid, None)
        return supervised.process.returncode or 0, stdout, stderr

    async def cancel(self, supervised: SupervisedProcess, *, grace_seconds: float = 2.0) -> int:
        pid = supervised.pid
        if self._children.get(pid) is not supervised:
            raise RuntimeError("process is not owned by this supervisor")
        if supervised.process.returncode is not None:
            self._children.pop(pid, None)
            return supervised.process.returncode

        os.killpg(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(supervised.process.wait(), timeout=grace_seconds)
        except TimeoutError:
            os.killpg(pid, signal.SIGKILL)
            await supervised.process.wait()
        self._children.pop(pid, None)
        return supervised.process.returncode or 0

    def emergency_kill(self, supervised: SupervisedProcess) -> None:
        """Best-effort synchronous cleanup for executor teardown paths."""

        pid = supervised.pid
        if supervised.process.returncode is not None:
            self._children.pop(pid, None)
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            try:
                supervised.process.kill()
            except Exception:
                pass
        self._children.pop(pid, None)

    async def abort_unowned(self, supervised: SupervisedProcess) -> int | None:
        """Immediately kill and reap an exact child not yet durably registered."""

        if supervised.process.returncode is not None:
            self._children.pop(supervised.pid, None)
            return supervised.process.returncode
        self.emergency_kill(supervised)
        try:
            return await supervised.process.wait()
        except Exception:
            return supervised.process.returncode

    def owned_pids(self) -> tuple[int, ...]:
        return tuple(sorted(self._children))


_OBSERVED_PROCESS = contextvars.ContextVar("pao_observed_process", default=None)


__all__ = [
    "ProcessSpawnError",
    "ProcessSupervisor",
    "SPAWN_DIAGNOSTICS_VERSION",
    "SpawnDiagnostics",
    "SpawnStage",
    "SupervisedProcess",
]
