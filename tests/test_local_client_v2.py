"""Manual local-client v2: explicit authority, identity, safe views and no retry."""

from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator import client_cli
from personal_ai_orchestrator.control_api import ControlPlaneError, handler_for_control
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.local_client_v2 import (
    CAPABILITIES,
    CATALOG_LIMIT,
    DETAIL_LIMIT,
    handle_local_client_v2,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_cleanup_quarantine import _claim, _observe, _reserved, _start
from tests.test_dispatch_initiator import _build_service, _register_project


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()

    def execute(self, request_id):
        self.calls.append(request_id)
        self.started.set()


@pytest.fixture
def service(tmp_path):
    service = _build_service(tmp_path)
    _register_project(service, tmp_path)
    service.dispatch_executor = RecordingExecutor()
    try:
        yield service
    finally:
        service.store.close()


def request(service, operation, **fields):
    payload = {"protocol_version": 2, "operation": operation, "request_id": "read-1"}
    if operation in {"SUBMIT_V2", "START", "CANCEL", "DISPATCH_STATUS"}:
        payload.update(
            expected_store_id=service.store.store_id,
            expected_process_epoch=service.process_epoch,
        )
    return payload | fields


def submit(service, **fields):
    return handle_local_client_v2(
        service,
        request(
            service,
            "SUBMIT_V2",
            task_id="task-1",
            request_id="submit-1",
            project_id="project-fixture",
            intent="implement a fixture; not an executable command",
            execution_target_id="m3-sub",
        )
        | fields,
    )


def start_request(service):
    return request(
        service,
        "START",
        task_id="task-1",
        request_id="start-1",
        task_state_version=0,
        execution_target_id="m3-sub",
    )


def counts(service):
    return tuple(
        service.store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tasks", "owner_dispatches", "worker_attempts", "runs", "audit_events")
    )


def test_context_is_bounded_readonly_and_identity_survives_connections(service):
    before = counts(service)
    result = handle_local_client_v2(service, request(service, "CONTEXT"))
    assert result["protocol_version"] == 2
    assert result["payload"]["capabilities"] == list(CAPABILITIES)
    assert result["payload"]["projects"] == [
        {"project_id": "project-fixture", "storage_availability": "ONLINE"}
    ]
    assert result["payload"]["execution_targets"] == [
        {"execution_target_id": "m3-sub", "runtime_available": True, "launch_verified": True}
    ]
    assert result["payload"]["owner_start"]["available"] is True
    clone = service.open_request()
    try:
        second = handle_local_client_v2(clone, request(clone, "CAPABILITIES"))
        assert second["store_id"] == result["store_id"]
        assert second["process_epoch"] == result["process_epoch"]
    finally:
        clone.store.close()
    assert counts(service) == before


def test_store_identity_atomic_initialization_and_no_rotation(tmp_path):
    path = tmp_path / "identity.sqlite3"
    initial = SafetyKernelStore(path)
    identity = initial.store_id
    initial.close()

    def reopen(_):
        store = SafetyKernelStore(path)
        try:
            return store.store_id
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(reopen, range(12))) == {identity}
    other = SafetyKernelStore(tmp_path / "other.sqlite3")
    assert other.store_id != identity
    other.close()


def test_corrupt_identity_fails_closed_without_reinitialization(service):
    service.store.connection.execute("UPDATE store_metadata SET value='corrupt'")
    with pytest.raises(RuntimeError, match="identity unavailable"):
        submit(service)
    assert counts(service) == (0, 0, 0, 0, 1)


@pytest.mark.parametrize("operation", ["SUBMIT_V2", "START", "CANCEL"])
@pytest.mark.parametrize(
    "field,code",
    [
        ("expected_store_id", "store_identity_mismatch"),
        ("expected_process_epoch", "process_epoch_mismatch"),
    ],
)
def test_identity_mismatch_rejected_before_any_effect(service, operation, field, code):
    if operation == "SUBMIT_V2":
        call = request(
            service,
            operation,
            task_id="task-1",
            project_id="project-fixture",
            intent="fixture",
            execution_target_id="m3-sub",
        )
    else:
        submit(service)
        call = (
            start_request(service)
            if operation == "START"
            else request(service, operation, task_id="task-1")
        )
    before = counts(service)
    with pytest.raises(ControlPlaneError) as caught:
        handle_local_client_v2(service, call | {field: "f" * 32})
    assert caught.value.code == code
    assert counts(service) == before
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"argv": ["sh"]},
        {"approved": True},
        {"scheduling_policy": "BALANCED"},
        {"authority": "SUPERVISED_AUTO"},
        {"retry": True},
        {"protocol_version": 1},
        {"intent": None},
        {"task_state_version": 0},
    ],
)
def test_submit_rejects_authority_and_unrelated_fields(service, extra):
    with pytest.raises(ValidationError):
        submit(service, **extra)
    assert counts(service) == (0, 0, 0, 0, 1)


@pytest.mark.parametrize("operation", ["APPROVE", "AUTO_ACK", "AUTO_VETO", "RETRY"])
def test_v2_has_no_approval_or_autonomous_operation(service, operation):
    with pytest.raises(ValidationError):
        handle_local_client_v2(service, request(service, "CAPABILITIES") | {"operation": operation})


def test_explicit_registered_target_and_fixed_manual_submission(service):
    with pytest.raises(ControlPlaneError, match="execution_target_not_registered"):
        submit(service, execution_target_id="missing")
    assert counts(service) == (0, 0, 0, 0, 1)
    response = submit(service)
    task = response["payload"]["task"]
    assert task["scheduling_policy"] == "MANUAL"
    assert task["manual_execution_target_id"] == "m3-sub"
    assert task["state_version"] == 0
    assert "intent" not in task and "base_sha" not in task
    assert submit(service) == response
    assert counts(service)[0] == 1
    assert service.dispatch_executor.calls == []


def test_start_original_tuple_idempotence_and_current_task_version(service):
    submit(service)
    original = start_request(service)
    first = handle_local_client_v2(service, original)
    assert service.dispatch_executor.started.wait(2)
    assert first["payload"]["task_state_version"] == 0
    assert first["payload"]["task"]["state_version"] == 1
    assert first["payload"]["authority"] == "OWNER_INITIATED_EXECUTION"
    assert first["payload"]["status"] == "RESERVED"
    second = handle_local_client_v2(service, original)
    assert second == first
    status = handle_local_client_v2(service, original | {"operation": "DISPATCH_STATUS"})
    assert status["payload"] == first["payload"]
    assert service.dispatch_executor.calls == ["start-1"]
    assert counts(service)[1] == 1
    with pytest.raises(ControlPlaneError, match="conflicting_dispatch_request_id"):
        handle_local_client_v2(
            service, original | {"operation": "DISPATCH_STATUS", "task_state_version": 1}
        )


def test_lost_start_response_reconciles_without_second_dispatch(service, monkeypatch):
    submit(service)
    original = start_request(service)
    dispatch = service.dispatch_task

    def lose_response(*args, **kwargs):
        dispatch(*args, **kwargs)
        raise OSError("simulated connection loss after durable reservation")

    monkeypatch.setattr(service, "dispatch_task", lose_response)
    with pytest.raises(OSError):
        handle_local_client_v2(service, original)
    assert service.dispatch_executor.started.wait(2)
    reconciled = handle_local_client_v2(service, original | {"operation": "DISPATCH_STATUS"})
    assert reconciled["payload"]["request_id"] == "start-1"
    assert reconciled["payload"]["task_state_version"] == 0
    assert service.dispatch_executor.calls == ["start-1"]


def test_prior_epoch_reconciliation_is_readonly_recovery_required(service):
    submit(service)
    original = start_request(service)
    handle_local_client_v2(service, original)
    assert service.dispatch_executor.started.wait(2)
    restarted = replace(service, process_epoch="e" * 32)
    before = counts(service)
    with pytest.raises(ControlPlaneError, match="process_epoch_mismatch"):
        handle_local_client_v2(restarted, original)
    result = handle_local_client_v2(restarted, original | {"operation": "DISPATCH_STATUS"})
    assert result["process_epoch"] == "e" * 32
    assert result["payload"]["recovery_required"] is True
    assert counts(service) == before
    assert service.dispatch_executor.calls == ["start-1"]


@pytest.mark.parametrize(
    "gate,code",
    [
        ("owner", "owner_initiated_execution_disabled"),
        ("executor", "dispatch_executor_unavailable"),
        ("target", "manual_execution_target_mismatch"),
    ],
)
def test_start_preflight_gates_make_no_reservation(service, gate, code):
    submit(service)
    call = start_request(service)
    if gate == "owner":
        service.owner_execution.set_enabled(False)
    elif gate == "executor":
        service.dispatch_executor = None
    else:
        call["execution_target_id"] = "other"
    before = counts(service)
    with pytest.raises(ControlPlaneError, match=code):
        handle_local_client_v2(service, call)
    assert counts(service) == before


def test_stale_start_retains_existing_blocked_audit_semantics(service):
    submit(service)
    call = start_request(service) | {"task_state_version": 99}
    with pytest.raises(ControlPlaneError, match="stale_task_state_version"):
        handle_local_client_v2(service, call)
    result = handle_local_client_v2(service, call | {"operation": "DISPATCH_STATUS"})
    assert result["payload"]["status"] == "BLOCKED"
    assert result["payload"]["failure_code"] == "STALE_TASK_STATE_VERSION"
    assert service.dispatch_executor.calls == []


def test_manual_cancel_has_task_level_semantics_and_replay(service):
    submit(service)
    call = request(service, "CANCEL", task_id="task-1", request_id="cancel-1")
    result = handle_local_client_v2(service, call)
    assert result["payload"]["task"]["state"] == "CANCELLED"
    assert result["payload"]["cancelled_now"] is True
    replay = handle_local_client_v2(service, call)
    assert replay["payload"]["task"] == result["payload"]["task"]
    assert replay["payload"]["cancelled_now"] is False


def test_detail_allowlist_bounded_snapshot_never_loads_raw_results(service, monkeypatch):
    submit(service, intent="/private/secret/path token=VERY_PRIVATE_SECRET")
    connection = service.store.connection
    stamp = "2026-10-06T00:00:00+00:00"
    for i in range(DETAIL_LIMIT + 3):
        connection.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (
                f"run-{i}",
                "task-1",
                "/private/worker",
                98765,
                "FINISHED",
                stamp,
                stamp,
                '{"token":"VERY_PRIVATE_SECRET","log":"/private/log"}',
            ),
        )
    for i in range(DETAIL_LIMIT + 1):
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?)",
            (
                f"approval-{i}",
                "task-1",
                "HIGH_RISK_EXECUTION",
                "PENDING",
                stamp,
                None,
            ),
        )
    queries = []
    connection.set_trace_callback(queries.append)
    for name in ("task_detail", "task_runs", "verification_report", "approvals_for_task"):
        monkeypatch.setattr(service, name, lambda *a: pytest.fail("raw view called"))
    result = handle_local_client_v2(service, request(service, "DETAIL", task_id="task-1"))
    rendered = json.dumps(result)
    assert "VERY_PRIVATE_SECRET" not in rendered
    assert "/private" not in rendered and "98765" not in rendered
    assert "result" not in rendered and "worker_id" not in rendered
    assert result["payload"]["answer_completeness"] == "UNAVAILABLE"
    assert result["payload"]["verification"]["status"] == "NOT_OBSERVED"
    assert len(result["payload"]["runs"]) == DETAIL_LIMIT
    assert len(result["payload"]["approvals"]) == DETAIL_LIMIT
    assert result["payload"]["truncated"] == {"runs": True, "approvals": True}
    assert "BEGIN" in queries and "ROLLBACK" in queries
    assert not any("result_json" in sql or "audit_events" in sql for sql in queries)


def test_catalog_bounded_and_omits_sensitive_project_fields(service):
    for i in range(CATALOG_LIMIT + 3):
        service.store.connection.execute(
            "INSERT INTO projects SELECT ?, display_name, canonical_repo_root, git_root, "
            "default_branch, last_known_head, created_at, updated_at, working_subpath, "
            "remote_url, last_opened_at, storage_availability, security_bookmark_b64, "
            "scheduling_policy,manual_execution_target_id,supervised_auto_allowed,"
            "unattended_allowed,grace_seconds FROM projects WHERE project_id='project-fixture'",
            (f"project-{i:03}",),
        )
    result = handle_local_client_v2(service, request(service, "CONTEXT"))
    assert len(result["payload"]["projects"]) == CATALOG_LIMIT
    assert result["payload"]["truncated"]["projects"] is True
    assert "canonical_repo_root" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["claim", "permit", "guard", "start", "legacy_start"])
def test_bound_target_enforced_at_every_transactional_launch_boundary(tmp_path, boundary):
    store = _reserved(tmp_path)
    try:
        if boundary in {"permit", "guard", "start"}:
            _claim(store)
        if boundary == "guard":
            store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
        elif boundary == "start":
            _observe(store)
        store.connection.execute(
            "UPDATE tasks SET scheduling_policy='MANUAL',manual_execution_target_id='other' "
            "WHERE task_id='task'"
        )
        with pytest.raises(RuntimeError, match="manual execution target mismatch"):
            if boundary == "claim":
                _claim(store)
            elif boundary == "permit":
                store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
            elif boundary == "guard":
                with store.worker_spawn_guard(
                    attempt_id="attempt", executor_id="executor", spawn_ticket="ticket"
                ):
                    pytest.fail("spawn entered with mismatched target")
            elif boundary == "start":
                _start(store)
            else:
                _start(store, attempt_id=None, executor_id=None)
        assert store.get_task("task").state is TaskState.READY
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        if boundary == "claim":
            assert store.get_worker_attempt_for_dispatch("dispatch") is None
    finally:
        store.close()


def test_legacy_targetless_manual_still_dispatches_and_starts(service, tmp_path):
    service.store.submit_task(
        task_id="task-1",
        request_id="submit-1",
        intent="fixture",
        project_id="project-fixture",
        base_sha="abc123",
        scheduling_policy="MANUAL",
    )
    with pytest.raises(ControlPlaneError, match="manual_task_required"):
        handle_local_client_v2(service, start_request(service))
    response = service.dispatch_task(
        "task-1",
        {"request_id": "legacy-start", "task_state_version": 0, "execution_target_id": "m3-sub"},
    )
    assert response.status == "RESERVED"
    store = _reserved(tmp_path)
    store.connection.execute("UPDATE tasks SET scheduling_policy='MANUAL'")
    _start(store, attempt_id=None, executor_id=None)
    assert store.get_task("task").state is TaskState.RUNNING
    store.close()


def test_initiator_rechecks_durable_binding_before_reservation(service):
    submit(service)
    with pytest.raises(ControlPlaneError, match="manual_execution_target_mismatch"):
        service.dispatch_task(
            "task-1",
            {"request_id": "wrong", "task_state_version": 0, "execution_target_id": "other"},
        )
    assert counts(service)[1] == 0


def test_cli_forwards_v2_once_and_preserves_v1_parser(service, monkeypatch, capsys):
    calls = []

    def downstream(_client, method, path, *, payload=None):
        calls.append((method, path))
        return handle_local_client_v2(service, payload)

    monkeypatch.setattr(ControlPlaneClient, "_request", downstream)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request(service, "CONTEXT"))))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 0
    assert json.loads(capsys.readouterr().out)["protocol_version"] == 2
    assert calls == [("POST", "/v2/local-client")]
    monkeypatch.setattr("sys.stdin", io.StringIO('{"protocol_version":1}'))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 1
    assert calls == [("POST", "/v2/local-client")]


def _http(service, body, *, path="/v2/local-client", method="POST", host="localhost"):
    # Run the real HTTP handler over an in-memory accepted connection. This
    # exercises routing/header/schema/error handling even on socket-denied CI.
    raw = json.dumps(body).encode()
    wire = (
        f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(raw)}\r\n\r\n"
    ).encode() + raw

    class Connection:
        def __init__(self):
            self.output = bytearray()

        def makefile(self, *_args):
            return io.BytesIO(wire)

        def sendall(self, data):
            self.output.extend(data)

    connection = Connection()
    handler_for_control(service)(connection, ("local", 0), object())
    header, response = bytes(connection.output).split(b"\r\n\r\n", 1)
    return int(header.split()[1]), json.loads(response)


def test_v2_real_http_handler_route_without_socket(service):
    code, response = _http(service, request(service, "CONTEXT"))
    assert code == 200 and response["store_id"] == service.store.store_id
    code, _ = _http(service, request(service, "CONTEXT"), method="GET")
    assert code == 405
    code, response = _http(service, {"protocol_version": 2, "operation": "APPROVE"})
    assert code == 400 and response == {"error": "invalid_json_schema"}
    code, response = _http(service, request(service, "CONTEXT"), host="evil.example")
    assert code == 403 and response == {"error": "invalid_host"}


def test_actual_fakeworker_manual_start_attempt_at_most_once(tmp_path):
    """The v2 route reaches the unchanged fakeworker, verifier and cleanup path."""
    from personal_ai_orchestrator.control_api import ControlPlaneService
    from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
    from tests.test_dispatch_executor import ExecutorHarness, _git

    harness = ExecutorHarness(tmp_path)
    store = SafetyKernelStore(harness.state_db)
    project = store.register_project(
        project_id="project-main",
        display_name="Fixture",
        canonical_repo_root=str(harness.main_repo),
        git_root=str(harness.main_repo),
        default_branch="main",
        last_known_head=_git(harness.main_repo, "rev-parse", "HEAD"),
    )
    finished = threading.Event()
    calls = []

    class Executor:
        def execute(self, request_id):
            calls.append(request_id)
            try:
                harness.executor.execute(request_id)
            finally:
                finished.set()

    service = ControlPlaneService(
        store=store,
        registry=harness.registry,
        owner_execution=OwnerExecutionSettings(initial=True),
        dispatch_executor=Executor(),
        runtime_availability={"zai-coding-plan-glm-5.3": True},
        execution_evidence_journal=harness.execution_evidence,
    )
    try:
        handle_local_client_v2(
            service,
            request(
                service,
                "SUBMIT_V2",
                task_id="task-1",
                request_id="submit-1",
                project_id=project.project_id,
                intent="Create hello.txt fixture",
                execution_target_id="zai-coding-plan-glm-5.3",
            ),
        )
        original = start_request(service) | {"execution_target_id": "zai-coding-plan-glm-5.3"}
        handle_local_client_v2(service, original)
        assert finished.wait(10), "fakeworker did not reach terminal cleanup"
        # Explicit duplicate server admission remains observational even after
        # the original worker finished; client code itself never resends START.
        handle_local_client_v2(service, original)
        status = handle_local_client_v2(service, original | {"operation": "DISPATCH_STATUS"})
        assert status["payload"]["status"] == "FINISHED"
        assert status["payload"]["task_state_version"] == 0
        assert calls == ["start-1"]
        assert store.connection.execute("SELECT COUNT(*) FROM worker_attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        attempt = store.get_worker_attempt_for_dispatch("owner-dispatch-start-1")
        assert attempt.cleanup_state == "CONFIRMED"
        assert not store.has_cleanup_quarantine("task-1")
        assert store.get_workspace("task-1").writer_token is None
        assert harness.main_unchanged()
        detail = handle_local_client_v2(service, request(service, "DETAIL", task_id="task-1"))
        assert detail["payload"]["answer_completeness"] == "UNAVAILABLE"
        assert "result" not in json.dumps(detail)
    finally:
        store.close()


def test_concurrent_duplicate_start_only_one_dispatch(service):
    submit(service)
    original = start_request(service)

    def send(_):
        clone = service.open_request()
        try:
            return handle_local_client_v2(clone, original)
        finally:
            clone.store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(8)))
    assert service.dispatch_executor.started.wait(2)
    assert all(item["payload"]["request_id"] == "start-1" for item in responses)
    assert service.dispatch_executor.calls == ["start-1"]
    assert counts(service)[1] == 1


def test_unregistered_project_blocks_manual_submit(service):
    before = counts(service)
    with pytest.raises(ControlPlaneError, match="project_not_registered"):
        submit(service, project_id="not-registered")
    assert counts(service) == before


def test_http_mutation_checks_identity_on_actual_handling_store(service, tmp_path, monkeypatch):
    original = request(
        service,
        "SUBMIT_V2",
        task_id="task-1",
        project_id="project-fixture",
        intent="fixture",
        execution_target_id="m3-sub",
    )
    other_path = tmp_path / "replacement.sqlite3"
    other = SafetyKernelStore(other_path)
    replacement_id = other.store_id
    other.close()

    def replaced_host():
        return replace(service, store=SafetyKernelStore(other_path))

    monkeypatch.setattr(service, "open_request", replaced_host)
    code, response = _http(service, original)
    assert code == 409 and response == {"error": "store_identity_mismatch"}
    other = SafetyKernelStore(other_path)
    try:
        assert other.store_id == replacement_id
        assert other.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    finally:
        other.close()


@pytest.mark.parametrize("extra", [{"argv": ["sh"]}, {"approved": True}, {"protocol_version": 3}])
def test_http_v2_extra_authority_or_protocol_rejected(service, extra):
    before = counts(service)
    code, response = _http(service, request(service, "CONTEXT") | extra)
    assert code == 400 and response == {"error": "invalid_json_schema"}
    assert counts(service) == before


def test_dispatch_status_does_not_load_or_return_freeform_failure(service):
    submit(service)
    original = start_request(service)
    handle_local_client_v2(service, original)
    assert service.dispatch_executor.started.wait(2)
    service.store.connection.execute(
        "UPDATE owner_dispatches SET failure_reason=? WHERE request_id='start-1'",
        ("/private/secret/path AUTH_TOKEN=" + "secret" * 100_000,),
    )
    queries = []
    service.store.connection.set_trace_callback(queries.append)
    result = handle_local_client_v2(service, original | {"operation": "DISPATCH_STATUS"})
    assert "/private" not in json.dumps(result) and "AUTH_TOKEN" not in json.dumps(result)
    assert not any("failure_reason" in query for query in queries)
