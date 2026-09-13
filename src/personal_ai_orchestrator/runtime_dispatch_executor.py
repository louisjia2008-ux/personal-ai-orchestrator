"""Dispatch to the host-owned executor matching the selected runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    SafetyKernelStore,
)


class _ExecutionSupervisorProxy:
    def __init__(self, executors: Mapping[str, Any]) -> None:
        self._executors = executors

    def get(self, task_id: str) -> Any | None:
        for executor in self._executors.values():
            supervisor = getattr(executor, "execution_supervisor", None)
            if supervisor is None:
                continue
            active = supervisor.get(task_id)
            if active is not None:
                return active
        return None


class RuntimeDispatchExecutor:
    """Select an existing full-authority executor by target.runtime_id.

    This class is intentionally a routing adapter, not a second execution
    authority. Every underlying executor remains responsible for its own
    launch-time validation, quota admission, worktree/writer lifecycle,
    cancellation, and deterministic verification.
    """

    def __init__(
        self,
        *,
        state_db: str | Path,
        registry_provider: Callable[[], ModelRegistry],
        executors: Mapping[str, Any],
    ) -> None:
        self._state_db = state_db
        self._registry_provider = registry_provider
        self._executors = dict(executors)
        self.execution_supervisor = _ExecutionSupervisorProxy(self._executors)

    def execute(self, request_id: str) -> None:
        store = SafetyKernelStore(self._state_db)
        try:
            try:
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
            except KeyError:
                return
            if dispatch.status is not OwnerDispatchStatus.RESERVED:
                return

            target = self._registry_provider().execution_targets.get(
                dispatch.execution_target_id
            )
            if target is None:
                store.mark_owner_dispatch_blocked(
                    request_id,
                    failure_code="EXECUTION_TARGET_NOT_LAUNCHABLE",
                    failure_reason="execution target disappeared from the live registry",
                )
                return

            executor = self._executors.get(target.runtime_id)
            if executor is None:
                store.mark_owner_dispatch_blocked(
                    request_id,
                    failure_code="UNSUPPORTED_RUNTIME",
                    failure_reason=f"runtime {target.runtime_id!r} has no executor",
                )
                return
        finally:
            store.close()

        executor.execute(request_id)

    def cancel_active(self, task_id: str) -> bool:
        for executor in self._executors.values():
            supervisor = getattr(executor, "execution_supervisor", None)
            if supervisor is None or supervisor.get(task_id) is None:
                continue
            cancel_active = getattr(executor, "cancel_active", None)
            if cancel_active is None:
                return False
            return bool(cancel_active(task_id))
        return False


__all__ = ["RuntimeDispatchExecutor"]
