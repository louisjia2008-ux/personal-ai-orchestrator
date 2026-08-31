import json
import shutil
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.approval import ApprovalAuthority, ApprovalKind
from personal_ai_orchestrator.cli import main as cli_main
from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneServer,
    ControlPlaneService,
)
from personal_ai_orchestrator.control_client import (
    ControlPlaneClient,
    ControlPlaneUnavailable,
    UnixSocketHTTPConnection,
)
from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
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
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    unknown_availability,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult, VerificationStage

NOW = datetime(2026, 8, 31, tzinfo=UTC)
SECRET_MARKER = "credential-ref-must-never-appear"


def _registry() -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        confidence=EvidenceConfidence.ESTIMATED,
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18000,
        remaining_fraction=0.8,
        window_started_at=NOW - timedelta(hours=3),
        reset_at=NOW + timedelta(hours=2),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.ESTIMATED,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-1",
        quota_pool_id="pool",
        provider_id="minimax",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.ESTIMATED,
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
                display_name="M3",
                capabilities=CapabilityProfile(scores={"debugging": 0.9}),
            )
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
            )
        },
    )


@pytest.fixture
def harness(tmp_path):
    # macOS limits AF_UNIX sun_path to 104 bytes; pytest tmp_path dirs are deeper than that.
    socket_dir = Path(tempfile.mkdtemp(prefix="pao-ctl-"))
    socket_path = socket_dir / "control.sock"
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    availability = QuotaAvailabilityJournal(tmp_path)
    availability.save(
        unknown_availability(
            execution_target_id="m3-sub",
            provider_id="minimax",
            quota_pool_id="pool",
            observed_at=NOW,
        )
    )
    service = ControlPlaneService(
        registry=_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=availability,
    )
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path)
    try:
        yield type(
            "Harness",
            (),
            {
                "store": store,
                "service": service,
                "server": server,
                "client": client,
                "tmp_path": tmp_path,
                "socket_path": socket_path,
                "availability": availability,
            },
        )()
    finally:
        server.stop()
        store.close()
        shutil.rmtree(socket_dir, ignore_errors=True)


def _raw_request(socket_path, method, path, *, headers=None, body=None):
    connection = UnixSocketHTTPConnection(socket_path)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _submit(harness, *, task_id="task-1", request_id="req-1", intent="fix the bug"):
    return harness.client.submit(task_id=task_id, request_id=request_id, intent=intent)


def test_health_roundtrip(harness):
    health = harness.client.health()
    assert health.status == "ok"
    assert health.api_version == "v1"


def test_socket_is_permission_restricted(harness):
    mode = stat.S_IMODE(harness.server.socket_path.stat().st_mode)
    assert mode == 0o600


def test_second_server_on_live_socket_fails_closed(harness):
    with pytest.raises(RuntimeError):
        ControlPlaneServer(harness.service, harness.socket_path)


def test_submit_get_list_roundtrip(harness):
    view = _submit(harness)
    assert view.state == TaskState.SUBMITTED.value
    assert view.state_version == 0

    fetched = harness.client.get_task("task-1")
    assert fetched.task_id == "task-1"
    assert fetched.intent == "fix the bug"

    listed = harness.client.list_tasks()
    assert listed.total == 1
    assert listed.tasks[0].task_id == "task-1"


def test_submit_is_idempotent_by_request_id(harness):
    first = _submit(harness)
    second = _submit(harness)
    assert first.model_dump() == second.model_dump()
    total = harness.store.connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
    assert total == 1
    events = [
        event
        for event in harness.store.audit_events("task-1")
        if event["event_type"] == "TASK_SUBMITTED"
    ]
    assert len(events) == 1


def test_submit_conflicting_request_id_rejected(harness):
    _submit(harness)
    with pytest.raises(ControlPlaneError) as error:
        _submit(harness, request_id="req-1", intent="different intent")
    assert error.value.status == 400
    assert error.value.code == "conflicting_request_id"


def test_submit_rejects_unknown_fields(harness):
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks",
        headers={"content-type": "application/json"},
        body=json.dumps(
            {
                "task_id": "task-x",
                "request_id": "req-x",
                "intent": "text",
                "shell_command": "rm -rf /",
            }
        ).encode(),
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid_json_schema"}


def test_submit_rejects_unsafe_task_id(harness):
    with pytest.raises(ControlPlaneError) as error:
        harness.client.submit(
            task_id="../escape",
            request_id="req-2",
            intent="text",
        )
    assert error.value.status == 400
    assert error.value.code == "invalid_task_id"


def test_malformed_json_rejected(harness):
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks",
        headers={"content-type": "application/json"},
        body=b"{not json",
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid_json"}


def test_wrong_content_type_rejected(harness):
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks",
        headers={"content-type": "text/plain"},
        body=b"{}",
    )
    assert status == 415
    assert json.loads(body) == {"error": "json_required"}


def test_oversized_body_rejected(harness):
    payload = json.dumps(
        {"task_id": "t" * 100, "request_id": "r", "intent": "x" * 70000}
    ).encode()
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks",
        headers={"content-type": "application/json"},
        body=payload,
    )
    assert status == 413
    assert json.loads(body)["error"] == "request_too_large_or_empty"


def test_non_object_json_rejected(harness):
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks",
        headers={"content-type": "application/json"},
        body=b"[1, 2, 3]",
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid_json_object"}


def test_unknown_task_returns_sanitized_404(harness):
    with pytest.raises(ControlPlaneError) as error:
        harness.client.get_task("missing-task")
    assert error.value.status == 404
    assert error.value.code == "task_not_found"


def test_unknown_path_returns_404(harness):
    status, body = _raw_request(
        harness.socket_path,
        "GET",
        "/v1/definitely-not-a-path",
    )
    assert status == 404
    assert json.loads(body) == {"error": "not_found"}


def test_invalid_host_header_rejected(harness):
    status, body = _raw_request(
        harness.socket_path,
        "GET",
        "/v1/health",
        headers={"host": "localhost.evil.invalid"},
    )
    assert status == 403
    assert json.loads(body) == {"error": "invalid_host"}


def test_cancel_non_running_task(harness):
    harness.store.submit_task(task_id="task-c", request_id="req-c", intent="cancel me")
    harness.store.transition_task("task-c", TaskState.READY)
    view = harness.client.cancel("task-c", request_id="cancel-1")
    assert view.cancelled_now is True
    assert view.task.state == TaskState.CANCELLED.value

    again = harness.client.cancel("task-c", request_id="cancel-1")
    assert again.cancelled_now is False
    assert again.task.state == TaskState.CANCELLED.value


def test_cancel_running_task_requires_execution_supervisor(harness):
    harness.store.submit_task(task_id="task-r", request_id="req-r", intent="running")
    harness.store.transition_task("task-r", TaskState.READY)
    harness.store.transition_task("task-r", TaskState.RUNNING)
    with pytest.raises(ControlPlaneError) as error:
        harness.client.cancel("task-r")
    assert error.value.status == 409
    assert error.value.code == "running_task_cancellation_requires_execution_supervisor"


def test_cancel_terminal_task_rejected(harness):
    harness.store.submit_task(task_id="task-t", request_id="req-t", intent="terminal")
    harness.store.transition_task("task-t", TaskState.READY)
    harness.store.transition_task("task-t", TaskState.RUNNING)
    harness.store.transition_task("task-t", TaskState.FAILED)
    with pytest.raises(ControlPlaneError) as error:
        harness.client.cancel("task-t")
    assert error.value.status == 409
    assert error.value.code == "task_state_is_terminal"


def test_cancel_unknown_task_404(harness):
    with pytest.raises(ControlPlaneError) as error:
        harness.client.cancel("nope")
    assert error.value.status == 404


def _verified_result(passed: bool) -> VerificationResult:
    stage = VerificationStage(
        name="pytest",
        argv=(".venv/bin/python", "-m", "pytest", "-q"),
        returncode=0 if passed else 1,
        stdout="",
        stderr="",
    )
    result = VerificationResult(
        profile="profile",
        passed=passed,
        changed_paths=("src/tiny.py",),
        unexpected_paths=(),
        stages=(stage,),
        evidence_id=None,
        failure_reason=None if passed else "verifier command failed: pytest",
    )
    import hashlib

    digest = hashlib.sha256(
        json.dumps(
            {
                "passed": passed,
                "changed": list(result.changed_paths),
                "stage": stage.name,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    return result.model_copy(update={"evidence_id": f"verify-{digest}"})


def test_verification_report_for_verified_task(harness):
    harness.store.submit_task(task_id="task-v", request_id="req-v", intent="verify me")
    for state in (TaskState.READY, TaskState.RUNNING, TaskState.WORKER_FINISHED):
        harness.store.transition_task("task-v", state)
    begin_verification(harness.store, task_id="task-v")
    result = _verified_result(passed=True)
    harness.service.verification_journal.append(result)
    apply_verification_result(
        harness.store,
        task_id="task-v",
        result=result,
        evidence_journal=harness.service.verification_journal,
    )

    report = harness.client.verification_report("task-v")
    assert report.status == "VERIFIED"
    assert report.evidence_id == result.evidence_id
    assert report.result is not None
    assert report.result.passed is True
    assert report.task_state == TaskState.VERIFIED.value


def test_verification_report_for_failed_task(harness):
    harness.store.submit_task(task_id="task-f", request_id="req-f", intent="fail verify")
    for state in (TaskState.READY, TaskState.RUNNING, TaskState.WORKER_FINISHED):
        harness.store.transition_task("task-f", state)
    begin_verification(harness.store, task_id="task-f")
    result = _verified_result(passed=False)
    apply_verification_result(
        harness.store,
        task_id="task-f",
        result=result,
        evidence_journal=harness.service.verification_journal,
    )

    report = harness.client.verification_report("task-f")
    assert report.status == "FAILED_VERIFICATION"
    assert report.result is None
    assert report.task_state == TaskState.BLOCKED.value


def test_verification_report_not_verified(harness):
    _submit(harness)
    report = harness.client.verification_report("task-1")
    assert report.status == "NOT_VERIFIED"
    assert report.result is None


def test_routing_decision_view(harness):
    harness.store.submit_task(task_id="task-d", request_id="req-d", intent="routing")
    decision = {
        "decision_id": "route-1",
        "request_id": "req-d",
        "mode": "SHADOW",
        "selected_execution_target_id": None,
        "fallback_reason": "quota confidence remained UNKNOWN",
    }
    harness.store.record_routing_decision(
        decision_id="route-1",
        request_id="req-d",
        task_id="task-d",
        payload=decision,
    )
    view = harness.client.routing_decision("task-d")
    assert view.decision_id == "route-1"
    assert view.decision["mode"] == "SHADOW"
    assert view.decision["fallback_reason"] == "quota confidence remained UNKNOWN"


def test_dashboard_summary_composes_sanitized_authoritative_state(harness):
    harness.store.submit_task(task_id="task-dash", request_id="req-dash", intent="dashboard")
    harness.store.transition_task("task-dash", TaskState.READY)
    harness.store.transition_task("task-dash", TaskState.RUNNING)

    view = harness.client.dashboard()
    assert view.connection.status == "ok"
    assert view.counts.running == 1
    assert view.counts.total == 1
    assert view.recent_tasks[0].task_id == "task-dash"
    assert view.active_status.production_active == "DISABLED_BY_DESIGN"
    assert "explicit owner approval missing" in view.important_blockers
    assert any(event.event_type == "TASK_STATE_CHANGED" for event in view.recent_events)

    raw = view.model_dump_json().lower()
    assert SECRET_MARKER not in raw
    assert "credential_ref" not in raw


def test_dashboard_summary_is_read_only(harness):
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/dashboard",
        headers={"content-type": "application/json"},
        body=b"{}",
    )
    assert status == 405
    assert json.loads(body) == {"error": "method_not_allowed"}


def test_task_detail_composes_runs_routing_verification_approvals_and_events(harness):
    harness.store.submit_task(task_id="task-detail", request_id="req-detail", intent="detail")
    harness.store.register_workspace(
        task_id="task-detail",
        repo_path="/repo",
        worktree_path="/repo-worktree",
        branch="codex/task-detail",
        base_sha="abc123",
    )
    harness.store.record_routing_decision(
        decision_id="route-detail",
        request_id="route-req-detail",
        task_id="task-detail",
        payload={
            "mode": "SHADOW",
            "selected_execution_target_id": None,
            "fallback_reason": "quota confidence remained UNKNOWN",
        },
    )
    harness.store.transition_task("task-detail", TaskState.READY)
    harness.store.transition_task("task-detail", TaskState.RUNNING)
    harness.store.start_run(run_id="run-detail", task_id="task-detail", worker_id="m3-sub")
    authority = ApprovalAuthority(harness.store)
    authority.request(
        approval_id="approval-detail",
        task_id="task-detail",
        kind=ApprovalKind.HIGH_RISK_EXECUTION,
    )

    view = harness.client.task_detail("task-detail")
    assert view.task.task_id == "task-detail"
    assert view.runs[0].run_id == "run-detail"
    assert view.routing is not None
    assert view.routing.decision_id == "route-detail"
    assert view.verification.status == "NOT_VERIFIED"
    assert view.approvals.approvals[0].approval_id == "approval-detail"
    assert view.workspace is not None
    assert view.workspace.writer_locked is False
    assert [event.event_type for event in view.events][0] == "TASK_SUBMITTED"


def test_routing_decision_missing_404(harness):
    _submit(harness)
    with pytest.raises(ControlPlaneError) as error:
        harness.client.routing_decision("task-1")
    assert error.value.status == 404
    assert error.value.code == "routing_decision_not_found"


def test_runs_listing(harness):
    harness.store.submit_task(task_id="task-run", request_id="req-run", intent="runs")
    harness.store.start_run(run_id="run-1", task_id="task-run", worker_id="glm")
    runs = harness.client.task_runs("task-run")
    assert len(runs.runs) == 1
    assert runs.runs[0].worker_id == "glm"
    assert runs.runs[0].status == "RUNNING"

    single = harness.client.get_run("run-1")
    assert single.run_id == "run-1"


def test_provider_health_is_sanitized(harness):
    view = harness.client.providers()
    assert len(view.providers) == 1
    provider = view.providers[0]
    assert provider.provider_id == "minimax"
    assert provider.account_count == 1
    assert provider.display_name == "MiniMax CN"

    raw = json.dumps(json.loads(view.model_dump_json()))
    for forbidden in (
        SECRET_MARKER,
        "credential_ref",
        "api_key",
        "token",
        "authorization",
    ):
        assert forbidden not in raw.lower()

    pool = provider.quota_pools[0]
    assert pool.state == QuotaState.AVAILABLE.value
    assert pool.confidence == EvidenceConfidence.ESTIMATED.value
    assert pool.measurement_source_type == EvidenceSourceType.PROVIDER_API.value

    target = provider.execution_targets[0]
    assert target.execution_target_id == "m3-sub"
    assert target.runtime_available is True
    assert target.observed_availability is not None
    assert target.observed_availability.state == "UNKNOWN"
    assert target.observed_availability.confidence == EvidenceConfidence.UNKNOWN.value


def test_quota_alias_matches_providers(harness):
    assert harness.client.quota().providers == harness.client.providers().providers


def test_active_status_fail_closed(harness):
    view = harness.client.active_status()
    assert view.production_active == "DISABLED_BY_DESIGN"
    assert view.authorized is False
    assert "explicit owner approval missing" in view.blocking_reasons
    assert "P3.5 Shadow evidence not accepted" in view.blocking_reasons


def test_approvals_read_only(harness):
    harness.store.submit_task(task_id="task-a", request_id="req-a", intent="approval")
    authority = ApprovalAuthority(harness.store)
    authority.request(
        approval_id="approval-1",
        task_id="task-a",
        kind=ApprovalKind.HIGH_RISK_EXECUTION,
    )
    listed = harness.client.approvals_for_task("task-a")
    assert listed.approvals[0].approval_id == "approval-1"
    assert listed.approvals[0].status == "PENDING"

    single = harness.client.get_approval("approval-1")
    assert single.kind == ApprovalKind.HIGH_RISK_EXECUTION.value

    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/approvals",
        headers={"content-type": "application/json"},
        body=b"{}",
    )
    assert status == 404
    assert json.loads(body) == {"error": "not_found"}


def test_no_client_path_can_mark_verified(harness):
    _submit(harness)
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks/task-1/verify",
        headers={"content-type": "application/json"},
        body=json.dumps({"passed": True, "evidence_id": "verify-forged"}).encode(),
    )
    assert status == 404
    assert harness.client.get_task("task-1").state == TaskState.SUBMITTED.value


def test_state_mutation_methods_rejected(harness):
    for method in ("PUT", "DELETE", "PATCH"):
        status, _ = _raw_request(
            harness.socket_path,
            method,
            "/v1/tasks/task-1",
        )
        assert status == 405


def test_write_endpoints_cannot_target_authority_surfaces(harness):
    for path in ("/v1/active-status", "/v1/providers", "/v1/quota"):
        status, body = _raw_request(
            harness.socket_path,
            "POST",
            path,
            headers={"content-type": "application/json"},
            body=b"{}",
        )
        assert status == 405
        assert json.loads(body) == {"error": "method_not_allowed"}


def test_client_fails_closed_when_daemon_unavailable(tmp_path):
    client = ControlPlaneClient(tmp_path / "missing.sock")
    with pytest.raises(ControlPlaneUnavailable):
        client.get_task("task-1")


def test_cli_reports_unavailable_daemon(tmp_path, capsys):
    code = cli_main(["--socket", str(tmp_path / "missing.sock"), "status", "task-1"])
    assert code == 1
    assert "control plane unavailable" in capsys.readouterr().err


def test_cli_submit_status_active_status(harness, capsys):
    socket_arg = "--socket"
    socket_value = str(harness.socket_path)

    assert cli_main([socket_arg, socket_value, "submit", "--task-id", "cli-1",
                     "--request-id", "cli-req-1", "--intent", "cli driven task"]) == 0
    out = capsys.readouterr().out
    assert "SUBMITTED" in out

    assert cli_main([socket_arg, socket_value, "status", "cli-1"]) == 0
    assert "cli-1" in capsys.readouterr().out

    assert cli_main([socket_arg, socket_value, "active-status"]) == 0
    out = capsys.readouterr().out
    assert "DISABLED_BY_DESIGN" in out
    assert "explicit owner approval missing" in out

    assert cli_main([socket_arg, socket_value, "--json", "list"]) == 0
    assert "cli-1" in capsys.readouterr().out

    assert cli_main([socket_arg, socket_value, "cancel", "cli-1", "--request-id", "cancel-1"]) == 0
    assert cli_main([socket_arg, socket_value, "status", "cli-1"]) == 0
    assert "CANCELLED" in capsys.readouterr().out


def test_cli_providers_render(harness, capsys):
    assert cli_main(["--socket", str(harness.socket_path), "providers"]) == 0
    out = capsys.readouterr().out
    assert "minimax" in out
    assert "ESTIMATED" in out
    assert SECRET_MARKER not in out


def test_daemon_control_service_wiring(tmp_path):
    import shutil
    import tempfile

    from personal_ai_orchestrator.daemon import build_control_service
    from personal_ai_orchestrator.runtime_config import RuntimeConfig

    socket_dir = Path(tempfile.mkdtemp(prefix="pao-ctl-"))
    socket_path = socket_dir / "control.sock"
    state_root = tmp_path / "runtime-state"
    config = RuntimeConfig(
        catalog_snapshot_id="catalog-p40",
        registry=_registry(),
        runtime_availability={"m3-sub": True},
    )
    control_service = build_control_service(
        config=config,
        state_db=tmp_path / "state.sqlite3",
        runtime_state_root=state_root,
    )
    server = ControlPlaneServer(control_service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path)
    try:
        view = client.submit(task_id="wire-1", request_id="wire-req-1", intent="wired")
        assert view.state == TaskState.SUBMITTED.value
        status = client.active_status()
        assert status.production_active == "DISABLED_BY_DESIGN"
        assert status.authorized is False
    finally:
        server.stop()
        control_service.store.close()
        shutil.rmtree(socket_dir, ignore_errors=True)
