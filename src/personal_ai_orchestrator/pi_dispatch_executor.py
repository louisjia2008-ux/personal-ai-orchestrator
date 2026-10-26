"""PI-1 adapter: run PAO owner dispatches through a one-shot Pi worker.

The adapter intentionally subclasses the existing authoritative executor.
It changes only the worker-harness edge; task state, worktrees, writer locks,
quota admission, cancellation, deterministic verification, shadow evidence,
and final task authority remain owned by ``OwnerDispatchExecutor``.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from personal_ai_orchestrator.dispatch_executor import (
    MAX_WORKER_STDERR_BYTES,
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
    build_worker_env,
)
from personal_ai_orchestrator.pi5_broker import (
    DelegationBrokerContext,
    DelegationBrokerSession,
    DelegationChildExecutionPort,
    UnixDelegationBrokerServer,
)
from personal_ai_orchestrator.pi5_contract import DelegationRequest
from personal_ai_orchestrator.pi5_runtime import PI5_TOOL_NAME
from personal_ai_orchestrator.pi5_tool import pi5_socket_tool_source, seed_pi5_socket_tool
from personal_ai_orchestrator.pi5b3g_scope_validator import PI5B3GScopeValidationResult
from personal_ai_orchestrator.pi_diagnostics import PI_STOP_REASONS, pi_diagnostic_code
from personal_ai_orchestrator.pi_provider_diagnostics import provider_error_diagnostics
from personal_ai_orchestrator.pi_runtime import (
    PI_ALLOWED_TOOLS,
    PI_MAX_STDOUT_BYTES,
    PI_PROTOCOL_ERROR_EXIT,
    PiDelegationActivationError,
    PiRuntimeConfig,
    build_pi_json_argv,
    pi_model_ref,
    seed_pi_worktree_guard,
    summarize_pi_json_stream,
)
from personal_ai_orchestrator.pi_stream_capture import PiStreamCapture
from personal_ai_orchestrator.process_supervisor import SpawnStage, SupervisedProcess
from personal_ai_orchestrator.safety_kernel import (
    AUTHORITY_OWNER_INITIATED_EXECUTION,
    AUTHORITY_SUPERVISED_AUTO,
    OwnerDispatchRecord,
    SafetyKernelStore,
)
from personal_ai_orchestrator.worker_outcome_classifier import (
    WorkerFailureClass,
    classify_worker_failure,
    worker_failure_diagnostics,
)
from personal_ai_orchestrator.worktree_manager import ManagedWorktree


def should_pao_delegate_be_model_visible(
    config: PiRuntimeConfig, authority: str, task_request_id: str
) -> bool:
    """One host policy decision; durable child identity also survives owner retry."""
    return (
        config.delegation_enabled
        and authority in (AUTHORITY_OWNER_INITIATED_EXECUTION, AUTHORITY_SUPERVISED_AUTO)
        and not task_request_id.startswith("pi5-child-submit-")
    )


class PiOwnerDispatchExecutor(OwnerDispatchExecutor):
    """Owner-dispatch executor whose child runtime is Pi JSON mode.

    ``OwnerDispatchExecutor`` still performs the complete host authority
    pipeline. The only substitutions here are runtime availability, argv,
    host guard seeding, and fail-closed validation of Pi's JSONL stream.
    """

    def __init__(self, *, pi_runtime: PiRuntimeConfig | None = None, **kwargs: object) -> None:
        runtime = pi_runtime or PiRuntimeConfig()
        config = kwargs.get("config")
        if not isinstance(config, DispatchExecutorConfig):
            raise TypeError("PiOwnerDispatchExecutor requires DispatchExecutorConfig")

        # The base executor's runtime-availability gate intentionally remains
        # untouched. Point that existing host-owned gate at the selected Pi
        # binary, then keep the Pi-specific command construction in this
        # adapter. No Safety Kernel or verification semantics are forked.
        kwargs["config"] = replace(config, opencode_bin=runtime.pi_bin)
        self.pi_runtime = runtime
        self.delegation_child_port: DelegationChildExecutionPort | None = None
        self.delegation_scope_validator_factory: (
            Callable[
                [DelegationBrokerContext],
                Callable[[DelegationRequest], PI5B3GScopeValidationResult],
            ]
            | None
        ) = None
        self.delegation_scope_trace_sink: Callable[[PI5B3GScopeValidationResult], None] | None = (
            None
        )
        self._delegation_brokers: dict[str, UnixDelegationBrokerServer] = {}
        super().__init__(**kwargs)  # type: ignore[arg-type]

    async def execute_async(self, request_id: str) -> None:
        """Run normal authority flow, then append best-effort parent outcome evidence."""
        try:
            await super().execute_async(request_id)
        finally:
            self._record_parent_delegation_outcomes(request_id)

    def _record_parent_delegation_outcomes(self, request_id: str) -> None:
        port = self.delegation_child_port
        record_method = getattr(port, "record_parent_final_outcomes", None)
        if port is None or not callable(record_method):
            return
        store = SafetyKernelStore(self._state_db)
        try:
            try:
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                task = store.get_task(dispatch.task_id)
            except (KeyError, ValueError):
                return
            if not should_pao_delegate_be_model_visible(
                self.pi_runtime,
                dispatch.authority,
                task.request_id,
            ):
                return
            parent_task_id = task.task_id
            parent_run_id = f"run-{dispatch.dispatch_id}"
        finally:
            store.close()
        try:
            record_method(
                parent_task_id=parent_task_id,
                parent_run_id=parent_run_id,
            )
        except Exception:
            # Outcome enrichment is SHADOW evidence only and has zero task authority.
            pass

    def _activate_worker_session(self, request_id: str) -> None:
        broker = self._delegation_brokers.get(request_id)
        if broker is not None:
            broker.session.active = True

    def _disable_worker_session(self, request_id: str) -> None:
        broker = self._delegation_brokers.pop(request_id, None)
        if broker is not None:
            broker.close()
            shutil.rmtree(broker.socket_path.parent, ignore_errors=True)

    def _guard_path(self) -> Path:
        # Keep policy outside the writable task worktree. The Pi file tools
        # are confined to the worktree, so the worker cannot weaken this file.
        runtime_policy_root = self.config.worktree_root / ".pao-runtime"
        return seed_pi_worktree_guard(runtime_policy_root)

    def _seed_worker_policy(self, managed: ManagedWorktree) -> None:
        del managed
        # Overwrite on every adopt/create so a previous process cannot persist
        # a weakened policy into the next run.
        self._guard_path()

    def _pi_model_ref(self, dispatch: OwnerDispatchRecord) -> str:
        return pi_model_ref(
            provider_id=self._provider_id(dispatch),
            model_sku_id=self._model_sku_id(dispatch),
        )

    async def _spawn_worker(
        self,
        store: SafetyKernelStore,
        dispatch: OwnerDispatchRecord,
        worktree: ManagedWorktree,
    ) -> SupervisedProcess:
        try:
            return await self._spawn_with_activation(store, dispatch, worktree)
        except PiDelegationActivationError as error:
            store._audit(dispatch.task_id, error.code, {"reason_code": error.reason_code})
            raise

    async def _spawn_with_activation(self, store, dispatch, worktree) -> SupervisedProcess:
        task = store.get_task(dispatch.task_id)
        enabled = should_pao_delegate_be_model_visible(
            self.pi_runtime, dispatch.authority, task.request_id
        )
        tool_path = None
        if enabled:
            if (
                self.delegation_child_port is None
                or task.project_id is None
                or task.base_sha is None
            ):
                raise PiDelegationActivationError("HOST_DEPENDENCIES_UNAVAILABLE")
            policy_root = Path(tempfile.mkdtemp(prefix="pao-pi5-"))
            broker_context = DelegationBrokerContext(
                parent_task_id=task.task_id,
                parent_run_id=f"run-{dispatch.dispatch_id}",
                project_id=task.project_id,
                base_sha=task.base_sha,
                working_subpath=task.working_subpath,
            )
            scope_validator = (
                None
                if self.delegation_scope_validator_factory is None
                else self.delegation_scope_validator_factory(broker_context)
            )
            broker = UnixDelegationBrokerServer(
                socket_path=policy_root / "broker.sock",
                session=DelegationBrokerSession(
                    context=broker_context,
                    child_port=self.delegation_child_port,
                    active=False,
                    scope_validator=scope_validator,
                    scope_trace_sink=self.delegation_scope_trace_sink,
                ),
            )
            self._delegation_brokers[dispatch.request_id] = broker
            try:
                await broker.start()
                tool_path = seed_pi5_socket_tool(
                    policy_root,
                    socket_path=broker.socket_path,
                )
                if (
                    not tool_path.is_file()
                    or tool_path.read_text(encoding="utf-8")
                    != pi5_socket_tool_source(broker.socket_path)
                    or not broker.socket_path.is_socket()
                ):
                    raise PiDelegationActivationError("TRUSTED_TOOL_UNRESOLVED")
            except PiDelegationActivationError:
                raise
            except (OSError, UnicodeError):
                raise PiDelegationActivationError("TRUSTED_TOOL_UNAVAILABLE") from None
        argv = build_pi_json_argv(
            config=replace(self.pi_runtime, delegation_enabled=enabled),
            model_ref=self._pi_model_ref(dispatch),
            intent=self._worker_intent(task),
            guard_path=self._guard_path(),
            delegation_tool_path=tool_path,
        )
        if enabled:
            expected_tools = ",".join((*PI_ALLOWED_TOOLS, PI5_TOOL_NAME))
            if (
                argv.count("--tools") != 1
                or argv[argv.index("--tools") + 1] != expected_tools
                or argv.count(str(tool_path)) != 1
                or "--no-extensions" not in argv
            ):
                raise PiDelegationActivationError("MODEL_TOOL_VISIBILITY_MISMATCH")
        store._audit(
            dispatch.task_id,
            "WORKER_ARGV_BUILT",
            {
                "dispatch_id": dispatch.dispatch_id,
                "runtime": "pi-json",
                "argv": list(argv),
                "cwd": str(worktree.worktree_path),
            },
        )
        env = build_worker_env()
        self._audit_spawn(
            store,
            dispatch.task_id,
            "PROCESS_CREATE_STARTED",
            self._supervisor.inspect_contract(argv, cwd=worktree.worktree_path, env=env).evolved(
                spawn_stage=SpawnStage.PROCESS_CREATE_STARTED
            ),
        )
        return await self._supervisor.start(
            argv,
            cwd=worktree.worktree_path,
            env=env,
        )

    @staticmethod
    def _expected_model_ref(supervised: SupervisedProcess) -> str | None:
        try:
            index = supervised.argv.index("--model")
            value = supervised.argv[index + 1]
        except (ValueError, IndexError):
            return None
        return value if "/" in value else None

    async def _wait_for_worker(
        self, supervised: SupervisedProcess
    ) -> tuple[int, bytes, bytes, bool]:
        """Validate all bounded protocol bytes independently of transcript retention."""
        capture = PiStreamCapture()
        result = await self._supervisor.collect(
            supervised,
            timeout_seconds=self.config.worker_timeout_seconds,
            stdout_cap=PI_MAX_STDOUT_BYTES,
            stderr_cap=MAX_WORKER_STDERR_BYTES,
            grace_seconds=self.config.worker_grace_seconds,
            cleanup_budget_seconds=self.config.worker_cleanup_budget_seconds,
            eof_grace_seconds=self.config.worker_eof_grace_seconds,
            stdout_observer=capture,
        )
        stdout, stderr, truncated = result.stdout, result.stderr, result.truncated
        returncode = result.exit_code

        summary = capture.finish(eof=result.cleanup.stdout.eof)
        if capture.limit_reason is not None:
            returncode = PI_PROTOCOL_ERROR_EXIT
        elif returncode == 0:
            expected_model_ref = self._expected_model_ref(supervised)
            if (
                supervised.stderr_truncated
                or expected_model_ref is None
                or not summary.matches_model_ref(expected_model_ref)
            ):
                # A clean OS exit is not enough to claim a real Pi worker
                # invocation. The final assistant event must be a normal
                # stop on the exact PAO-selected provider/model.
                returncode = PI_PROTOCOL_ERROR_EXIT
        return returncode, stdout, stderr, truncated

    @classmethod
    def _host_result_envelope(
        cls,
        exit_code: int,
        stdout: bytes,
        stderr: bytes,
        *,
        truncated: bool,
        timeout: bool = False,
        expected_model_ref: str | None = None,
        process_exit_code: int | None = None,
        stream_capture: PiStreamCapture | None = None,
        stdout_capture_truncated: bool = False,
        stderr_capture_truncated: bool = False,
    ) -> dict[str, object]:
        result = super()._host_result_envelope(
            exit_code,
            stdout,
            stderr,
            truncated=truncated,
            timeout=timeout,
        )
        # Only this exact child's finished observer may supplement a retained
        # prefix. Direct callers without full-stream evidence remain fail closed.
        observed = stream_capture is not None and stream_capture.summary is not None
        summary = stream_capture.summary if observed else summarize_pi_json_stream(stdout)
        protocol_truncated = stderr_capture_truncated if observed else truncated
        code = pi_diagnostic_code(
            summary,
            expected_model_ref=expected_model_ref,
            process_exit_code=(
                0 if observed and stream_capture.limit_reason else process_exit_code
            ),
            truncated=protocol_truncated,
            timeout=timeout,
        )
        # Only the protocol error channel may supplement stderr. Assistant
        # content and malformed/incomplete protocol output are not diagnostics.
        error_message = (
            summary.final_error_message
            if (
                summary.protocol_valid
                and summary.session_seen
                and summary.agent_start_seen
                and summary.agent_end_seen
                and not protocol_truncated
                and summary.final_stop_reason == "error"
            )
            else None
        )
        verdict = classify_worker_failure(
            exit_code=exit_code,
            stderr_tail="\n".join(
                value for value in (cls._sanitize_transcript(stderr), error_message) if value
            ),
        )
        provider_details = (
            provider_error_diagnostics(error_message)
            if code == "PI_PROVIDER_ERROR" and summary.protocol_valid
            else {}
        )
        if provider_details.get("pi_provider_error_reason") == "CONFLICTING_ERROR":
            verdict = WorkerFailureClass.UNCLASSIFIED
        if (
            exit_code == PI_PROTOCOL_ERROR_EXIT
            and verdict is WorkerFailureClass.UNCLASSIFIED
            and code
            not in {
                "PI_PROVIDER_ERROR",
                "PI_PROCESS_EXIT",
                "PI_TIMEOUT",
                "PI_EXPECTED_MODEL_UNAVAILABLE",
                "PI_DIAGNOSTICS_UNAVAILABLE",
            }
        ):
            verdict = WorkerFailureClass.PROTOCOL_ERROR
        diagnostics = worker_failure_diagnostics(verdict)
        result.update(diagnostics)
        result.update(
            {
                "runtime": "pi-json",
                "pi_diagnostic_code": code,
                "pi_protocol_valid": code == "PI_COMPLETE",
                "pi_stream_valid": summary.protocol_valid,
                "pi_session_seen": summary.session_seen,
                "pi_agent_start_seen": summary.agent_start_seen,
                "pi_agent_end_seen": summary.agent_end_seen,
                "pi_model_matches": (
                    f"{summary.final_provider}/{summary.final_model}" == expected_model_ref
                    if expected_model_ref is not None
                    else None
                ),
                "pi_event_count": summary.event_count,
                "pi_tool_start_count": summary.tool_start_count,
                "pi_tool_end_count": summary.tool_end_count,
                "pi_extension_error_count": summary.extension_error_count,
                "pi_provider": summary.final_provider if exit_code == 0 else None,
                "pi_model": summary.final_model if exit_code == 0 else None,
                "pi_stop_reason": (
                    summary.final_stop_reason
                    if summary.final_stop_reason in PI_STOP_REASONS
                    else None
                ),
                "pi_error_message": (
                    diagnostics["worker_failure_message"] if error_message else None
                ),
                "pi_parse_error": summary.parse_error,
            }
        )
        if process_exit_code is not None:
            result["worker_process_exit_code"] = process_exit_code
        result.update(provider_details)
        # Raw Pi events can contain credentials in tool arguments or results.
        # Keep only hashes/counts and bounded allowlisted diagnostics in the DB.
        result["stdout_tail"] = ""
        result["stderr_tail"] = ""
        if observed:
            result.update(stream_capture.metadata())
            result["pi_stdout_capture_truncated"] = stdout_capture_truncated
            result["pi_stderr_capture_truncated"] = stderr_capture_truncated
        return result

    def _worker_result_envelope(
        self,
        supervised: SupervisedProcess,
        exit_code: int,
        stdout: bytes,
        stderr: bytes,
        *,
        truncated: bool,
    ) -> dict[str, object]:
        return self._host_result_envelope(
            exit_code,
            stdout,
            stderr,
            truncated=truncated,
            expected_model_ref=self._expected_model_ref(supervised),
            process_exit_code=supervised.process.returncode,
            stream_capture=(
                supervised.stdout_observer
                if isinstance(supervised.stdout_observer, PiStreamCapture)
                else None
            ),
            stdout_capture_truncated=supervised.stdout_truncated,
            stderr_capture_truncated=supervised.stderr_truncated,
        )


__all__ = ["PiOwnerDispatchExecutor"]
