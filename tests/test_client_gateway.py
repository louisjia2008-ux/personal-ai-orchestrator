from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.client_gateway import (
    ClientKind,
    ClientOperation,
    DeskPetClientAdapter,
    DeskPetToolRequest,
    ExternalClientGateway,
    ExternalClientRequest,
    TaskStateNotifier,
    TelegramAllowlist,
    TelegramClientAdapter,
    TelegramUpdate,
)
from personal_ai_orchestrator.control_api import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    ApprovalView,
    CancelView,
    ControlPlaneError,
    TaskListView,
    TaskView,
    VerificationReportView,
)
from personal_ai_orchestrator.control_client import ControlPlaneUnavailable

NOW = datetime(2026, 9, 29, tzinfo=UTC).isoformat()


def _task(task_id: str, request_id: str, *, state: str = "SUBMITTED", version: int = 0):
    return TaskView(
        task_id=task_id,
        request_id=request_id,
        intent="typed intent",
        project_id="project-1",
        state=state,
        state_version=version,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeControlClient:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskView] = {}
        self.by_request: dict[str, str] = {}
        self.submit_calls = 0
        self.resolve_calls = 0

    def submit(
        self,
        *,
        task_id: str,
        request_id: str,
        project_id: str,
        intent: str,
        scheduling_policy: str | None = None,
        manual_execution_target_id: str | None = None,
    ) -> TaskView:
        self.submit_calls += 1
        if request_id in self.by_request:
            return self.tasks[self.by_request[request_id]]
        task = _task(task_id, request_id)
        task = task.model_copy(update={"project_id": project_id, "intent": intent})
        self.tasks[task_id] = task
        self.by_request[request_id] = task_id
        return task

    def get_task(self, task_id: str) -> TaskView:
        return self.tasks[task_id]

    def list_tasks(self, *, limit: int | None = None) -> TaskListView:
        tasks = tuple(self.tasks.values())
        return TaskListView(tasks=tasks, total=len(tasks))

    def cancel(self, task_id: str, *, request_id: str | None = None) -> CancelView:
        current = self.tasks[task_id]
        if current.state == "CANCELLED":
            return CancelView(task=current, cancelled_now=False)
        updated = current.model_copy(
            update={"state": "CANCELLED", "state_version": current.state_version + 1}
        )
        self.tasks[task_id] = updated
        return CancelView(task=updated, cancelled_now=True)

    def verification_report(self, task_id: str) -> VerificationReportView:
        task = self.tasks[task_id]
        return VerificationReportView(
            task_id=task_id,
            task_state=task.state,
            status="NOT_VERIFIED",
        )

    def resolve_approval(
        self,
        approval_id: str,
        *,
        request_id: str,
        approved: bool,
    ) -> ApprovalView:
        self.resolve_calls += 1
        return ApprovalView(
            approval_id=approval_id,
            task_id="task-approval",
            kind="HIGH_RISK_EXECUTION",
            status="APPROVED" if approved else "REJECTED",
            created_at=NOW,
            resolved_at=NOW,
        )


def _telegram_update(
    text: str,
    *,
    message_id: int = 10,
    user_id: int = 42,
    chat_id: int = 84,
) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=message_id,
        message_id=message_id,
        user_id=user_id,
        chat_id=chat_id,
        text=text,
    )


def test_duplicate_telegram_message_is_one_durable_task() -> None:
    client = FakeControlClient()
    adapter = TelegramClientAdapter(
        ExternalClientGateway(client),
        TelegramAllowlist(user_ids={42}, chat_ids={84}),
    )
    update = _telegram_update("/pao submit project-1 fix the deterministic test")

    first = adapter.handle_update(update)
    second = adapter.handle_update(update)

    assert first is not None and second is not None
    assert first.request_id == second.request_id
    assert first.task_id == second.task_id
    assert len(client.tasks) == 1
    assert client.submit_calls == 2  # retry reached the idempotent control-plane boundary


def test_telegram_and_deskpet_observe_the_same_task_state() -> None:
    client = FakeControlClient()
    gateway = ExternalClientGateway(client)
    telegram = TelegramClientAdapter(
        gateway,
        TelegramAllowlist(user_ids={42}, chat_ids={84}),
    )
    deskpet = DeskPetClientAdapter(gateway, installation_id="deskpet-local")
    submitted = telegram.handle_update(
        _telegram_update("/pao submit project-1 inspect shared state")
    )
    assert submitted is not None and submitted.task_id is not None

    deskpet_status = deskpet.call(
        DeskPetToolRequest(
            request_id="deskpet-status-1",
            operation=ClientOperation.STATUS,
            task_id=submitted.task_id,
        )
    )
    telegram_status = telegram.handle_update(
        _telegram_update(f"/pao status {submitted.task_id}", message_id=11)
    )

    assert telegram_status is not None
    assert deskpet_status.payload == telegram_status.payload
    assert deskpet_status.state == "SUBMITTED"


def test_unauthorized_telegram_sender_cannot_trigger_execution_or_state_change() -> None:
    client = FakeControlClient()
    adapter = TelegramClientAdapter(
        ExternalClientGateway(client),
        TelegramAllowlist(user_ids={42}, chat_ids={84}),
    )

    assert (
        adapter.handle_update(
            _telegram_update(
                "/pao submit project-1 should not run",
                user_id=999,
            )
        )
        is None
    )
    assert client.tasks == {}
    assert client.submit_calls == 0


def test_client_adapter_lifetime_does_not_own_worker_or_task_state() -> None:
    client = FakeControlClient()
    gateway = ExternalClientGateway(client)
    adapter = DeskPetClientAdapter(gateway, installation_id="deskpet-local")
    submitted = adapter.call(
        DeskPetToolRequest(
            request_id="deskpet-submit-1",
            operation=ClientOperation.SUBMIT,
            project_id="project-1",
            intent="long running worker",
        )
    )
    assert submitted.task_id is not None
    client.tasks[submitted.task_id] = client.tasks[submitted.task_id].model_copy(
        update={"state": "RUNNING", "state_version": 1}
    )

    del adapter

    assert client.get_task(submitted.task_id).state == "RUNNING"


def test_approve_is_a_fixed_owner_operation_not_arbitrary_state_mutation() -> None:
    client = FakeControlClient()
    deskpet = DeskPetClientAdapter(
        ExternalClientGateway(client),
        installation_id="deskpet-local",
    )

    result = deskpet.call(
        DeskPetToolRequest(
            request_id="deskpet-approval-1",
            operation=ClientOperation.APPROVE,
            approval_id="approval-1",
            approved=True,
        )
    )

    assert result.state == "APPROVED"
    assert client.resolve_calls == 1


def test_client_contract_has_no_shell_or_argv_escape_hatch() -> None:
    with pytest.raises(ValidationError):
        ExternalClientRequest.model_validate(
            {
                "client": ClientKind.DESKPET,
                "actor_id": "deskpet",
                "conversation_id": "deskpet",
                "request_id": "request-1",
                "operation": ClientOperation.SUBMIT,
                "project_id": "project-1",
                "intent": "ordinary natural language",
                "argv": ["/bin/sh", "-c", "unsafe"],
            }
        )


def test_notifier_emits_only_new_or_changed_durable_task_states() -> None:
    client = FakeControlClient()
    client.tasks["task-1"] = _task("task-1", "request-1")
    notifier = TaskStateNotifier(client)

    assert notifier.poll() == ()
    assert notifier.poll() == ()

    client.tasks["task-1"] = client.tasks["task-1"].model_copy(
        update={"state": "RUNNING", "state_version": 1}
    )
    changed = notifier.poll()
    assert [(item.previous_state, item.current_state) for item in changed] == [
        ("SUBMITTED", "RUNNING")
    ]

    client.tasks["task-2"] = _task("task-2", "request-2")
    added = notifier.poll()
    assert [(item.task_id, item.previous_state, item.current_state) for item in added] == [
        ("task-2", "UNSEEN", "SUBMITTED")
    ]


class CappedControlClient(FakeControlClient):
    """Use the real API's newest-first default/cap, without a socket or provider."""

    def __init__(self) -> None:
        super().__init__()
        self.limits: list[int | None] = []
        self.lookups: list[str] = []
        self.lookup_errors: dict[str, Exception] = {}
        self.list_error: Exception | None = None

    def list_tasks(self, *, limit: int | None = None) -> TaskListView:
        self.limits.append(limit)
        if self.list_error is not None:
            raise self.list_error
        bounded = min(MAX_LIST_LIMIT, DEFAULT_LIST_LIMIT if limit is None else limit)
        tasks = tuple(reversed(self.tasks.values()))[:bounded]
        return TaskListView(tasks=tasks, total=len(self.tasks))

    def get_task(self, task_id: str) -> TaskView:
        self.lookups.append(task_id)
        if task_id in self.lookup_errors:
            raise self.lookup_errors[task_id]
        if task_id not in self.tasks:
            raise ControlPlaneError(404, "task_not_found")
        return self.tasks[task_id]

    def add_tasks(self, start: int, stop: int, *, state: str = "SUBMITTED") -> None:
        for index in range(start, stop):
            task_id = f"task-{index}"
            self.tasks[task_id] = _task(task_id, f"request-{index}", state=state)

    def change_state(self, task_id: str, state: str) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = task.model_copy(
            update={"state": state, "state_version": task.state_version + 1}
        )


def test_notifier_discovers_beyond_default_fifty_row_page() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 75)
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()

    client.change_state("task-0", "RUNNING")
    changed = notifier.poll()

    assert [(item.task_id, item.previous_state, item.current_state) for item in changed] == [
        ("task-0", "SUBMITTED", "RUNNING")
    ]
    assert client.limits == [MAX_LIST_LIMIT, MAX_LIST_LIMIT]
    assert client.lookups == []
    assert notifier.poll() == ()


@pytest.mark.parametrize("terminal_state", ["FAILED", "CANCELLED", "COMPLETED"])
def test_notifier_tracks_old_task_after_more_than_two_hundred_new_tasks(
    terminal_state: str,
) -> None:
    client = CappedControlClient()
    client.add_tasks(0, 1, state="RUNNING")
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()

    client.add_tasks(1, 251, state="COMPLETED")
    notifier.poll()
    assert client.lookups == ["task-0"]
    client.change_state("task-0", terminal_state)
    changed = notifier.poll()

    assert [(item.task_id, item.previous_state, item.current_state) for item in changed] == [
        ("task-0", "RUNNING", terminal_state)
    ]
    assert changed[0].state_version == 1
    assert notifier.poll() == ()
    # Off-page terminal history is retired, so it is never polled repeatedly.
    assert client.lookups == ["task-0", "task-0"]
    assert len(notifier._states) == MAX_LIST_LIMIT


def test_notifier_bounds_and_rotates_lookups_for_over_two_hundred_unfinished_tasks() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 200, state="RUNNING")
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()
    client.add_tasks(200, 400, state="RUNNING")
    notifier.poll()
    assert len(notifier._states) == 400

    client.add_tasks(400, 600, state="COMPLETED")
    for index in range(400):
        client.change_state(f"task-{index}", "COMPLETED")

    completed = []
    for _ in range(2):
        client.lookups.clear()
        completed.extend(item for item in notifier.poll() if item.previous_state == "RUNNING")
        assert len(client.lookups) == MAX_LIST_LIMIT
        assert len(set(client.lookups)) == MAX_LIST_LIMIT

    assert len(completed) == 400
    assert {item.task_id for item in completed} == {f"task-{index}" for index in range(400)}
    assert {item.current_state for item in completed} == {"COMPLETED"}
    assert len(notifier._states) == MAX_LIST_LIMIT
    client.lookups.clear()
    assert notifier.poll() == ()
    assert client.lookups == []


@pytest.mark.parametrize(
    "error",
    [
        ControlPlaneUnavailable("fixture unavailable"),
        ControlPlaneError(503, "temporarily_unavailable"),
        ControlPlaneError(404, "unknown_endpoint"),
    ],
)
def test_notifier_retries_transient_lookup_without_losing_previous_state(error: Exception) -> None:
    client = CappedControlClient()
    client.add_tasks(0, 1, state="RUNNING")
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()
    client.add_tasks(1, 251, state="COMPLETED")
    notifier.poll()
    client.change_state("task-0", "COMPLETED")
    client.lookup_errors["task-0"] = error

    assert notifier.poll() == ()
    assert notifier._states["task-0"] == ("RUNNING", 0)

    client.lookup_errors.clear()
    changed = notifier.poll()
    assert [(item.task_id, item.previous_state, item.current_state) for item in changed] == [
        ("task-0", "RUNNING", "COMPLETED")
    ]
    assert notifier.poll() == ()


def test_notifier_missing_record_is_retired_without_inventing_terminal_state() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 2, state="RUNNING")
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()
    client.add_tasks(2, 252, state="COMPLETED")
    notifier.poll()
    del client.tasks["task-0"]
    client.change_state("task-1", "BLOCKED")
    client.lookups.clear()

    changed = notifier.poll()

    assert [(item.task_id, item.previous_state, item.current_state) for item in changed] == [
        ("task-1", "RUNNING", "BLOCKED")
    ]
    assert set(client.lookups) == {"task-0", "task-1"}
    assert "task-0" not in notifier._states
    client.lookups.clear()
    assert notifier.poll() == ()
    assert client.lookups == ["task-1"]  # BLOCKED is resumable, not terminal.


def test_notifier_failed_listing_keeps_baseline_for_recovery() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 1)
    notifier = TaskStateNotifier(client)
    client.list_error = ControlPlaneUnavailable("fixture unavailable")
    with pytest.raises(ControlPlaneUnavailable):
        notifier.poll()
    assert not notifier._primed
    client.list_error = None
    assert notifier.poll() == ()

    client.change_state("task-0", "RUNNING")
    client.list_error = ControlPlaneUnavailable("fixture unavailable")
    with pytest.raises(ControlPlaneUnavailable):
        notifier.poll()
    client.list_error = None
    changed = notifier.poll()
    assert [(item.previous_state, item.current_state) for item in changed] == [
        ("SUBMITTED", "RUNNING")
    ]
    assert notifier.poll() == ()


def test_notifier_version_only_changes_do_not_duplicate_state_notifications() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 1)
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()

    client.change_state("task-0", "SUBMITTED")
    assert notifier.poll() == ()
    client.change_state("task-0", "RUNNING")
    changed = notifier.poll()
    assert len(changed) == 1
    assert changed[0].state_version == 2
    assert notifier.poll() == ()


def test_notifier_does_not_recheck_terminal_tasks_after_leaving_discovery_page() -> None:
    client = CappedControlClient()
    client.add_tasks(0, 200, state="COMPLETED")
    notifier = TaskStateNotifier(client)
    assert notifier.poll() == ()

    for start in (200, 400, 600):
        client.add_tasks(start, start + 200, state="COMPLETED")
        assert len(notifier.poll()) == 200
        assert len(notifier._states) == MAX_LIST_LIMIT
        assert notifier.poll() == ()

    assert client.lookups == []
