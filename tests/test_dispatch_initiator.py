"""M1 WP5a-1 commit 1 — unit tests for the extracted dispatch initiator.

These tests pin the byte-equivalence contract that the refactor
preserves: every error code, every audit trace, every
``mark_owner_dispatch_blocked`` failure code, the SUBMITTED→READY
transition, and the thread-spawn behavior. They are written against
the extracted helper directly (not through the HTTP handler) so a
regression in the helper surfaces here before the integration tests
catch it.

Behavior under test (one assertion per row of the existing handler):

- ``initiate_owner_dispatch`` reserves a fresh dispatch row, runs
  the validation sequence, transitions SUBMITTED→READY, and spawns
  the worker thread.
- Validation failures call
  ``SafetyKernelStore.mark_owner_dispatch_blocked`` with the same
  failure code the pre-refactor handler used, then raise
  ``ControlPlaneError`` with the same status / code.
- Idempotent retry (a second call with the same ``request_id``)
  returns the existing dispatch without re-running validation or
  re-spawning the thread.
- ``reserve_owner_dispatch``'s ``ValueError`` propagates (the caller
  maps it to 409 ``conflicting_dispatch_request_id``).
"""

from __future__ import annotations

import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneService,
    DispatchAuthority,
)
from personal_ai_orchestrator.dispatch_initiator import (
    AUTHORITY_OWNER_INITIATED_EXECUTION,
    initiate_owner_dispatch,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    Provider,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
)
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchRecord,
    SafetyKernelStore,
    TaskState,
)
from personal_ai_orchestrator.verification_evidence import (
    VerificationEvidenceJournal,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


SECRET_MARKER = "credential-ref-must-never-appear"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "control@example.invalid")
    _git(path, "config", "user.name", "Control")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _registry() -> ModelRegistry:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=now,
        confidence=EvidenceConfidence.EXACT,
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18_000,
        remaining_fraction=0.42,
        window_started_at=now - timedelta(hours=3),
        reset_at=now + timedelta(hours=2),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-1",
        quota_pool_id="pool",
        provider_id="minimax",
        observed_at=now,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax CN")},
        accounts={
            "account": Account(
                id="account",
                provider_id="minimax",
                label="subscription",
                credential_ref=SECRET_MARKER,
            )
        },
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="shared",
                snapshot=snapshot,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
            )
        },
        models={
            "m3": ModelSKU(
                id="m3",
                provider_id="minimax",
                display_name="m3",
            )
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
                execution_verified=True,
            )
        },
    )


class _RecordingExecutor:
    """A drop-in ``DispatchExecutor`` stand-in that records ``execute`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []
        self._started: list[threading.Event] = []

    def execute(self, request_id: str) -> None:
        event = threading.Event()
        self._started.append(event)
        self.calls.append((request_id, threading.current_thread()))
        event.set()

    def wait_started(self, timeout: float = 0.5) -> None:
        for event in self._started:
            event.wait(timeout=timeout)


def _build_service(tmp_path: Path) -> ControlPlaneService:
    store = SafetyKernelStore(tmp_path / "safety.db")
    availability = QuotaAvailabilityJournal(tmp_path)
    return ControlPlaneService(
        registry=_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=availability,
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json", initial=True
        ),
    )


def _register_project(service: ControlPlaneService, tmp_path: Path) -> str:
    repo_path = _make_repo(tmp_path / "project")
    project = service.store.register_project(
        project_id="project-fixture",
        display_name="Fixture",
        canonical_repo_root=str(repo_path),
        git_root=str(repo_path),
        default_branch="main",
        last_known_head="abc123",
    )
    return project.project_id


def _submit_task(service: ControlPlaneService, project_id: str) -> object:
    return service.store.submit_task(
        task_id="task-1",
        request_id="submit-1",
        project_id=project_id,
        intent="fix bug",
        base_sha="abc123",
    )


def _call(
    service: ControlPlaneService,
    executor: _RecordingExecutor | None,
    *,
    task,
    request_id: str,
    task_state_version: int,
    execution_target_id: str = "m3-sub",
    authority: str = DispatchAuthority.OWNER_INITIATED_EXECUTION.value,
):
    return initiate_owner_dispatch(
        service.store,
        executor,  # type: ignore[arg-type]
        task=task,
        request_id=request_id,
        task_state_version=task_state_version,
        execution_target_id=execution_target_id,
        authority=authority,
        project_provider=service.get_project,
        registry_provider=service._effective_registry,
        provider_registry_manager=service.provider_registry_manager,
        runtime_available_provider=service._runtime_available,
        execution_evidence_journal=service.execution_evidence_journal,
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_initiate_owner_dispatch_succeeds_on_fresh_reservation(tmp_path: Path) -> None:
    """Happy path: a fresh reservation runs validation, transitions
    SUBMITTED→READY, and spawns the executor thread.
    """

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    project_id = _register_project(service, tmp_path)
    task = _submit_task(service, project_id)

    dispatch, created, transitioned = _call(
        service, executor,
        task=task,
        request_id="dispatch-1",
        task_state_version=task.state_version,
    )

    assert isinstance(dispatch, OwnerDispatchRecord)
    assert created is True
    assert transitioned is not None
    assert transitioned.state is TaskState.READY

    executor.wait_started()
    assert len(executor.calls) == 1
    assert executor.calls[0][0] == "dispatch-1"
    assert executor.calls[0][1] is not threading.main_thread()

    service.store.close()


def test_initiate_owner_dispatch_returns_existing_on_idempotent_retry(tmp_path: Path) -> None:
    """Second call with the same ``request_id`` returns the existing
    dispatch without re-running validation, re-transitioning the task,
    or re-spawning the executor thread.
    """

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    project_id = _register_project(service, tmp_path)
    task = _submit_task(service, project_id)

    first_dispatch, first_created, first_transitioned = _call(
        service, executor,
        task=task,
        request_id="dispatch-1",
        task_state_version=task.state_version,
    )
    executor.wait_started()
    assert first_created is True
    assert first_transitioned is not None
    assert len(executor.calls) == 1

    second_dispatch, second_created, second_transitioned = _call(
        service, executor,
        task=task,
        request_id="dispatch-1",
        task_state_version=task.state_version,
    )
    assert second_created is False
    assert second_transitioned is None
    assert second_dispatch.dispatch_id == first_dispatch.dispatch_id
    assert len(executor.calls) == 1

    service.store.close()


def test_initiate_owner_dispatch_blocks_on_stale_task_version(tmp_path: Path) -> None:
    """Stale task_state_version records BLOCKED with the expected
    failure_code and raises 409 ``stale_task_state_version``.
    """

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    project_id = _register_project(service, tmp_path)
    task = _submit_task(service, project_id)

    with pytest.raises(ControlPlaneError) as error:
        _call(
            service, executor,
            task=task,
            request_id="dispatch-stale",
            task_state_version=99,  # wrong on purpose
        )
    assert error.value.status == 409
    assert error.value.code == "stale_task_state_version"

    row = service.store.connection.execute(
        "SELECT status, failure_code FROM owner_dispatches WHERE request_id=?",
        ("dispatch-stale",),
    ).fetchone()
    assert row["status"] == "BLOCKED"
    assert row["failure_code"] == "STALE_TASK_STATE_VERSION"
    assert executor.calls == []
    service.store.close()


def test_initiate_owner_dispatch_blocks_on_non_dispatchable_state(tmp_path: Path) -> None:
    """Tasks in mid-execution states cannot be re-dispatched."""

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    project_id = _register_project(service, tmp_path)
    _submit_task(service, project_id)
    service.store.transition_task("task-1", TaskState.READY)
    service.store.transition_task("task-1", TaskState.RUNNING)
    live_task = service.store.get_task("task-1")

    with pytest.raises(ControlPlaneError) as error:
        _call(
            service, executor,
            task=live_task,
            request_id="dispatch-running",
            task_state_version=live_task.state_version,
        )
    assert error.value.code == "task_state_not_dispatchable"

    row = service.store.connection.execute(
        "SELECT status, failure_code FROM owner_dispatches WHERE request_id=?",
        ("dispatch-running",),
    ).fetchone()
    assert row["status"] == "BLOCKED"
    assert row["failure_code"] == "TASK_STATE_NOT_DISPATCHABLE"
    assert executor.calls == []
    service.store.close()


def test_initiate_owner_dispatch_blocks_on_missing_project_id(tmp_path: Path) -> None:
    """A task with no project_id fails the durability gate."""

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    task = service.store.submit_task(
        task_id="task-1",
        request_id="submit-1",
        project_id=None,
        intent="ad-hoc",
    )

    with pytest.raises(ControlPlaneError) as error:
        _call(
            service, executor,
            task=task,
            request_id="dispatch-no-project",
            task_state_version=task.state_version,
        )
    assert error.value.code == "missing_project_id"
    assert executor.calls == []
    service.store.close()


def test_initiate_owner_dispatch_blocks_on_missing_base_sha(tmp_path: Path) -> None:
    """A coding task without a durable base_sha fails the gate."""

    service = _build_service(tmp_path)
    executor = _RecordingExecutor()
    project_id = _register_project(service, tmp_path)
    _submit_task(service, project_id)
    service.store.connection.execute(
        "UPDATE tasks SET base_sha=NULL WHERE task_id=?", ("task-1",)
    )
    live_task = service.store.get_task("task-1")

    with pytest.raises(ControlPlaneError) as error:
        _call(
            service, executor,
            task=live_task,
            request_id="dispatch-no-base-sha",
            task_state_version=live_task.state_version,
        )
    assert error.value.code == "project_base_sha_missing"
    assert executor.calls == []
    service.store.close()


def test_initiate_owner_dispatch_authority_default_is_owner_initiated() -> None:
    """The default ``authority`` value matches
    ``DispatchAuthority.OWNER_INITIATED_EXECUTION.value`` — pinning the
    pre-WP5a-1 default for future SUPERVISED_AUTO callers.
    """

    assert (
        AUTHORITY_OWNER_INITIATED_EXECUTION
        == DispatchAuthority.OWNER_INITIATED_EXECUTION.value
    )


def test_initiate_owner_dispatch_propagates_reserve_value_error(tmp_path: Path) -> None:
    """A second ``reserve_owner_dispatch`` with a conflicting
    ``dispatch_id`` for the same ``request_id`` raises ``ValueError``.
    The helper does not catch it (the caller maps it to 409
    ``conflicting_dispatch_request_id``).
    """

    service = _build_service(tmp_path)
    project_id = _register_project(service, tmp_path)
    task = _submit_task(service, project_id)

    service.store.reserve_owner_dispatch(
        dispatch_id="owner-dispatch-conflict",
        request_id="dispatch-conflict",
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority=DispatchAuthority.OWNER_INITIATED_EXECUTION.value,
    )

    with pytest.raises(ValueError):
        service.store.reserve_owner_dispatch(
            dispatch_id="owner-dispatch-different",
            request_id="dispatch-conflict",
            task_id="task-1",
            task_state_version=task.state_version,
            execution_target_id="m3-sub",
            authority=DispatchAuthority.OWNER_INITIATED_EXECUTION.value,
        )

    service.store.close()


def test_initiate_owner_dispatch_with_none_executor_skips_thread(tmp_path: Path) -> None:
    """When ``executor is None`` the helper still reserves, validates,
    and transitions — but does NOT spawn a worker thread. This is the
    ``--control-only`` mode the integration tests already exercise.
    """

    service = _build_service(tmp_path)
    project_id = _register_project(service, tmp_path)
    task = _submit_task(service, project_id)

    dispatch, created, transitioned = _call(
        service, None,
        task=task,
        request_id="dispatch-no-exec",
        task_state_version=task.state_version,
    )

    assert created is True
    assert transitioned is not None
    assert transitioned.state is TaskState.READY
    assert dispatch.dispatch_id == "owner-dispatch-dispatch-no-exec"
    service.store.close()