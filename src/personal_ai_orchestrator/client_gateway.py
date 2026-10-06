"""Typed Telegram and DeskPet client boundary for the durable control plane.

The adapters in this module deliberately do not own execution.  They translate
an authenticated client message into one of five fixed control-plane
operations and return sanitized view models.  There is no field for argv,
shell, verifier commands, provider credentials, or execution-target authority.

Telegram transport/authentication remains provider-native: a bot host supplies
already-normalized updates after validating its bot token.  PAO only enforces
the owner-configured user/chat allowlists and talks to the 0600 Unix socket.
That keeps Telegram secrets out of repository files, URLs, logs, and PAO state.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.control_api import (
    MAX_LIST_LIMIT,
    ApprovalView,
    CancelView,
    ControlPlaneError,
    TaskListView,
    TaskView,
    VerificationReportView,
)
from personal_ai_orchestrator.control_client import ControlPlaneUnavailable


class ClientKind(StrEnum):
    TELEGRAM = "TELEGRAM"
    DESKPET = "DESKPET"


class ClientOperation(StrEnum):
    SUBMIT = "SUBMIT"
    STATUS = "STATUS"
    CANCEL = "CANCEL"
    APPROVE = "APPROVE"
    REPORT = "REPORT"


class ExternalClientRequest(BaseModel):
    """One strict client operation; unrelated authority fields are forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client: ClientKind
    actor_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    operation: ClientOperation
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    intent: str | None = Field(default=None, min_length=1, max_length=16_384)
    approval_id: str | None = Field(default=None, min_length=1, max_length=128)
    approved: bool | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> ExternalClientRequest:
        if self.operation is ClientOperation.SUBMIT:
            if self.project_id is None or self.intent is None:
                raise ValueError("submit_requires_project_and_intent")
            if self.approval_id is not None or self.approved is not None:
                raise ValueError("submit_cannot_resolve_approval")
            return self
        if self.operation in {
            ClientOperation.STATUS,
            ClientOperation.CANCEL,
            ClientOperation.REPORT,
        }:
            if self.task_id is None:
                raise ValueError("task_operation_requires_task_id")
            if any(
                value is not None
                for value in (self.project_id, self.intent, self.approval_id, self.approved)
            ):
                raise ValueError("task_operation_contains_unexpected_fields")
            return self
        if self.operation is ClientOperation.APPROVE:
            if self.approval_id is None or self.approved is None:
                raise ValueError("approval_operation_requires_resolution")
            if any(value is not None for value in (self.task_id, self.project_id, self.intent)):
                raise ValueError("approval_operation_contains_unexpected_fields")
            return self
        raise ValueError("unsupported_client_operation")


class ExternalClientResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    operation: ClientOperation
    task_id: str | None = None
    state: str | None = None
    summary: str
    payload: dict[str, Any]


class TaskStateNotification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    previous_state: str
    current_state: str
    state_version: int


class TaskControlClient(Protocol):
    """The fixed subset exposed to external clients."""

    def submit(
        self,
        *,
        task_id: str,
        request_id: str,
        project_id: str,
        intent: str,
        scheduling_policy: str | None = None,
        manual_execution_target_id: str | None = None,
    ) -> TaskView: ...

    def get_task(self, task_id: str) -> TaskView: ...

    def list_tasks(self, *, limit: int | None = None) -> TaskListView: ...

    def cancel(self, task_id: str, *, request_id: str | None = None) -> CancelView: ...

    def verification_report(self, task_id: str) -> VerificationReportView: ...

    def resolve_approval(
        self,
        approval_id: str,
        *,
        request_id: str,
        approved: bool,
    ) -> ApprovalView: ...


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


class ExternalClientGateway:
    """Map typed client requests onto the one authoritative task store."""

    def __init__(self, client: TaskControlClient) -> None:
        self.client = client

    def handle(self, request: ExternalClientRequest) -> ExternalClientResult:
        if request.operation is ClientOperation.SUBMIT:
            task_id = request.task_id or _stable_id(
                request.client.value.lower(),
                request.conversation_id,
                request.request_id,
            )
            task = self.client.submit(
                task_id=task_id,
                request_id=request.request_id,
                project_id=request.project_id or "",
                intent=request.intent or "",
            )
            return self._task_result(request, task, "task submitted")
        if request.operation is ClientOperation.STATUS:
            task = self.client.get_task(request.task_id or "")
            return self._task_result(request, task, "task status")
        if request.operation is ClientOperation.CANCEL:
            cancelled = self.client.cancel(
                request.task_id or "",
                request_id=request.request_id,
            )
            if cancelled.task.state == "READY":
                summary = "automatic execution vetoed; task remains ready for manual handling"
            elif cancelled.task.state == "CANCELLED":
                summary = "task cancelled" if cancelled.cancelled_now else "task already cancelled"
            else:
                # A replay returns current truth, including a later execution
                # or terminal outcome. It must not label that task cancelled.
                summary = f"cancellation request handled; task state is {cancelled.task.state}"
            return self._task_result(request, cancelled.task, summary)
        if request.operation is ClientOperation.REPORT:
            report = self.client.verification_report(request.task_id or "")
            return ExternalClientResult(
                request_id=request.request_id,
                operation=request.operation,
                task_id=report.task_id,
                state=report.task_state,
                summary=f"verification {report.status}",
                payload=report.model_dump(mode="json"),
            )
        approval = self.client.resolve_approval(
            request.approval_id or "",
            request_id=request.request_id,
            approved=bool(request.approved),
        )
        return ExternalClientResult(
            request_id=request.request_id,
            operation=request.operation,
            task_id=approval.task_id,
            state=approval.status,
            summary=f"approval {approval.status.lower()}",
            payload=approval.model_dump(mode="json"),
        )

    @staticmethod
    def _task_result(
        request: ExternalClientRequest,
        task: TaskView,
        summary: str,
    ) -> ExternalClientResult:
        return ExternalClientResult(
            request_id=request.request_id,
            operation=request.operation,
            task_id=task.task_id,
            state=task.state,
            summary=summary,
            payload=task.model_dump(mode="json"),
        )


class TelegramUpdate(BaseModel):
    """Sanitized update supplied by a provider-native Telegram bot host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    update_id: int
    message_id: int
    user_id: int
    chat_id: int
    text: str = Field(min_length=1, max_length=16_384)


class TelegramAllowlist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_ids: frozenset[int] = frozenset()
    chat_ids: frozenset[int] = frozenset()

    def permits(self, update: TelegramUpdate) -> bool:
        return update.user_id in self.user_ids and update.chat_id in self.chat_ids


class TelegramCommandParser:
    """Parse only `/pao` operations; free text is never interpreted as shell."""

    @staticmethod
    def parse(update: TelegramUpdate) -> ExternalClientRequest:
        parts = update.text.strip().split(maxsplit=3)
        if len(parts) < 2 or parts[0].lower() != "/pao":
            raise ValueError("unsupported_telegram_command")
        command = parts[1].lower()
        request_id = _stable_id("telegram", str(update.chat_id), str(update.message_id))
        common = {
            "client": ClientKind.TELEGRAM,
            "actor_id": str(update.user_id),
            "conversation_id": str(update.chat_id),
            "request_id": request_id,
        }
        if command == "submit" and len(parts) == 4:
            return ExternalClientRequest(
                **common,
                operation=ClientOperation.SUBMIT,
                project_id=parts[2],
                intent=parts[3],
            )
        if command in {"status", "cancel", "report"} and len(parts) == 3:
            return ExternalClientRequest(
                **common,
                operation=ClientOperation(command.upper()),
                task_id=parts[2],
            )
        if command in {"approve", "reject"} and len(parts) == 3:
            return ExternalClientRequest(
                **common,
                operation=ClientOperation.APPROVE,
                approval_id=parts[2],
                approved=command == "approve",
            )
        raise ValueError("invalid_telegram_command")


class TelegramClientAdapter:
    def __init__(self, gateway: ExternalClientGateway, allowlist: TelegramAllowlist) -> None:
        self.gateway = gateway
        self.allowlist = allowlist

    def handle_update(self, update: TelegramUpdate) -> ExternalClientResult | None:
        if not self.allowlist.permits(update):
            return None
        return self.gateway.handle(TelegramCommandParser.parse(update))


class DeskPetToolRequest(BaseModel):
    """Local DeskPet tool call using the same operation contract as Telegram."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    operation: ClientOperation
    task_id: str | None = None
    project_id: str | None = None
    intent: str | None = None
    approval_id: str | None = None
    approved: bool | None = None


class DeskPetClientAdapter:
    def __init__(self, gateway: ExternalClientGateway, *, installation_id: str) -> None:
        if not installation_id:
            raise ValueError("installation_id_required")
        self.gateway = gateway
        self.installation_id = installation_id

    def call(self, call: DeskPetToolRequest) -> ExternalClientResult:
        return self.gateway.handle(
            ExternalClientRequest(
                client=ClientKind.DESKPET,
                actor_id=self.installation_id,
                conversation_id=self.installation_id,
                **call.model_dump(),
            )
        )


class TaskStateNotifier:
    """Detect observed durable state changes without owning worker lifetime.

    The list endpoint has no pagination: only its newest ``MAX_LIST_LIMIT``
    tasks can be discovered. Previously seen unfinished tasks are retained
    and checked by ID after leaving that page. At most ``MAX_LIST_LIMIT``
    off-page lookups run per poll, rotating fairly even when lookups fail.
    Memory is bounded by unfinished tasks plus the current discovery page;
    terminal history is not retained forever. Tasks never seen in a page
    and intermediate transitions between polls cannot be reported.
    """

    _TERMINAL_STATES = frozenset({"FAILED", "CANCELLED", "COMPLETED"})

    def __init__(self, client: TaskControlClient) -> None:
        self.client = client
        self._states: dict[str, tuple[str, int]] = {}
        self._primed = False

    def poll(self) -> tuple[TaskStateNotification, ...]:
        tasks = self.client.list_tasks(limit=MAX_LIST_LIMIT).tasks
        current = {task.task_id: (task.state, task.state_version) for task in tasks}
        if not self._primed:
            self._states = current
            self._primed = True
            return ()

        # Absence from a capped page is not evidence of deletion or completion.
        # Preserve last-observed state until a lookup supplies fresh evidence.
        pending = {
            task_id: state
            for task_id, state in self._states.items()
            if task_id not in current and state[0] not in self._TERMINAL_STATES
        }
        for task_id in tuple(pending)[:MAX_LIST_LIMIT]:
            previous = pending.pop(task_id)
            try:
                task = self.client.get_task(task_id)
            except ControlPlaneError as error:
                if error.status == 404 and error.code == "task_not_found":
                    # A missing record has no observed terminal transition.
                    continue
                pending[task_id] = previous
            except ControlPlaneUnavailable:
                pending[task_id] = previous
            else:
                current[task_id] = (task.state, task.state_version)

        notifications = tuple(
            TaskStateNotification(
                task_id=task_id,
                previous_state=self._states.get(task_id, ("UNSEEN", 0))[0],
                current_state=state,
                state_version=version,
            )
            for task_id, (state, version) in sorted(current.items())
            if task_id not in self._states or self._states[task_id][0] != state
        )
        # Unchecked/temporarily unavailable IDs stay ahead of successful
        # lookups on the next poll. Keep terminal states only while on-page.
        page_ids = {task.task_id for task in tasks}
        self._states = pending | {
            task_id: state
            for task_id, state in current.items()
            if task_id in page_ids or state[0] not in self._TERMINAL_STATES
        }
        return notifications


__all__ = [
    "ClientKind",
    "ClientOperation",
    "DeskPetClientAdapter",
    "DeskPetToolRequest",
    "ExternalClientGateway",
    "ExternalClientRequest",
    "ExternalClientResult",
    "TaskStateNotification",
    "TaskStateNotifier",
    "TelegramAllowlist",
    "TelegramClientAdapter",
    "TelegramCommandParser",
    "TelegramUpdate",
]
