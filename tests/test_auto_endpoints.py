"""M1 WP5a-2 §28 — the supervised-auto owner control endpoints.

Covered over the real UDS HTTP surface (sanitize + status codes) and the
service layer (idempotence, deadline pinning, metadata clearing):

- ACK: success / stale version / wrong state / duplicate idempotent /
  retry does not extend the deadline.
- VETO: success / stale version / duplicate replay / clears metadata +
  locks the task policy to MANUAL + discards the pending shadow.
- DISPATCH-NOW: success / stale version / wrong mode / frozen-decision
  guard / duplicate does not launch twice.
- 404 unknown task, 400 malformed payload, 409 lifecycle violations.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneServer,
)
from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.supervised_auto_step import (
    supervised_auto_dispatch_request_id,
)
from tests.test_supervised_auto_step import NOW, _env, _plan


def _http_client(env):
    import tempfile

    from personal_ai_orchestrator.control_client import ControlPlaneClient

    socket_path = Path(tempfile.mkdtemp(prefix="pao-auto-ep-")) / "control.sock"
    server = ControlPlaneServer(env.service, socket_path)
    server.start_background()
    return ControlPlaneClient(socket_path), server, socket_path


@pytest.fixture()
def planned(tmp_path: Path):
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    return env


# ---------------------------------------------------------------------------
# ACK
# ---------------------------------------------------------------------------


def test_ack_success_sets_acked_at_and_deadline(planned) -> None:
    task = planned.store.get_task("task-1")
    _client, server, _path = _http_client(planned)
    try:
        body, status = _raw_post(
            _path,
            "/v1/tasks/task-1/auto/ack",
            {"task_state_version": task.state_version},
        )
        assert status == 200
        assert body["state"] == "AUTO_GRACE"
        assert body["auto_acked_at"] is not None
        assert body["auto_grace_deadline_at"] is not None
    finally:
        server.stop()


def test_ack_stale_version_is_409(planned) -> None:
    task = planned.store.get_task("task-1")
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_ack("task-1", {"task_state_version": task.state_version + 5})
    assert excinfo.value.status == 409
    assert excinfo.value.code == "stale_task_state_version"


def test_ack_wrong_state_is_409(planned) -> None:
    task = planned.store.get_task("task-1")
    planned.service.auto_ack("task-1", {"task_state_version": task.state_version})
    # Veto with the CURRENT (post-ack) version → task returns to READY.
    current = planned.store.get_task("task-1")
    planned.service.auto_veto(
        "task-1",
        {"request_id": "veto-1", "task_state_version": current.state_version},
    )
    assert planned.store.get_task("task-1").state is TaskState.READY
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_ack("task-1", {"task_state_version": 0})
    assert excinfo.value.status == 409
    assert excinfo.value.code == "task_state_not_auto_grace"


def test_ack_duplicate_is_idempotent(planned) -> None:
    task = planned.store.get_task("task-1")
    first = planned.service.auto_ack("task-1", {"task_state_version": task.state_version})
    # Retry with the STALE original version: already-acked → idempotent.
    second = planned.service.auto_ack("task-1", {"task_state_version": task.state_version})
    assert second.auto_acked_at == first.auto_acked_at
    assert second.auto_grace_deadline_at == first.auto_grace_deadline_at


def test_ack_retry_does_not_extend_deadline(planned) -> None:
    task = planned.store.get_task("task-1")
    first = planned.service.auto_ack("task-1", {"task_state_version": task.state_version})
    deadline = first.auto_grace_deadline_at
    assert deadline is not None
    # A retry via the CURRENT version also keeps the original deadline.
    current = planned.store.get_task("task-1")
    second = planned.service.auto_ack("task-1", {"task_state_version": current.state_version})
    assert second.auto_grace_deadline_at == deadline
    assert planned.store.get_task("task-1").auto_grace_deadline_at == deadline


# ---------------------------------------------------------------------------
# VETO
# ---------------------------------------------------------------------------


def test_veto_success_returns_ready_and_clears_everything(planned) -> None:
    task = planned.store.get_task("task-1")
    assert planned.shadow.load_pending_all()
    view = planned.service.auto_veto(
        "task-1", {"request_id": "veto-1", "task_state_version": task.state_version}
    )
    assert view.state == "READY"
    assert view.auto_decision_id is None
    assert view.auto_grace_deadline_at is None
    assert view.auto_acked_at is None
    assert view.auto_reason is None
    # Task policy locked MANUAL → the next tick must not re-plan.
    row = planned.store.connection.execute(
        "SELECT scheduling_policy FROM tasks WHERE task_id='task-1'"
    ).fetchone()
    assert row["scheduling_policy"] == "MANUAL"
    planned.tick(NOW + timedelta(seconds=5))
    assert planned.store.get_task("task-1").state is TaskState.READY
    assert planned.shadow.load_pending_all() == ()
    vetoed = [e for e in planned.store.audit_events("task-1") if e["event_type"] == "AUTO_VETOED"]
    assert len(vetoed) == 1
    assert vetoed[0]["payload"]["request_id"] == "veto-1"


def test_veto_stale_version_is_409(planned) -> None:
    task = planned.store.get_task("task-1")
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_veto(
            "task-1",
            {"request_id": "veto-1", "task_state_version": task.state_version + 3},
        )
    assert excinfo.value.status == 409
    assert excinfo.value.code == "stale_task_state_version"
    assert planned.store.get_task("task-1").state is TaskState.AUTO_GRACE


def test_veto_duplicate_replays_idempotently(planned) -> None:
    task = planned.store.get_task("task-1")
    first = planned.service.auto_veto(
        "task-1", {"request_id": "veto-1", "task_state_version": task.state_version}
    )
    assert first.state == "READY"
    # Same request id replayed → 200 with the current view, no second
    # audit row.
    second = planned.service.auto_veto(
        "task-1", {"request_id": "veto-1", "task_state_version": first.state_version}
    )
    assert second.state == "READY"
    vetoed = [e for e in planned.store.audit_events("task-1") if e["event_type"] == "AUTO_VETOED"]
    assert len(vetoed) == 1


def test_cancel_on_auto_task_behaves_as_veto(planned) -> None:
    view = planned.service.cancel_task("task-1", {"request_id": "cancel-auto-1"})
    assert view.cancelled_now is True
    assert view.task.state == "READY"
    assert view.task.auto_decision_id is None
    row = planned.store.connection.execute(
        "SELECT scheduling_policy FROM tasks WHERE task_id='task-1'"
    ).fetchone()
    assert row["scheduling_policy"] == "MANUAL"
    vetoed = [e for e in planned.store.audit_events("task-1") if e["event_type"] == "AUTO_VETOED"]
    assert any(e["payload"]["reason"] == "owner_cancel" for e in vetoed)
    assert planned.shadow.load_pending_all() == ()


# ---------------------------------------------------------------------------
# DISPATCH-NOW
# ---------------------------------------------------------------------------


def _dispatch_now(env, task_version: int):
    return env.service.auto_dispatch_now("task-1", {"task_state_version": task_version})


def test_dispatch_now_success_reserves_and_audits(planned) -> None:
    task = planned.store.get_task("task-1")
    planned.service.auto_ack("task-1", {"task_state_version": task.state_version})
    current = planned.store.get_task("task-1")
    view = _dispatch_now(planned, current.state_version)
    assert view.authority == "SUPERVISED_AUTO"
    assert view.execution_target_id == "m3-sub"
    assert view.status == "RESERVED"
    dispatched = [
        e for e in planned.store.audit_events("task-1") if e["event_type"] == "AUTO_DISPATCHED"
    ]
    assert len(dispatched) == 1
    assert dispatched[0]["payload"]["trigger"] == "dispatch_now"
    assert planned.executor is not None
    assert len(planned.executor.unique_calls) == 1


def test_dispatch_now_stale_version_is_409(planned) -> None:
    task = planned.store.get_task("task-1")
    with pytest.raises(ControlPlaneError) as excinfo:
        _dispatch_now(planned, task.state_version + 9)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "stale_task_state_version"
    assert planned.executor is not None and planned.executor.unique_calls == []


def test_dispatch_now_wrong_mode_is_409(planned) -> None:
    # Flip the persisted mode directly (no handler abort) so the task
    # still sits in AUTO_GRACE — proving the endpoint re-checks the mode
    # on the durable state, not just on the task row.
    planned.settings.set_mode("MANUAL")
    task = planned.store.get_task("task-1")
    assert task.state is TaskState.AUTO_GRACE
    with pytest.raises(ControlPlaneError) as excinfo:
        _dispatch_now(planned, task.state_version)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "scheduling_mode_not_supervised_auto"
    assert planned.executor is not None and planned.executor.unique_calls == []


def test_dispatch_now_missing_frozen_decision_is_409(planned) -> None:
    task = planned.store.get_task("task-1")
    # Blow the frozen decision away while keeping the AUTO_GRACE state.
    planned.store.connection.execute("DELETE FROM routing_decisions WHERE task_id='task-1'")
    with pytest.raises(ControlPlaneError) as excinfo:
        _dispatch_now(planned, task.state_version)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "frozen_decision_missing"


def test_dispatch_now_duplicate_does_not_launch_twice(planned) -> None:
    task = planned.store.get_task("task-1")
    first = _dispatch_now(planned, task.state_version)
    assert first.status == "RESERVED"
    # The reservation does not bump the task version — a duplicate call
    # with the same version hits the idempotent reservation and returns
    # the existing record without a second worker thread.
    current = planned.store.get_task("task-1")
    second = _dispatch_now(planned, current.state_version)
    assert second.dispatch_id == first.dispatch_id
    assert second.status == "RESERVED"
    assert planned.executor is not None
    assert len(planned.executor.unique_calls) == 1
    rows = planned.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches WHERE request_id=?",
        (supervised_auto_dispatch_request_id(task.auto_decision_id),),
    ).fetchone()
    assert rows["n"] == 1


def test_dispatch_now_requires_auto_grace_state(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    task = env.store.get_task("task-1")  # READY, never planned
    with pytest.raises(ControlPlaneError) as excinfo:
        _dispatch_now(env, task.state_version)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "task_state_not_auto_grace"


# ---------------------------------------------------------------------------
# wire-level status codes
# ---------------------------------------------------------------------------


def test_endpoints_404_unknown_task(planned) -> None:
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_ack("task-does-not-exist", {"task_state_version": 0})
    assert excinfo.value.status == 404
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_veto(
            "task-does-not-exist",
            {"request_id": "v1", "task_state_version": 0},
        )
    assert excinfo.value.status == 404
    with pytest.raises(ControlPlaneError) as excinfo:
        planned.service.auto_dispatch_now("task-does-not-exist", {"task_state_version": 0})
    assert excinfo.value.status == 404


def test_endpoints_400_malformed_payload(planned) -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        planned.service.auto_ack("task-1", {})
    with pytest.raises(pydantic.ValidationError):
        planned.service.auto_veto("task-1", {"task_state_version": 0})


def test_endpoints_http_wire(planned) -> None:
    _client, server, _path = _http_client(planned)
    try:
        task = planned.store.get_task("task-1")
        # 400: malformed body over the wire.
        body, status = _raw_post(_path, "/v1/tasks/task-1/auto/ack", {"unexpected": True})
        assert status == 400
        assert body["error"] == "invalid_json_schema"
        # 404: unknown task.
        body, status = _raw_post(
            _path,
            "/v1/tasks/task-404/auto/ack",
            {"task_state_version": 0},
        )
        assert status == 404
        # 200: happy path.
        body, status = _raw_post(
            _path,
            "/v1/tasks/task-1/auto/ack",
            {"task_state_version": task.state_version},
        )
        assert status == 200
        assert body["auto_acked_at"] is not None
    finally:
        server.stop()


def _raw_post(socket_path, payload_path: str, payload: dict):
    """POST over a raw UDS socket, returning (json, status)."""

    import socket as _socket

    rendered = json.dumps(payload)
    request = (
        f"POST {payload_path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(rendered)}\r\n"
        "\r\n"
        f"{rendered}"
    )
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(str(socket_path))
    try:
        sock.sendall(request.encode("utf-8"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += sock.recv(4096)
        head, _, rest = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ")[1])
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        body = rest
        while len(body) < length:
            body += sock.recv(4096)
        return json.loads(body.decode("utf-8")), status
    finally:
        sock.close()
