"""Explicit manual recovery is identity-bound and never an execution request."""

import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator import client_cli
from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.safety_kernel import TaskState
from tests.test_local_client_v2 import _http
from tests.test_local_client_v2 import service as service
from tests.test_owner_dispatch_recovery import _recoverable


@pytest.fixture
def recoverable_service(service, tmp_path):
    service.store.close()
    root = tmp_path / "recovery"
    root.mkdir()
    service.store = _recoverable(root)
    return service


def recovery_request(service, operation="RECOVER", **changes):
    return {
        "protocol_version": 2,
        "operation": operation,
        "request_id": "recover-1",
        "task_id": "task",
        "task_state_version": service.store.get_task("task").state_version,
        "prior_dispatch_id": "dispatch",
        "expected_store_id": service.store.store_id,
        "expected_process_epoch": service.process_epoch,
    } | changes


def counts(service):
    return tuple(
        service.store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("runs", "owner_dispatches", "worker_attempts", "owner_dispatch_recoveries")
    )


def test_recover_requires_separate_start_and_returns_historical_receipt(recoverable_service):
    service = recoverable_service
    request = recovery_request(service)
    before = counts(service)
    reply = handle_local_client_v2(service, request)
    payload = reply["payload"]
    assert payload["current_task"]["state"] == "READY"
    assert payload["launch_performed"] is False
    assert payload["recovery"]["task_state_version"] == request["task_state_version"]
    assert payload["recovery"]["recovered_task_state_version"] == request["task_state_version"] + 1
    assert service.dispatch_executor.calls == []
    assert counts(service) == before[:-1] + (1,)
    assert handle_local_client_v2(service, request) == reply
    current = service.store.get_task("task")
    service.store.transition_task(
        "task", TaskState.CANCELLED, expected_version=current.state_version
    )
    replay = handle_local_client_v2(service, request)["payload"]
    assert replay["recovery"] == payload["recovery"]
    assert replay["current_task"]["state"] == "CANCELLED"
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize("field", ["expected_store_id", "expected_process_epoch"])
def test_recovery_identity_rejected_before_effect(recoverable_service, field):
    service = recoverable_service
    request = recovery_request(service, **{field: "0" * 32})
    before = counts(service), service.store.get_task("task"), service.store.audit_events("task")
    with pytest.raises(ControlPlaneError) as error:
        handle_local_client_v2(service, request)
    assert error.value.code in {"store_identity_mismatch", "process_epoch_mismatch"}
    assert (
        counts(service),
        service.store.get_task("task"),
        service.store.audit_events("task"),
    ) == before


def test_lost_recovery_reply_restart_uses_readonly_status(recoverable_service):
    service = recoverable_service
    request = recovery_request(service)
    original = handle_local_client_v2(service, request)["payload"]["recovery"]
    restarted = replace(service, process_epoch="a" * 32)
    before = counts(service), service.store.audit_events("task")
    with pytest.raises(ControlPlaneError, match="process_epoch_mismatch"):
        handle_local_client_v2(restarted, request)
    status = handle_local_client_v2(restarted, request | {"operation": "RECOVERY_STATUS"})
    assert status["payload"]["recovery"] == original
    assert status["process_epoch"] != status["payload"]["recovery"]["process_epoch"]
    assert (counts(service), service.store.audit_events("task")) == before
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "another-task"},
        {"task_state_version": 999},
        {"prior_dispatch_id": "other-dispatch"},
        {"expected_process_epoch": "b" * 32},
    ],
)
def test_recovery_status_checks_entire_original_tuple(recoverable_service, change):
    service = recoverable_service
    request = recovery_request(service)
    handle_local_client_v2(service, request)
    before = counts(service), service.store.audit_events("task")
    with pytest.raises(ControlPlaneError, match="conflicting_recovery_request_id"):
        handle_local_client_v2(service, request | {"operation": "RECOVERY_STATUS"} | change)
    assert (counts(service), service.store.audit_events("task")) == before


def test_recovery_requires_current_owner_authority_but_status_does_not(recoverable_service):
    service = recoverable_service
    request = recovery_request(service)
    service.owner_execution.set_enabled(False)
    with pytest.raises(ControlPlaneError, match="owner_initiated_execution_disabled"):
        handle_local_client_v2(service, request)
    assert service.store.get_task("task").state is TaskState.BLOCKED
    service.owner_execution.set_enabled(True)
    handle_local_client_v2(service, request)
    service.owner_execution.set_enabled(False)
    assert (
        handle_local_client_v2(service, request | {"operation": "RECOVERY_STATUS"})["payload"][
            "current_task"
        ]["state"]
        == "READY"
    )


def test_live_supervisor_blocks_new_recovery(recoverable_service):
    service = recoverable_service
    service.dispatch_executor.execution_supervisor = SimpleNamespace(get=lambda _task: object())
    with pytest.raises(ControlPlaneError, match="recovery_active_execution"):
        handle_local_client_v2(service, recovery_request(service))
    assert service.store.get_task("task").state is TaskState.BLOCKED
    assert (
        service.store.connection.execute(
            "SELECT count(*) FROM owner_dispatch_recoveries"
        ).fetchone()[0]
        == 0
    )


def test_recovery_denial_and_status_are_sanitized(recoverable_service):
    service = recoverable_service
    request = recovery_request(service)
    with pytest.raises(ControlPlaneError, match="recovery_not_found"):
        handle_local_client_v2(service, request | {"operation": "RECOVERY_STATUS"})
    service.store.connection.execute("UPDATE workspaces SET writer_token='PRIVATE_WRITER_SECRET'")
    with pytest.raises(ControlPlaneError) as error:
        handle_local_client_v2(service, request)
    assert error.value.code == "recovery_writer_held"
    assert "PRIVATE" not in str(error.value)
    service.store.connection.execute("UPDATE workspaces SET writer_token=NULL")
    result = handle_local_client_v2(service, request)
    text = json.dumps(result)
    assert "worktree_path" not in text and "writer_token" not in text
    assert "/workspace" not in text and "/tmp" not in text


@pytest.mark.parametrize("operation", ["RECOVER", "RECOVERY_STATUS"])
@pytest.mark.parametrize(
    "field", ["prior_dispatch_id", "expected_store_id", "expected_process_epoch"]
)
def test_recovery_shape_requires_every_identity(recoverable_service, operation, field):
    request = recovery_request(recoverable_service, operation)
    request.pop(field)
    with pytest.raises(ValidationError):
        handle_local_client_v2(recoverable_service, request)


def test_recovery_cannot_smuggle_launch_or_cleanup_authority(recoverable_service):
    request = recovery_request(recoverable_service, cleanup_confirmed=True)
    with pytest.raises(ValidationError):
        handle_local_client_v2(recoverable_service, request)


def test_recovery_http_and_cli_forward_once_without_launch(
    recoverable_service, monkeypatch, capsys
):
    service = recoverable_service
    request = recovery_request(service)
    code, response = _http(service, request)
    assert code == 200 and response["payload"]["launch_performed"] is False
    calls = []

    def downstream(_client, method, path, *, payload=None):
        calls.append((method, path))
        return handle_local_client_v2(service, payload)

    monkeypatch.setattr(ControlPlaneClient, "_request", downstream)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 0
    assert json.loads(capsys.readouterr().out) == response
    assert calls == [("POST", "/v2/local-client")]
    assert service.dispatch_executor.calls == []
    assert counts(service)[-1] == 1


def test_http_recovery_checks_current_owner_before_creating_receipt(recoverable_service):
    service = recoverable_service
    request = recovery_request(service)
    service.owner_execution.set_enabled(False)
    code, response = _http(service, request)
    assert code == 403 and response["error"] == "owner_initiated_execution_disabled"
    assert service.store.get_task("task").state is TaskState.BLOCKED
    assert counts(service)[-1] == 0 and service.dispatch_executor.calls == []
