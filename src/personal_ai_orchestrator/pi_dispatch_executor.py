"""PI-1 adapter: run PAO owner dispatches through a one-shot Pi worker.

The adapter intentionally subclasses the existing authoritative executor.
It changes only the worker-harness edge; task state, worktrees, writer locks,
quota admission, cancellation, deterministic verification, shadow evidence,
and final task authority remain owned by ``OwnerDispatchExecutor``.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

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
from personal_ai_orchestrator.pi5_runtime import PI5_TOOL_NAME
from personal_ai_orchestrator.pi5_tool import pi5_socket_tool_source, seed_pi5_socket_tool
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
from personal_ai_orchestrator.process_supervisor import SupervisedProcess
from personal_ai_orchestrator.safety_kernel import (
    AUTHORITY_OWNER_INITIATED_EXECUTION,
    AUTHORITY_SUPERVISED_AUTO,
    OwnerDispatchRecord,
    SafetyKernelStore,
)
from personal_ai_orchestrator.worker_outcome_classifier import WorkerFailureClass
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
        self._delegation_brokers: dict[str, UnixDelegationBrokerServer] = {}
        super().__init__(**kwargs)  # type: ignore[arg-type]

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
            if (self.delegation_child_port is None
                    or task.project_id is None or task.base_sha is None):
                raise PiDelegationActivationError("HOST_DEPENDENCIES_UNAVAILABLE")
            policy_root = Path(tempfile.mkdtemp(prefix="pao-pi5-"))
            broker = UnixDelegationBrokerServer(
                socket_path=policy_root / "broker.sock",
                session=DelegationBrokerSession(
                    context=DelegationBrokerContext(
                        parent_task_id=task.task_id, parent_run_id=f"run-{dispatch.dispatch_id}",
                        project_id=task.project_id, base_sha=task.base_sha,
                        working_subpath=task.working_subpath,
                    ),
                    child_port=self.delegation_child_port, active=False,
                ),
            )
            self._delegation_brokers[dispatch.request_id] = broker
            try:
                await broker.start()
                tool_path = seed_pi5_socket_tool(policy_root, socket_path=broker.socket_path)
                if (not tool_path.is_file()
                        or tool_path.read_text(encoding="utf-8")
                        != pi5_socket_tool_source(broker.socket_path)
                        or not broker.socket_path.is_socket()):
                    raise PiDelegationActivationError("TRUSTED_TOOL_UNRESOLVED")
            except PiDelegationActivationError:
                raise
            except (OSError, UnicodeError):
                raise PiDelegationActivationError("TRUSTED_TOOL_UNAVAILABLE") from None
        argv = build_pi_json_argv(
            config=replace(self.pi_runtime, delegation_enabled=enabled),
            model_ref=self._pi_model_ref(dispatch),
            intent=task.intent,
            guard_path=self._guard_path(),
            delegation_tool_path=tool_path,
        )
        if enabled:
            expected_tools = ",".join((*PI_ALLOWED_TOOLS, PI5_TOOL_NAME))
            if (argv.count("--tools") != 1
                    or argv[argv.index("--tools") + 1] != expected_tools
                    or argv.count(str(tool_path)) != 1
                    or "--no-extensions" not in argv):
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
        return await self._supervisor.start(
            argv,
            cwd=worktree.worktree_path,
            env=build_worker_env(),
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
        """Drain one Pi JSON stream with a larger but still bounded cap."""

        chunks: dict[str, list[bytes]] = {"out": [], "err": []}
        truncated = False

        async def _drain(stream: Any, cap: int, key: str) -> None:
            nonlocal truncated
            total = 0
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                remaining = max(0, cap - total)
                if remaining:
                    chunks[key].append(chunk[:remaining])
                total += len(chunk)
                if total > cap:
                    truncated = True

        stdout_task = asyncio.create_task(
            _drain(supervised.process.stdout, PI_MAX_STDOUT_BYTES, "out")
        )
        stderr_task = asyncio.create_task(
            _drain(supervised.process.stderr, MAX_WORKER_STDERR_BYTES, "err")
        )
        try:
            await asyncio.wait_for(
                supervised.process.wait(), timeout=self.config.worker_timeout_seconds
            )
        except TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        await asyncio.gather(stdout_task, stderr_task)

        stdout = b"".join(chunks["out"])
        stderr = b"".join(chunks["err"])
        returncode = supervised.process.returncode or 0
        self._supervisor._children.pop(supervised.pid, None)

        if returncode == 0:
            summary = summarize_pi_json_stream(stdout)
            expected_model_ref = self._expected_model_ref(supervised)
            if (
                truncated
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
    ) -> dict[str, object]:
        result = super()._host_result_envelope(
            exit_code,
            stdout,
            stderr,
            truncated=truncated,
            timeout=timeout,
        )
        summary = summarize_pi_json_stream(stdout)
        result.update(
            {
                "runtime": "pi-json",
                "pi_protocol_valid": summary.completed and not truncated,
                "pi_event_count": summary.event_count,
                "pi_tool_start_count": summary.tool_start_count,
                "pi_tool_end_count": summary.tool_end_count,
                "pi_extension_error_count": summary.extension_error_count,
                "pi_provider": summary.final_provider,
                "pi_model": summary.final_model,
                "pi_stop_reason": summary.final_stop_reason,
                "pi_error_message": summary.final_error_message,
                "pi_parse_error": summary.parse_error,
            }
        )
        return result

    def _classify_worker_failure(
        self,
        *,
        exit_code: int,
        worker_result: dict[str, object],
    ) -> WorkerFailureClass:
        """Classify Pi provider errors without persisting raw provider bodies."""

        from personal_ai_orchestrator.worker_outcome_classifier import (
            classify_worker_failure,
        )

        stderr = worker_result.get("stderr_tail")
        error_message = worker_result.get("pi_error_message")
        diagnostic_parts = [
            value
            for value in (stderr, error_message)
            if isinstance(value, str) and value
        ]
        return classify_worker_failure(
            exit_code=exit_code,
            stderr_tail="\n".join(diagnostic_parts),
        )


__all__ = ["PiOwnerDispatchExecutor"]
