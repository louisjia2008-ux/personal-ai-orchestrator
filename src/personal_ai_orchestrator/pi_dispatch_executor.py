"""PI-1 adapter: run PAO owner dispatches through a one-shot Pi worker.

The adapter intentionally subclasses the existing authoritative executor.
It changes only the worker-harness edge; task state, worktrees, writer locks,
quota admission, cancellation, deterministic verification, shadow evidence,
and final task authority remain owned by ``OwnerDispatchExecutor``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.dispatch_executor import (
    MAX_WORKER_STDERR_BYTES,
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
    build_worker_env,
)
from personal_ai_orchestrator.pi_runtime import (
    PI_MAX_STDOUT_BYTES,
    PI_PROTOCOL_ERROR_EXIT,
    PiRuntimeConfig,
    build_pi_json_argv,
    pi_model_ref,
    seed_pi_worktree_guard,
    summarize_pi_json_stream,
)
from personal_ai_orchestrator.process_supervisor import SupervisedProcess
from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecord, SafetyKernelStore
from personal_ai_orchestrator.worktree_manager import ManagedWorktree


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
        super().__init__(**kwargs)  # type: ignore[arg-type]

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
        task = store.get_task(dispatch.task_id)
        argv = build_pi_json_argv(
            config=self.pi_runtime,
            model_ref=self._pi_model_ref(dispatch),
            intent=task.intent,
            guard_path=self._guard_path(),
        )
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
            if truncated or not summary.completed:
                # A clean OS exit is not enough to claim a real Pi worker
                # invocation. Fail closed before WORKER_FINISHED so the
                # deterministic verifier can never bless an incomplete stream.
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
                "pi_parse_error": summary.parse_error,
            }
        )
        return result


__all__ = ["PiOwnerDispatchExecutor"]
