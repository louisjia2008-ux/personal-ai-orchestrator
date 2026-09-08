"""M1 WP5a-1 commit 1 — owner-dispatch initiation helper.

Pure extraction of the dispatch-reservation + validation + transition
+ thread-spawn sequence from ``control_api.ControlPlaneService.dispatch_task``.

The helper takes the owner-dispatch parameters the handler has
parsed from the request and:

1. Reserves the dispatch record via
   ``SafetyKernelStore.reserve_owner_dispatch`` (idempotent on the
   same ``request_id``).
2. Returns early when the reservation is a duplicate (a previous
   dispatch with the same ``request_id`` already exists) — the
   caller renders the existing record.
3. When the reservation is fresh, runs the full validation sequence
   (``task_state_version`` staleness, ``task.state`` dispatchability,
   ``project_id`` / ``base_sha`` durability, project availability,
   provider connection, execution-target launchability) — failures
   call ``SafetyKernelStore.mark_owner_dispatch_blocked`` to leave
   the same BLOCKED audit trail the pre-refactor handler produced
   before raising ``ControlPlaneError``.
4. On validation success, transitions ``SUBMITTED → READY`` if the
   task is still in ``SUBMITTED``, then spawns the
   ``DispatchExecutor.execute`` thread.

All order-of-operations are preserved: validation runs **after**
reserve (so the BLOCKED row trace is identical to the pre-refactor
behavior) and **before** transition + thread spawn (so a failed
validation does not strand a live worker).

Authority value is forwarded to ``SafetyKernelStore.reserve_owner_dispatch``
unchanged. The current ``OWNER_INITIATED_EXECUTION`` value is the
default; WP5a-2 will introduce a SUPERVISED_AUTO authority by passing
a different value through the same ``authority`` parameter.

Note: this helper raises ``ControlPlaneError`` on validation failure
because the validation logic and the ``mark_owner_dispatch_blocked``
audit trail are inseparable from the validation itself; the helper
imports the exception type from ``control_api`` for type identity
(the HTTP status mapping at the caller boundary is unchanged).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from personal_ai_orchestrator.execution_controller import (
    validate_execution_target_launch,
)
from personal_ai_orchestrator.safety_kernel import (
    ProjectAvailability,
    ProjectRecord,
    SafetyKernelStore,
    TaskState,
)


def _control_plane_error(status: int, code: str) -> Any:
    """Lazy import of ``ControlPlaneError`` to avoid a circular dependency
    between ``control_api`` (which imports ``initiate_owner_dispatch``)
    and this one (which raises ``ControlPlaneError``).
    """
    from personal_ai_orchestrator.control_api import ControlPlaneError

    return ControlPlaneError(status, code)

if TYPE_CHECKING:
    from personal_ai_orchestrator.dispatch_executor import DispatchExecutor
    from personal_ai_orchestrator.safety_kernel import (
        OwnerDispatchRecord,
        TaskRecord,
    )


#: String value forwarded to ``SafetyKernelStore.reserve_owner_dispatch``
#: as ``authority=...``. Kept here (rather than imported from
#: ``control_api``) so the authority value is owned by this module.
AUTHORITY_OWNER_INITIATED_EXECUTION = "OWNER_INITIATED_EXECUTION"


def initiate_owner_dispatch(
    store: SafetyKernelStore,
    executor: DispatchExecutor | None,
    *,
    task: TaskRecord,
    request_id: str,
    task_state_version: int,
    execution_target_id: str,
    authority: str = AUTHORITY_OWNER_INITIATED_EXECUTION,
    project_provider: Callable[[str], ProjectRecord],
    registry_provider: Callable[[], Any],
    provider_registry_manager: Any | None,
    runtime_available_provider: Callable[[str], bool],
    execution_evidence_journal: Any | None,
) -> tuple[OwnerDispatchRecord, bool, TaskRecord | None]:
    """Reserve, validate, transition, and spawn — the irreversible tail.

    Returns:
        - ``(dispatch, created=False, None)`` when a previous dispatch
          with the same ``request_id`` exists. No validation runs, no
          transition, no thread spawned.
        - ``(dispatch, created=True, transitioned_task_or_none)`` when
          a fresh reservation succeeded AND all validation guards
          passed. ``transitioned_task`` is the post-transition
          ``TaskRecord`` when ``SUBMITTED → READY`` fired; otherwise
          ``None``. The worker thread is spawned when
          ``executor is not None``.

    Raises:
        ``ControlPlaneError`` (status / code as below) when validation
        fails. The BLOCKED audit row is recorded via
        ``SafetyKernelStore.mark_owner_dispatch_blocked`` before the
        exception is raised, matching the pre-refactor behavior:

        - 409 ``stale_task_state_version``
        - 409 ``task_state_not_dispatchable``
        - 409 ``missing_project_id``
        - 409 ``project_base_sha_missing``
        - 409 ``project_not_available``
        - 409 ``provider_not_connected``
        - 409 ``execution_target_not_launchable``

        ``ValueError`` (uncaught by this helper) propagates from
        ``reserve_owner_dispatch`` when the durable record conflicts;
        the caller maps it to 409 ``conflicting_dispatch_request_id``.

    Parameters:
        - ``project_provider``: callable returning a
          ``ProjectRecord`` (or view-model exposing
          ``storage_availability``) for the task's project. Mirrors
          ``ControlPlaneService.get_project``.
        - ``registry_provider``: callable returning the effective
          ``ModelRegistry`` (either the provider-registry-manager's
          snapshot or the static one).
        - ``provider_registry_manager``: ``None`` when the daemon
          uses the static registry; otherwise an object exposing
          ``.connected_provider_ids()``. The provider-connection
          check is a no-op when this is ``None`` (matches the
          pre-refactor handler).
        - ``runtime_available_provider``: callable returning the
          runtime-availability boolean for one execution target.
        - ``execution_evidence_journal``: forwarded to
          ``validate_execution_target_launch``.
    """

    dispatch_id = f"owner-dispatch-{request_id}"
    dispatch, created = store.reserve_owner_dispatch(
        dispatch_id=dispatch_id,
        request_id=request_id,
        task_id=task.task_id,
        task_state_version=task_state_version,
        execution_target_id=execution_target_id,
        authority=authority,
    )
    if not created:
        return dispatch, False, None

    # --- Validation guards (preserved in pre-refactor order) -----------
    if task.state_version != task_state_version:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code="STALE_TASK_STATE_VERSION",
            failure_reason="dispatch task_state_version did not match authoritative task",
        )
        raise _control_plane_error(409, "stale_task_state_version")
    if task.state not in {TaskState.SUBMITTED, TaskState.READY}:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code="TASK_STATE_NOT_DISPATCHABLE",
            failure_reason=f"task state {task.state.value} is not dispatchable",
        )
        raise _control_plane_error(409, "task_state_not_dispatchable")
    if task.project_id is None:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code="MISSING_PROJECT_ID",
            failure_reason="coding tasks require an explicit registered project",
        )
        raise _control_plane_error(409, "missing_project_id")
    if task.base_sha is None:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code="PROJECT_BASE_SHA_MISSING",
            failure_reason="coding tasks require a durable base_sha",
        )
        raise _control_plane_error(409, "project_base_sha_missing")
    project_view = project_provider(task.project_id)
    if project_view.storage_availability != ProjectAvailability.ONLINE.value:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code=f"PROJECT_{project_view.storage_availability}",
            failure_reason="registered project is not currently available",
        )
        raise _control_plane_error(409, "project_not_available")

    effective_registry = registry_provider()
    if provider_registry_manager is not None:
        target = effective_registry.execution_targets.get(execution_target_id)
        model = (
            effective_registry.models.get(target.model_sku_id)
            if target is not None
            else None
        )
        connected_provider_ids = provider_registry_manager.connected_provider_ids()
        if model is None or model.provider_id not in connected_provider_ids:
            store.mark_owner_dispatch_blocked(
                request_id,
                failure_code="PROVIDER_NOT_CONNECTED",
                failure_reason="execution target provider is not connected by owner",
            )
            raise _control_plane_error(
                409, "provider_not_connected"
            ) from None
    try:
        validate_execution_target_launch(
            effective_registry,
            execution_target_id=execution_target_id,
            runtime_available=runtime_available_provider(execution_target_id),
            execution_evidence_journal=execution_evidence_journal,
        )
    except RuntimeError as error:
        store.mark_owner_dispatch_blocked(
            request_id,
            failure_code="EXECUTION_TARGET_NOT_LAUNCHABLE",
            failure_reason=str(error),
        )
        raise _control_plane_error(409, "execution_target_not_launchable") from error

    # --- All guards passed: transition + thread -------------------------
    transitioned_task = None
    if task.state is TaskState.SUBMITTED:
        transitioned_task = store.transition_task(
            task.task_id,
            TaskState.READY,
            expected_version=task.state_version,
            reason="owner initiated execution dispatch reserved",
        )
    if executor is not None:
        thread = threading.Thread(
            target=executor.execute,
            args=(request_id,),
            name=f"owner-dispatch-{request_id}",
            daemon=True,
        )
        thread.start()
    return dispatch, True, transitioned_task


__all__ = [
    "AUTHORITY_OWNER_INITIATED_EXECUTION",
    "initiate_owner_dispatch",
]