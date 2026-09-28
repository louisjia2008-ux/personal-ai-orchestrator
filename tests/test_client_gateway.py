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
    ApprovalView,
    CancelView,
    TaskListView,
    TaskView,
    VerificationReportView,
)

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
