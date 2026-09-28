from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.pi5_broker import (
    DelegationBrokerContext,
    DelegationBrokerSession,
    DelegationBrokerStatus,
    DelegationChildPlan,
    DelegationChildResult,
    UnixDelegationBrokerServer,
    delegation_child_task_id,
)
from personal_ai_orchestrator.pi5_contract import DelegationRequest


class FakeChildPort:
    def __init__(self, *, final_state: str = "VERIFIED", verified: bool = True) -> None:
        self.plans: list[DelegationChildPlan] = []
        self.final_state = final_state
        self.verified = verified

    async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
        self.plans.append(plan)
        return DelegationChildResult(
            child_task_id=plan.child_task_id,
            final_state=self.final_state,
            selected_execution_target_id="host-selected-target",
            verified=self.verified,
            summary="sanitized child result",
        )


def _context(**overrides: object) -> DelegationBrokerContext:
    values: dict[str, object] = {
        "parent_task_id": "parent-task",
        "parent_run_id": "parent-run",
        "project_id": "project-1",
        "base_sha": "abc123",
        "working_subpath": None,
        "parent_depth": 0,
        "max_children": 3,
    }
    values.update(overrides)
    return DelegationBrokerContext(**values)  # type: ignore[arg-type]


def _request(ordinal: int, *, tool_call_id: str | None = None) -> DelegationRequest:
    return DelegationRequest(
        tool_call_id=tool_call_id or f"call-{ordinal}",
        ordinal=ordinal,
        intent=f"Inspect isolated concern {ordinal}",
        reason="Independent bounded analysis is useful",
    )


def test_wire_request_cannot_choose_parent_target_provider_or_runtime() -> None:
    base = {
        "schema_version": 1,
        "tool_call_id": "call-1",
        "ordinal": 1,
        "intent": "Inspect parser",
        "reason": "Need review",
    }
    for forbidden in (
        "parent_task_id",
        "parent_run_id",
        "child_task_id",
        "provider_id",
        "model_sku_id",
        "execution_target_id",
        "runtime_id",
        "worktree_path",
    ):
        with pytest.raises(ValidationError):
            DelegationRequest.model_validate({**base, forbidden: "attacker-choice"})


def test_context_rejects_recursive_parent_and_excess_budget() -> None:
    with pytest.raises(ValueError, match="depth-0"):
        _context(parent_depth=1)
    with pytest.raises(ValueError, match="max_children"):
        _context(max_children=4)


@pytest.mark.asyncio
async def test_broker_mints_identity_and_disables_child_delegation() -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(), child_port=port)

    response = await session.handle(_request(1))

    assert response.status is DelegationBrokerStatus.COMPLETED
    assert response.verified is True
    assert response.child_state == "VERIFIED"
    assert response.selected_execution_target_id == "host-selected-target"
    assert len(port.plans) == 1
    plan = port.plans[0]
    assert plan.parent_task_id == "parent-task"
    assert plan.parent_run_id == "parent-run"
    assert plan.child_task_id == delegation_child_task_id(
        parent_task_id="parent-task",
        parent_run_id="parent-run",
        ordinal=1,
    )
    assert plan.depth == 1
    assert plan.delegation_allowed is False
    assert not hasattr(plan, "execution_target_id")
    assert not hasattr(plan, "provider_id")
    assert not hasattr(plan, "runtime_id")


@pytest.mark.asyncio
async def test_broker_context_budget_rejects_next_schema_valid_request() -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(max_children=2), child_port=port)

    responses = [await session.handle(_request(index)) for index in range(1, 4)]

    assert [item.status for item in responses[:2]] == [
        DelegationBrokerStatus.COMPLETED,
        DelegationBrokerStatus.COMPLETED,
    ]
    assert responses[2].status is DelegationBrokerStatus.REJECTED
    assert responses[2].reason_code == "DELEGATION_BUDGET_EXHAUSTED"
    assert len(port.plans) == 2


@pytest.mark.asyncio
async def test_identical_request_is_idempotent_but_conflict_fails_closed() -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(), child_port=port)
    request = _request(1)

    first = await session.handle(request)
    replay = await session.handle(request)
    conflict = await session.handle(
        DelegationRequest(
            tool_call_id=request.tool_call_id,
            ordinal=1,
            intent="Different intent",
            reason=request.reason,
        )
    )

    assert replay == first
    assert len(port.plans) == 1
    assert conflict.status is DelegationBrokerStatus.REJECTED
    assert conflict.reason_code == "TOOL_CALL_ID_CONFLICT"


@pytest.mark.asyncio
async def test_out_of_order_request_does_not_consume_budget() -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(), child_port=port)

    rejected = await session.handle(_request(2))
    accepted = await session.handle(_request(1))

    assert rejected.status is DelegationBrokerStatus.REJECTED
    assert rejected.reason_code == "DELEGATION_ORDINAL_MISMATCH"
    assert accepted.status is DelegationBrokerStatus.COMPLETED
    assert len(port.plans) == 1


@pytest.mark.asyncio
async def test_child_identity_mismatch_never_surfaces_as_completed() -> None:
    class WrongIdentityPort:
        async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
            return DelegationChildResult(
                child_task_id="wrong-child",
                final_state="VERIFIED",
                selected_execution_target_id="some-target",
                verified=True,
            )

    session = DelegationBrokerSession(context=_context(), child_port=WrongIdentityPort())
    response = await session.handle(_request(1))

    assert response.status is DelegationBrokerStatus.ERROR
    assert response.reason_code == "CHILD_IDENTITY_MISMATCH"
    assert response.verified is False
    assert response.child_task_id is None


@pytest.mark.asyncio
async def test_secret_shaped_child_result_is_replaced_by_generic_error() -> None:
    class UnsafePort:
        async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
            return DelegationChildResult(
                child_task_id=plan.child_task_id,
                final_state="BLOCKED",
                verified=False,
                summary="Authorization: Bearer secret-value-12345",
            )

    session = DelegationBrokerSession(context=_context(), child_port=UnsafePort())
    response = await session.handle(_request(1))

    assert response.status is DelegationBrokerStatus.ERROR
    assert response.reason_code == "CHILD_EXECUTION_ERROR"
    assert "secret" not in response.model_dump_json().lower()


@pytest.mark.asyncio
async def test_unix_broker_is_parent_bound_private_and_removes_socket(
    tmp_path: Path,
) -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(), child_port=port)
    socket_path = tmp_path / "private" / "delegate.sock"
    server = UnixDelegationBrokerServer(socket_path=socket_path, session=session)
    await server.start()
    try:
        mode = socket_path.stat().st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700

        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        wire = _request(1).model_dump(mode="json")
        writer.write(json.dumps(wire).encode() + b"\n")
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        writer.close()
        await writer.wait_closed()

        assert response["status"] == "COMPLETED"
        assert response["verified"] is True
        assert response["child_task_id"].startswith("pi5-child-")
        assert len(port.plans) == 1
    finally:
        await server.stop()

    assert socket_path.exists() is False


@pytest.mark.asyncio
async def test_unix_broker_rejects_worker_supplied_parent_identity(
    tmp_path: Path,
) -> None:
    port = FakeChildPort()
    session = DelegationBrokerSession(context=_context(), child_port=port)
    socket_path = tmp_path / "private" / "delegate.sock"
    server = UnixDelegationBrokerServer(socket_path=socket_path, session=session)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        wire = _request(1).model_dump(mode="json")
        wire["parent_task_id"] = "spoofed-parent"
        writer.write(json.dumps(wire).encode() + b"\n")
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        writer.close()
        await writer.wait_closed()

        assert response["status"] == "REJECTED"
        assert response["reason_code"] == "INVALID_REQUEST"
        assert port.plans == []
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_broker_refuses_to_unlink_preexisting_non_socket(tmp_path: Path) -> None:
    path = tmp_path / "private" / "delegate.sock"
    path.parent.mkdir(parents=True)
    path.write_text("owner file", encoding="utf-8")
    session = DelegationBrokerSession(context=_context(), child_port=FakeChildPort())
    server = UnixDelegationBrokerServer(socket_path=path, session=session)

    with pytest.raises(FileExistsError):
        await server.start()
    assert path.read_text(encoding="utf-8") == "owner file"
    assert os.path.isfile(path)
