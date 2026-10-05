"""Exact child-process supervision for worker commands.

Only process groups created by this supervisor are signalled. There is no killall/pkill path.
The host must retain the default SIGCHLD disposition and give this supervisor
exclusive reaping ownership of its exact children. Ignored/custom SIGCHLD
policies are rejected, never reset. External waitpid reapers and native
SA_NOCLDWAIT configuration are unsupported: Python's portable signal API cannot
inspect that native flag or synchronize external changes to signal disposition.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
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


def _default_sigchld_policy() -> bool:
    try:
        return signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    except (AttributeError, OSError, ValueError):
        return False


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


class CleanupStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PipeCleanup:
    eof: bool
    error: str | None
    forced_closed: bool
    collector_done: bool

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class CleanupResult:
    """Evidence for the created process group, not descendants that escaped it.

    CONFIRMED requires a reaped exact child, an observed absent process group,
    and successful EOF from both collectors. Sending a signal proves none of
    these facts by itself. Monotonic timestamps are process-local evidence.
    """

    status: CleanupStatus
    scope: str
    capability: str
    child_exited: bool
    child_reaped: bool
    exit_code: int | None
    stdout: PipeCleanup
    stderr: PipeCleanup
    group_empty: bool
    signals: tuple[str, ...]
    reasons: tuple[str, ...]
    deadline_monotonic: float

    @property
    def confirmed(self) -> bool:
        return self.status is CleanupStatus.CONFIRMED

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "status": self.status.value,
            "stdout": self.stdout.as_dict(),
            "stderr": self.stderr.as_dict(),
            "signals": list(self.signals),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class CollectedProcess:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool
    cleanup: CleanupResult
    timed_out: bool = False


class OutputCollectionError(RuntimeError):
    """The child exited but the invocation did not finish its output contract."""

    def __init__(self, receipt: CleanupResult) -> None:
        self.receipt = receipt
        self.cleanup_result = receipt
        super().__init__("worker output did not reach EOF within its exit grace")


class CleanupIncomplete(RuntimeError):
    def __init__(self, receipt: CleanupResult) -> None:
        self.receipt = receipt
        self.cleanup_result = receipt
        super().__init__("process cleanup UNKNOWN: " + ", ".join(receipt.reasons))


@dataclass
class SpawnTicket:
    """Mutable shared context: changes in adapter Tasks reach their caller."""

    supervisor: ProcessSupervisor
    cleanup_budget_seconds: float = 10.0
    grace_seconds: float = 5.0
    creation_guard: Callable[[], AbstractContextManager[Callable[[int], None]]] | None = None
    supervised: SupervisedProcess | None = None
    raw_process: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    creation_task: asyncio.Task[SupervisedProcess] | None = None
    creation_started: bool = False
    creation_finished: bool = False
    error: str | None = None
    abort_requested: bool = False
    deadline_monotonic: float | None = None
    token: contextvars.Token[SpawnTicket | None] | None = None


@dataclass
class _PipeState:
    reader: asyncio.StreamReader
    chunks: bytearray = field(default_factory=bytearray)
    cap: int = 1024 * 1024
    truncated: bool = False
    eof: bool = False
    error: str | None = None
    forced_closed: bool = False
    transport: asyncio.ReadTransport | None = None
    task: asyncio.Task[None] | None = None

    def receipt(self) -> PipeCleanup:
        return PipeCleanup(
            eof=self.eof,
            error=self.error,
            forced_closed=self.forced_closed,
            collector_done=self.task is not None and self.task.done(),
        )


class _OwnedProcess:
    """Small asyncio-compatible facade; the underlying Popen is never polled.

    wait() observes exit without consuming the Linux zombie pin. communicate()
    delegates to the supervisor, which alone owns stream collection and reap.
    """

    def __init__(
        self, supervisor: ProcessSupervisor, child: subprocess.Popen[bytes] | None = None
    ) -> None:
        self._supervisor = supervisor
        self._child = child
        self._supervised: SupervisedProcess | None = None
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()

    @property
    def pid(self) -> int:
        if self._child is None:
            raise RuntimeError("child creation has not completed")
        return self._child.pid

    async def wait(self) -> int:
        assert self._supervised is not None
        while self.returncode is None:
            self._supervisor._observe(self._supervised)
            if self._supervised._identity_error:
                raise RuntimeError(self._supervised._identity_error)
            if self.returncode is None:
                await asyncio.sleep(0.01)
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        assert self._supervised is not None
        result = await self._supervisor.collect(self._supervised)
        return result.stdout, result.stderr


@dataclass
class SupervisedProcess:
    process: _OwnedProcess
    argv: tuple[str, ...]
    cwd: Path
    spawn_diagnostics: SpawnDiagnostics
    cleanup_result: CleanupResult | None = None
    collected_result: CollectedProcess | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pinned: bool = True
    _reaped: bool = False
    _identity_error: str | None = None
    _operation: asyncio.Task[CollectedProcess] | None = field(default=None, repr=False)
    _creation_task: asyncio.Task[SupervisedProcess] | None = field(default=None, repr=False)
    _cleanup_requested: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _cleanup_deadline: float | None = None
    _cleanup_work_deadline: float | None = None
    _worker_deadline: float | None = None
    _cleanup_budget: float = 10.0
    _grace_seconds: float = 5.0
    _eof_grace_seconds: float = 2.0
    _timed_out: bool = False
    _reasons: list[str] = field(default_factory=list, repr=False)
    _signals: list[str] = field(default_factory=list, repr=False)
    _pipes: tuple[_PipeState, _PipeState] = field(init=False, repr=False)
    _capability: str = field(init=False)

    def __post_init__(self) -> None:
        self._pipes = (_PipeState(self.process.stdout), _PipeState(self.process.stderr))
        self._capability = (
            "WAITID_WNOWAIT"
            if all(hasattr(os, key) for key in ("waitid", "WNOWAIT", "P_PID"))
            else "WAITPID_REAP_ON_OBSERVE"
        )
        self.process._supervised = self

    @property
    def pid(self) -> int:
        return self.process.pid


class ProcessSupervisor:
    def __init__(self) -> None:
        self._children: dict[int, SupervisedProcess] = {}
        # Keep Popen alive even if local indexing or adapter registration fails.
        # Popen.__del__ may reap; losing this reference invalidates a group pin.
        self._retained: list[SupervisedProcess] = []
        self._spawn_tasks: set[asyncio.Task[SupervisedProcess]] = set()

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
        self, *, cleanup_budget_seconds: float = 10.0, grace_seconds: float = 5.0
    ) -> SpawnTicket:
        ticket = SpawnTicket(
            self,
            cleanup_budget_seconds=max(0.0, cleanup_budget_seconds),
            grace_seconds=max(0.0, min(5.0, grace_seconds)),
        )
        ticket.token = _OBSERVED_PROCESS.set(ticket)
        return ticket

    def observed_process(self, ticket: SpawnTicket | None = None) -> SupervisedProcess | None:
        observed = ticket or _OBSERVED_PROCESS.get()
        return observed.supervised if observed is not None and observed.supervisor is self else None

    @staticmethod
    def end_spawn_observation(ticket: SpawnTicket) -> None:
        if ticket.token is not None:
            _OBSERVED_PROCESS.reset(ticket.token)
            ticket.token = None

    async def await_spawn(
        self, ticket: SpawnTicket, *, deadline_monotonic: float
    ) -> SupervisedProcess | None:
        while ticket.supervised is None and not ticket.creation_finished:
            if asyncio.get_running_loop().time() >= deadline_monotonic:
                break
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                # Cancellation cannot abandon an in-flight process creation.
                continue
        return ticket.supervised

    async def abort_spawn(
        self,
        ticket: SpawnTicket,
        *,
        grace_seconds: float = 5.0,
        cleanup_budget_seconds: float = 10.0,
    ) -> CleanupResult | None:
        ticket.abort_requested = True
        if ticket.creation_task is None:
            ticket.creation_finished = True
        if ticket.deadline_monotonic is None:
            ticket.deadline_monotonic = asyncio.get_running_loop().time() + max(
                0.0, cleanup_budget_seconds
            )
        supervised = await self.await_spawn(ticket, deadline_monotonic=ticket.deadline_monotonic)
        if supervised is None:
            if ticket.creation_finished and ticket.raw_process is None:
                return None
            pipe = PipeCleanup(False, None, False, False)
            return CleanupResult(
                status=CleanupStatus.UNKNOWN,
                scope="CREATED_PROCESS_GROUP",
                capability="CREATION_PENDING",
                child_exited=False,
                child_reaped=False,
                exit_code=None,
                stdout=pipe,
                stderr=pipe,
                group_empty=False,
                signals=(),
                reasons=("SPAWN_OUTCOME_PENDING",),
                deadline_monotonic=ticket.deadline_monotonic,
            )
        self._request_cleanup(
            supervised,
            reason="SPAWN_ABORTED",
            grace_seconds=grace_seconds,
            deadline=ticket.deadline_monotonic,
        )
        return await self.cleanup(supervised)

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> SupervisedProcess:
        ticket = _OBSERVED_PROCESS.get()
        if ticket is None or ticket.supervisor is not self:
            ticket = SpawnTicket(self)
        if ticket.creation_task is not None:
            raise RuntimeError("spawn ticket already has a process creation")
        if ticket.abort_requested:
            ticket.creation_finished = True
            ticket.error = "SpawnFenced"
            raise RuntimeError("spawn ticket was fenced before process creation")
        task = asyncio.create_task(self._create(argv, cwd=cwd, env=env, ticket=ticket))
        ticket.creation_task = task
        self._spawn_tasks.add(task)

        def created(completed: asyncio.Task[SupervisedProcess]) -> None:
            self._spawn_tasks.discard(completed)
            # Retrieve exceptions even if the adapter was cancelled before return.
            if not completed.cancelled():
                completed.exception()
            if ticket.abort_requested and ticket.supervised is not None:
                self._request_cleanup(
                    ticket.supervised,
                    reason="SPAWN_ABORTED",
                    deadline=ticket.deadline_monotonic,
                    grace_seconds=ticket.grace_seconds,
                )
                self._ensure_operation(ticket.supervised)

        task.add_done_callback(created)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            ticket.abort_requested = True
            if ticket.deadline_monotonic is None:
                ticket.deadline_monotonic = (
                    asyncio.get_running_loop().time() + ticket.cleanup_budget_seconds
                )
            if ticket.supervised is not None:
                self._request_cleanup(
                    ticket.supervised,
                    reason="SPAWN_ABORTED",
                    deadline=ticket.deadline_monotonic,
                    grace_seconds=ticket.grace_seconds,
                )
                self._ensure_operation(ticket.supervised)
            # During asyncio.run shutdown a task created here is not in the
            # original cancellation snapshot. Keep this original caller alive
            # until the same bounded cleanup finishes, rather than orphan it.
            await self.abort_spawn(
                ticket,
                grace_seconds=ticket.grace_seconds,
                cleanup_budget_seconds=ticket.cleanup_budget_seconds,
            )
            raise

    async def _create(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        ticket: SpawnTicket,
    ) -> SupervisedProcess:
        diagnostics = self.inspect_contract(argv, cwd=cwd, env=env).evolved(
            spawn_stage=SpawnStage.PROCESS_CREATE_STARTED
        )
        try:
            if ticket.abort_requested:
                raise RuntimeError("spawn ticket was fenced before process creation")
            ticket.creation_started = True
            # Popen executes synchronously until exact child capture; no Task
            # cancellation window exists between creation and ticket publication.
            # An OS-level spawn hang still requires an external process watchdog.
            guard = (
                ticket.creation_guard() if ticket.creation_guard else nullcontext(lambda pid: None)
            )
            # Allocate all fallible asyncio/wrapper state BEFORE creating the
            # OS child. A constructor failure is then genuinely a no-child error.
            process = _OwnedProcess(self)
            supervised = SupervisedProcess(
                process=process,
                argv=argv,
                cwd=cwd,
                spawn_diagnostics=diagnostics.evolved(
                    spawn_stage=SpawnStage.PROCESS_CREATED,
                    child_created=True,
                    child_pid_observed=True,
                    process_group_setup_stage="NEW_SESSION_CREATED",
                    stdout_pipe_created=True,
                    stderr_pipe_created=True,
                ),
            )
            supervised._creation_task = asyncio.current_task()
            with guard as report_created:
                if not _default_sigchld_policy():
                    diagnostics = diagnostics.evolved(
                        process_group_setup_stage="UNSUPPORTED_SIGCHLD_POLICY"
                    )
                    raise RuntimeError("process supervision requires default SIGCHLD disposition")
                child = subprocess.Popen(
                    argv,
                    cwd=str(cwd),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                # First operation after Popen is raw strong capture. Publishing
                # the already-allocated facade needs no constructors or awaits.
                ticket.raw_process = child
                process._child = child
                ticket.supervised = supervised
                self._retained.append(supervised)
                report_created(supervised.pid)
            loop = asyncio.get_running_loop()
            for state, pipe in zip(supervised._pipes, (child.stdout, child.stderr), strict=True):
                assert pipe is not None
                if state.forced_closed or supervised.cleanup_result is not None:
                    state.error = "PIPE_SETUP_AFTER_CLEANUP"
                    pipe.close()
                    continue
                transport, _ = await loop.connect_read_pipe(
                    lambda state=state: asyncio.StreamReaderProtocol(state.reader), pipe
                )
                state.transport = transport
                if state.forced_closed or supervised.cleanup_result is not None:
                    state.error = "PIPE_SETUP_AFTER_CLEANUP"
                    transport.close()
            if supervised.cleanup_result is None:
                self._children[supervised.pid] = supervised
            return supervised
        except BaseException as error:
            ticket.error = type(error).__name__
            if (
                ticket.supervised is None
                and ticket.raw_process is None
                and isinstance(error, Exception)
            ):
                raise ProcessSpawnError(
                    diagnostics.evolved(
                        exception_class=type(error).__name__,
                        safe_errno=error.errno if isinstance(error, OSError) else None,
                    )
                ) from error
            if ticket.supervised is not None:
                for state, pipe in zip(
                    ticket.supervised._pipes,
                    (
                        ticket.supervised.process._child.stdout,
                        ticket.supervised.process._child.stderr,
                    ),
                    strict=True,
                ):
                    if state.transport is None:
                        state.error = state.error or "PIPE_SETUP_FAILED"
                        if pipe is not None:
                            pipe.close()
            raise
        finally:
            ticket.creation_finished = True

    @staticmethod
    def _check_reaper_policy_locked(supervised: SupervisedProcess) -> bool:
        if _default_sigchld_policy():
            return True
        supervised._identity_error = "UNSUPPORTED_SIGCHLD_POLICY"
        supervised._pinned = False
        return False

    def _observe_locked(self, supervised: SupervisedProcess) -> None:
        if supervised._reaped or supervised._identity_error:
            return
        if not self._check_reaper_policy_locked(supervised):
            return
        try:
            if supervised._capability == "WAITID_WNOWAIT":
                result = os.waitid(os.P_PID, supervised.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if result is not None:
                    supervised.process.returncode = (
                        result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
                    )
            else:
                pid, status = os.waitpid(supervised.pid, os.WNOHANG)
                if pid:
                    self._mark_reaped_locked(supervised, status)
        except (OSError, ValueError, NotImplementedError) as error:
            supervised._identity_error = f"IDENTITY_{type(error).__name__}"
            supervised._pinned = False

    def _observe(self, supervised: SupervisedProcess) -> None:
        with supervised._lock:
            self._observe_locked(supervised)

    @staticmethod
    def _mark_reaped_locked(supervised: SupervisedProcess, status: int) -> None:
        supervised._reaped = True
        supervised._pinned = False
        code = os.waitstatus_to_exitcode(status)
        supervised.process.returncode = code
        # This assignment prevents Popen.__del__ from later attempting a reap.
        supervised.process._child.returncode = code

    def _signal(self, supervised: SupervisedProcess, requested: signal.Signals) -> bool:
        with supervised._lock:
            self._observe_locked(supervised)
            if not supervised._pinned or supervised._identity_error:
                return False
            if not self._check_reaper_policy_locked(supervised):
                return False
            try:
                os.killpg(supervised.pid, requested)
            except ProcessLookupError:
                return False
            except OSError as error:
                supervised._reasons.append(f"SIGNAL_{type(error).__name__}")
                return False
            supervised._signals.append(requested.name)
            return True

    def _final_reap(self, supervised: SupervisedProcess) -> None:
        with supervised._lock:
            if supervised._reaped or supervised._identity_error:
                return
            if not self._check_reaper_policy_locked(supervised):
                return
            try:
                pid, status = os.waitpid(supervised.pid, os.WNOHANG)
                if pid:
                    self._mark_reaped_locked(supervised, status)
            except (OSError, ValueError) as error:
                supervised._identity_error = f"REAP_{type(error).__name__}"
                supervised._pinned = False

    @staticmethod
    def _group_absent(supervised: SupervisedProcess) -> bool:
        # Observation only. No nonzero signal is allowed after final reap.
        try:
            os.killpg(supervised.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    @staticmethod
    async def _drain(state: _PipeState) -> None:
        try:
            while True:
                chunk = await state.reader.read(8192)
                if not chunk:
                    if not state.forced_closed:
                        state.eof = True
                    return
                remaining = max(0, state.cap - len(state.chunks))
                state.chunks.extend(chunk[:remaining])
                state.truncated |= len(chunk) > remaining
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state.error = type(error).__name__

    def _request_cleanup(
        self,
        supervised: SupervisedProcess,
        *,
        reason: str,
        grace_seconds: float | None = None,
        cleanup_budget_seconds: float | None = None,
        deadline: float | None = None,
    ) -> None:
        if reason not in supervised._reasons:
            supervised._reasons.append(reason)
        if supervised._cleanup_deadline is None:
            budget = (
                supervised._cleanup_budget
                if cleanup_budget_seconds is None
                else max(0.0, cleanup_budget_seconds)
            )
            supervised._cleanup_deadline = (
                asyncio.get_running_loop().time() + budget if deadline is None else deadline
            )
            # Reserve the final 10% (at most one second) of this SAME budget
            # for receipt persistence. Callers must use the receipt's remaining
            # monotonic deadline, not start another cleanup/database budget.
            remaining = max(0.0, supervised._cleanup_deadline - asyncio.get_running_loop().time())
            supervised._cleanup_work_deadline = supervised._cleanup_deadline - min(
                1.0, remaining * 0.1
            )
            if grace_seconds is not None:
                supervised._grace_seconds = max(0.0, min(5.0, grace_seconds))
        supervised._cleanup_requested.set()

    def _ensure_operation(self, supervised: SupervisedProcess) -> asyncio.Task[CollectedProcess]:
        if supervised.process._supervisor is not self:
            raise RuntimeError("process is not owned by this supervisor")
        if supervised._operation is None:
            # Python 3.12 is the supported minimum. Enter the cancellation
            # finalizer before publishing the task: cancelling a never-started
            # ordinary Task would otherwise bypass every coroutine finally.
            supervised._operation = asyncio.Task(
                self._run_operation(supervised),
                loop=asyncio.get_running_loop(),
                eager_start=True,
            )
        return supervised._operation

    async def _join(
        self, supervised: SupervisedProcess, *, propagate_cancel: bool
    ) -> CollectedProcess:
        task = self._ensure_operation(supervised)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
                self._request_cleanup(supervised, reason="CANCELLED")
        if cancelled and propagate_cancel:
            raise asyncio.CancelledError
        return result

    async def collect(
        self,
        supervised: SupervisedProcess,
        *,
        timeout_seconds: float | None = None,
        stdout_cap: int = 1024 * 1024,
        stderr_cap: int = 1024 * 1024,
        grace_seconds: float = 5.0,
        cleanup_budget_seconds: float = 10.0,
        eof_grace_seconds: float = 2.0,
    ) -> CollectedProcess:
        if supervised._operation is None:
            loop = asyncio.get_running_loop()
            supervised._worker_deadline = (
                None if timeout_seconds is None else loop.time() + max(0.0, timeout_seconds)
            )
            supervised._cleanup_budget = max(0.0, cleanup_budget_seconds)
            supervised._grace_seconds = max(0.0, min(5.0, grace_seconds))
            supervised._eof_grace_seconds = max(0.0, min(2.0, eof_grace_seconds))
            supervised._pipes[0].cap = max(0, stdout_cap)
            supervised._pipes[1].cap = max(0, stderr_cap)
        result = await self._join(supervised, propagate_cancel=True)
        if not result.cleanup.confirmed:
            raise CleanupIncomplete(result.cleanup)
        if result.timed_out:
            raise TimeoutError("worker deadline exceeded")
        if "EOF_GRACE_EXPIRED" in result.cleanup.reasons:
            raise OutputCollectionError(result.cleanup)
        return result

    async def cleanup(
        self,
        supervised: SupervisedProcess,
        *,
        grace_seconds: float = 5.0,
        cleanup_budget_seconds: float = 10.0,
    ) -> CleanupResult:
        if supervised.cleanup_result is not None:
            return supervised.cleanup_result
        self._request_cleanup(
            supervised,
            reason="CLEANUP_REQUESTED",
            grace_seconds=grace_seconds,
            cleanup_budget_seconds=cleanup_budget_seconds,
        )
        return (await self._join(supervised, propagate_cancel=False)).cleanup

    async def _run_operation(self, supervised: SupervisedProcess) -> CollectedProcess:
        while True:
            try:
                for state in supervised._pipes:
                    if state.task is None:
                        state.task = asyncio.create_task(self._drain(state))
                return await self._collect_until_terminal(supervised)
            except asyncio.CancelledError:
                # asyncio.run shutdown can cancel this task directly, bypassing
                # every caller's shield. Keep the same task and fixed deadline
                # alive through exact-child termination and a truthful receipt.
                # Cancelled collectors are not silently restarted or called EOF.
                self._request_cleanup(supervised, reason="SUPERVISOR_CANCELLED")

    async def _collect_until_terminal(self, supervised: SupervisedProcess) -> CollectedProcess:
        loop = asyncio.get_running_loop()
        eof_deadline: float | None = None
        while not supervised._cleanup_requested.is_set():
            self._observe(supervised)
            if supervised._identity_error:
                self._request_cleanup(supervised, reason="IDENTITY_UNCERTAIN")
                break
            if any(state.error for state in supervised._pipes):
                self._request_cleanup(supervised, reason="READER_ERROR")
                break
            if (
                supervised._worker_deadline is not None
                and loop.time() >= supervised._worker_deadline
            ):
                supervised._timed_out = True
                self._request_cleanup(supervised, reason="WORKER_TIMEOUT")
                break
            if supervised.process.returncode is not None:
                if all(state.eof for state in supervised._pipes):
                    break
                if eof_deadline is None:
                    eof_deadline = loop.time() + supervised._eof_grace_seconds
                    if supervised._worker_deadline is not None:
                        eof_deadline = min(eof_deadline, supervised._worker_deadline)
                if loop.time() >= eof_deadline:
                    self._request_cleanup(supervised, reason="EOF_GRACE_EXPIRED")
                    break
            await asyncio.sleep(0.01)

        if supervised._cleanup_requested.is_set():
            await self._terminate(supervised)
        else:
            # No further signal decisions after this exact child is reaped.
            self._final_reap(supervised)
        return await self._finish(supervised)

    async def _terminate(self, supervised: SupervisedProcess) -> None:
        loop = asyncio.get_running_loop()
        assert supervised._cleanup_deadline is not None
        deadline = supervised._cleanup_work_deadline
        assert deadline is not None
        self._observe(supervised)
        if supervised._identity_error:
            return
        if loop.time() < deadline:
            if supervised._grace_seconds > 0:
                self._signal(supervised, signal.SIGTERM)
                grace_end = min(deadline, loop.time() + supervised._grace_seconds)
                while loop.time() < grace_end:
                    self._observe(supervised)
                    if supervised._identity_error or (
                        supervised.process.returncode is not None
                        and all(state.eof or state.error for state in supervised._pipes)
                    ):
                        break
                    await asyncio.sleep(min(0.01, max(0.0, grace_end - loop.time())))
            # A zombie leader still pins the owned group. Remaining descendants
            # may have closed both pipes; EOF is not a reason to skip this decision.
        self._signal(supervised, signal.SIGKILL)
        self._final_reap(supervised)
        # All group signal decisions are finished. From here on only reap/observe.
        while loop.time() < deadline:
            self._observe(supervised)
            self._final_reap(supervised)
            if supervised._reaped or supervised._identity_error:
                break
            await asyncio.sleep(min(0.01, max(0.0, deadline - loop.time())))

    async def _finish(self, supervised: SupervisedProcess) -> CollectedProcess:
        loop = asyncio.get_running_loop()
        deadline = supervised._cleanup_work_deadline
        group_empty = supervised._reaped and self._group_absent(supervised)
        # Group zombies may need their own parent to reap them. Wait within the
        # same fixed budget, but never claim that SIGKILL itself proves absence.
        if deadline is not None:
            while loop.time() < deadline and not supervised._identity_error:
                streams_done = all(state.task and state.task.done() for state in supervised._pipes)
                if group_empty and streams_done:
                    break
                await asyncio.sleep(min(0.01, max(0.0, deadline - loop.time())))
                group_empty = supervised._reaped and self._group_absent(supervised)
        for state, pipe in zip(
            supervised._pipes,
            (supervised.process._child.stdout, supervised.process._child.stderr),
            strict=True,
        ):
            if not state.eof:
                state.forced_closed = True
                if state.transport is not None:
                    state.transport.close()
                elif pipe is not None:
                    creation = supervised._creation_task
                    if creation is not None and not creation.done():
                        # connect_read_pipe owns the descriptor while awaiting
                        # attachment. Let its cancellation close any transport
                        # before touching that descriptor, avoiding FD reuse.
                        creation.cancel()
                    else:
                        pipe.close()
            if state.task is not None and not state.task.done():
                state.task.cancel()
        # Let cooperative collectors finish without an unbounded gather on a
        # faulty reader that might swallow cancellation.
        await asyncio.sleep(0)
        pipes = tuple(state.receipt() for state in supervised._pipes)
        reasons = list(supervised._reasons)
        if supervised._identity_error:
            reasons.append(supervised._identity_error)
        if not supervised._reaped:
            reasons.append("CHILD_NOT_REAPED")
        if not group_empty:
            reasons.append("GROUP_NOT_OBSERVED_EMPTY")
        for name, state in zip(("STDOUT", "STDERR"), pipes, strict=True):
            if not state.eof:
                reasons.append(f"{name}_EOF_NOT_OBSERVED")
            if state.error:
                reasons.append(f"{name}_READER_ERROR")
            if not state.collector_done:
                reasons.append(f"{name}_COLLECTOR_NOT_DONE")
        confirmed = (
            supervised._reaped
            and group_empty
            and not supervised._identity_error
            and all(
                p.eof and not p.error and not p.forced_closed and p.collector_done for p in pipes
            )
        )
        if supervised._cleanup_deadline is None:
            # Ordinary exit used no termination budget; give its receipt the
            # same bounded persistence allowance, fixed exactly once.
            supervised._cleanup_deadline = loop.time() + min(1.0, supervised._cleanup_budget)
        receipt = CleanupResult(
            status=CleanupStatus.CONFIRMED if confirmed else CleanupStatus.UNKNOWN,
            scope="CREATED_PROCESS_GROUP",
            capability=supervised._capability,
            child_exited=supervised.process.returncode is not None,
            child_reaped=supervised._reaped,
            exit_code=supervised.process.returncode,
            stdout=pipes[0],
            stderr=pipes[1],
            group_empty=bool(group_empty),
            signals=tuple(supervised._signals),
            reasons=tuple(dict.fromkeys(reasons)),
            deadline_monotonic=supervised._cleanup_deadline,
        )
        result = CollectedProcess(
            exit_code=receipt.exit_code,
            stdout=bytes(supervised._pipes[0].chunks),
            stderr=bytes(supervised._pipes[1].chunks),
            truncated=any(state.truncated for state in supervised._pipes),
            cleanup=receipt,
            timed_out=supervised._timed_out,
        )
        supervised.cleanup_result = receipt
        supervised.collected_result = result
        if confirmed:
            # A PID may be reused after final reap. An old receipt must never
            # remove a different newly captured owner from the local index.
            if self._children.get(supervised.pid) is supervised:
                self._children.pop(supervised.pid)
            self._retained.remove(supervised)
        return result

    async def wait(self, supervised: SupervisedProcess) -> tuple[int, bytes, bytes]:
        result = await self.collect(supervised)
        assert result.exit_code is not None
        return result.exit_code, result.stdout, result.stderr

    async def cancel(self, supervised: SupervisedProcess, *, grace_seconds: float = 2.0) -> int:
        receipt = await self.cleanup(supervised, grace_seconds=grace_seconds)
        if not receipt.confirmed:
            raise CleanupIncomplete(receipt)
        assert receipt.exit_code is not None
        return receipt.exit_code

    def emergency_kill(self, supervised: SupervisedProcess) -> None:
        """Request retained bounded cleanup, never discard ownership on signal."""
        self._request_cleanup(supervised, reason="EMERGENCY_CLEANUP", grace_seconds=0)
        self._ensure_operation(supervised)

    async def abort_unowned(self, supervised: SupervisedProcess) -> int | None:
        receipt = await self.cleanup(supervised, grace_seconds=0)
        if not receipt.confirmed:
            raise CleanupIncomplete(receipt)
        return receipt.exit_code

    def owned_pids(self) -> tuple[int, ...]:
        return tuple(sorted(set(self._children) | {child.pid for child in self._retained}))


_OBSERVED_PROCESS: contextvars.ContextVar[SpawnTicket | None] = contextvars.ContextVar(
    "pao_observed_process", default=None
)


__all__ = [
    "CleanupIncomplete",
    "CleanupResult",
    "CleanupStatus",
    "CollectedProcess",
    "OutputCollectionError",
    "PipeCleanup",
    "ProcessSpawnError",
    "ProcessSupervisor",
    "SPAWN_DIAGNOSTICS_VERSION",
    "SpawnDiagnostics",
    "SpawnStage",
    "SpawnTicket",
    "SupervisedProcess",
]
