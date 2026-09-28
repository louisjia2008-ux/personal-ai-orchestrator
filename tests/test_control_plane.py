import json
import os
import shutil
import stat
import subprocess
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
from personal_ai_orchestrator.delegation_campaign import (
    DelegationCalibrationCampaignStore,
)
from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
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
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.pi5_identity import CampaignExecutionIdentityFactory
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
                execution_verified=True,
            )
        },
    )


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
        owner_execution=OwnerExecutionSettings(tmp_path / "owner-execution.json", initial=True),
    )
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path)
    repo = _make_repo(tmp_path / "project")
    project = client.register_project(path=str(repo), display_name="Fixture")
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
                "repo": repo,
                "project": project,
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


def _submit(
    harness,
    *,
    task_id="task-1",
    request_id="req-1",
    intent="fix the bug",
    project_id=None,
):
    return harness.client.submit(
        task_id=task_id,
        request_id=request_id,
        project_id=project_id or harness.project.project_id,
        intent=intent,
    )


def test_health_roundtrip(harness):
    health = harness.client.health()
    assert health.status == "ok"
    assert health.api_version == "v1"
    assert health.process_id == os.getpid()
    assert len(health.process_instance_id) >= 16
    # One daemon process keeps one opaque identity across reads.
    again = harness.client.health()
    assert again.process_id == health.process_id
    assert again.process_instance_id == health.process_instance_id


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
    assert view.project_id == harness.project.project_id
    assert view.base_sha == _git(harness.repo, "rev-parse", "HEAD")

    fetched = harness.client.get_task("task-1")
    assert fetched.task_id == "task-1"
    assert fetched.intent == "fix the bug"
    assert fetched.working_subpath is None

    listed = harness.client.list_tasks()
    assert listed.total == 1
    assert listed.tasks[0].task_id == "task-1"


def test_project_registry_resolves_registers_and_lists_git_roots(harness):
    nested = harness.repo / "apps" / "desktop"
    nested.mkdir(parents=True)
    preview = harness.client.resolve_project(path=str(nested))
    assert preview.git_root == str(harness.repo.resolve())
    assert preview.canonical_repo_root == str(nested.resolve())
    assert preview.working_subpath == "apps/desktop"
    assert preview.storage_availability == "ONLINE"

    project = harness.client.register_project(path=str(nested), display_name="Desktop")
    assert project.display_name == "Desktop"
    assert project.working_subpath == "apps/desktop"
    listed = harness.client.list_projects()
    assert {item.project_id for item in listed.projects} >= {
        harness.project.project_id,
        project.project_id,
    }


def test_project_registration_rejects_invalid_repositories(harness, tmp_path):
    invalid = tmp_path / "not-a-repo"
    invalid.mkdir()
    with pytest.raises(ControlPlaneError) as error:
        harness.client.resolve_project(path=str(invalid))
    assert error.value.status == 400
    assert error.value.code == "invalid_repository"


def test_project_removal_only_forgets_registration(harness):
    removed = harness.client.remove_project(harness.project.project_id)
    assert removed.removed_from_orchestrator is True
    assert harness.repo.exists()
    assert (harness.repo / "README.md").exists()
    assert harness.project.project_id not in {
        project.project_id for project in harness.client.list_projects().projects
    }


def test_submit_requires_registered_project(harness):
    with pytest.raises(ControlPlaneError) as error:
        harness.client.submit(
            task_id="task-missing-project",
            request_id="req-missing-project",
            project_id="project-not-registered",
            intent="fix",
        )
    assert error.value.status == 400
    assert error.value.code == "project_not_registered"


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


def test_campaign_scoped_submission_uses_canonical_idempotency_without_history_collision(
    harness,
):
    historical = _submit(
        harness,
        task_id="pi5b3g-obs1-parent",
        request_id="pi5b3g-obs1-submit",
        intent="historical campaign request",
    )
    campaign = DelegationCalibrationCampaignStore(harness.tmp_path / "campaign.json")

    started_a = campaign.start(max_observations=3)
    identity_a = CampaignExecutionIdentityFactory(started_a.campaign_id).observation(1)
    first = _submit(
        harness,
        task_id=identity_a.parent_task_id,
        request_id=identity_a.parent_submit_request_id,
        intent="bounded observation",
    )
    replay = _submit(
        harness,
        task_id=identity_a.parent_task_id,
        request_id=identity_a.parent_submit_request_id,
        intent="bounded observation",
    )
    assert replay.model_dump() == first.model_dump()
    first_dispatch = harness.client.dispatch(
        first.task_id,
        request_id=identity_a.parent_dispatch_request_id,
        task_state_version=first.state_version,
        execution_target_id="m3-sub",
    )
    dispatch_replay = harness.client.dispatch(
        first.task_id,
        request_id=identity_a.parent_dispatch_request_id,
        task_state_version=first.state_version,
        execution_target_id="m3-sub",
    )
    assert dispatch_replay.model_dump() == first_dispatch.model_dump()
    assert first_dispatch.dispatch_id == identity_a.parent_dispatch_id

    with pytest.raises(ControlPlaneError) as dispatch_conflict:
        harness.client.dispatch(
            first.task_id,
            request_id=identity_a.parent_dispatch_request_id,
            task_state_version=first.state_version + 1,
            execution_target_id="m3-sub",
        )
    assert dispatch_conflict.value.code == "conflicting_dispatch_request_id"

    with pytest.raises(ControlPlaneError) as conflict:
        _submit(
            harness,
            task_id=identity_a.parent_task_id,
            request_id=identity_a.parent_submit_request_id,
            intent="different payload",
        )
    assert conflict.value.code == "conflicting_request_id"

    campaign.stop()
    started_b = campaign.start(max_observations=3)
    identity_b = CampaignExecutionIdentityFactory(started_b.campaign_id).observation(1)
    second_campaign = _submit(
        harness,
        task_id=identity_b.parent_task_id,
        request_id=identity_b.parent_submit_request_id,
        intent="bounded observation",
    )

    assert started_a.campaign_id != started_b.campaign_id
    assert first.task_id != second_campaign.task_id
    assert first.request_id != second_campaign.request_id
    assert historical.request_id == "pi5b3g-obs1-submit"
    assert harness.store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3


def test_owner_dispatch_accepts_verified_target_and_is_idempotent(harness):
    task = _submit(harness)

    first = harness.client.dispatch(
        "task-1",
        request_id="dispatch-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
    )
    second = harness.client.dispatch(
        "task-1",
        request_id="dispatch-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
    )

    assert first.accepted is True
    assert first.authority == "OWNER_INITIATED_EXECUTION"
    assert first.status == "RESERVED"
    assert first.task.state == TaskState.READY.value
    assert second.model_dump() == first.model_dump()
    assert harness.client.active_status().production_active == "DISABLED_BY_DESIGN"
    rows = harness.store.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE request_id='dispatch-1'"
    ).fetchone()
    assert rows["n"] == 0
    rows = harness.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches WHERE request_id='dispatch-1'"
    ).fetchone()
    assert rows["n"] == 1


def test_owner_dispatch_fails_closed_when_owner_execution_setting_is_off(harness):
    harness.service.owner_execution.set_enabled(False)
    task = _submit(harness)

    with pytest.raises(ControlPlaneError) as error:
        harness.client.dispatch(
            "task-1",
            request_id="dispatch-disabled",
            task_state_version=task.state_version,
            execution_target_id="m3-sub",
        )

    assert error.value.status == 403
    assert error.value.code == "owner_initiated_execution_disabled"
    assert harness.client.owner_execution_settings().owner_initiated_execution_enabled is False
    rows = harness.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches WHERE request_id='dispatch-disabled'"
    ).fetchone()
    assert rows["n"] == 0


def test_owner_dispatch_rejects_stale_task_version(harness):
    _submit(harness)

    with pytest.raises(ControlPlaneError) as error:
        harness.client.dispatch(
            "task-1",
            request_id="dispatch-stale",
            task_state_version=99,
            execution_target_id="m3-sub",
        )

    assert error.value.status == 409
    assert error.value.code == "stale_task_state_version"
    dispatch = harness.client.get_dispatch("dispatch-stale")
    assert dispatch.status == "BLOCKED"
    assert dispatch.failure_code == "STALE_TASK_STATE_VERSION"


def test_owner_dispatch_request_id_conflicts_on_semantic_payload_changes(harness):
    task = _submit(harness)
    harness.client.dispatch(
        "task-1",
        request_id="dispatch-conflict",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
    )

    with pytest.raises(ControlPlaneError) as version_error:
        harness.client.dispatch(
            "task-1",
            request_id="dispatch-conflict",
            task_state_version=task.state_version + 1,
            execution_target_id="m3-sub",
        )
    assert version_error.value.status == 409
    assert version_error.value.code == "conflicting_dispatch_request_id"

    with pytest.raises(ControlPlaneError) as target_error:
        harness.client.dispatch(
            "task-1",
            request_id="dispatch-conflict",
            task_state_version=task.state_version,
            execution_target_id="different-target",
        )
    assert target_error.value.status == 409
    assert target_error.value.code == "conflicting_dispatch_request_id"

    other = _submit(harness, task_id="task-2", request_id="req-2")
    with pytest.raises(ControlPlaneError) as task_error:
        harness.client.dispatch(
            "task-2",
            request_id="dispatch-conflict",
            task_state_version=other.state_version,
            execution_target_id="m3-sub",
        )
    assert task_error.value.status == 409
    assert task_error.value.code == "conflicting_dispatch_request_id"


def test_owner_dispatch_rejects_client_supplied_authority_or_mode(harness):
    task = _submit(harness)
    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks/task-1/dispatch",
        headers={"content-type": "application/json"},
        body=json.dumps(
            {
                "request_id": "dispatch-authority",
                "task_state_version": task.state_version,
                "execution_target_id": "m3-sub",
                "authority": "OWNER_INITIATED_EXECUTION",
            }
        ).encode(),
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid_json_schema"}

    status, body = _raw_request(
        harness.socket_path,
        "POST",
        "/v1/tasks/task-1/dispatch",
        headers={"content-type": "application/json"},
        body=json.dumps(
            {
                "request_id": "dispatch-mode",
                "task_state_version": task.state_version,
                "execution_target_id": "m3-sub",
                "mode": "ACTIVE",
            }
        ).encode(),
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid_json_schema"}


def test_owner_dispatch_is_separate_from_routing_decisions(harness):
    task = _submit(harness)
    decision = {
        "decision_id": "route-before-dispatch",
        "request_id": "route-request",
        "mode": "SHADOW",
        "selected_execution_target_id": None,
        "fallback_reason": "quota confidence remained UNKNOWN",
    }
    harness.store.record_routing_decision(
        decision_id="route-before-dispatch",
        request_id="route-request",
        task_id="task-1",
        payload=decision,
    )

    dispatch = harness.client.dispatch(
        "task-1",
        request_id="dispatch-after-route",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
    )

    routing = harness.client.routing_decision("task-1")
    assert routing.decision_id == "route-before-dispatch"
    assert routing.request_id == "route-request"
    assert dispatch.request_id == "dispatch-after-route"
    assert harness.client.get_dispatch("dispatch-after-route").dispatch_id == dispatch.dispatch_id
    rows = harness.store.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE request_id='dispatch-after-route'"
    ).fetchone()
    assert rows["n"] == 0


def test_owner_dispatch_denies_catalog_only_target(harness):
    target = harness.service.registry.execution_targets["m3-sub"].model_copy(
        update={"enabled": True, "execution_verified": False}
    )
    harness.service.registry = harness.service.registry.model_copy(
        update={"execution_targets": {"m3-sub": target}}
    )
    task = _submit(harness)

    with pytest.raises(ControlPlaneError) as error:
        harness.client.dispatch(
            "task-1",
            request_id="dispatch-catalog-only",
            task_state_version=task.state_version,
            execution_target_id="m3-sub",
        )

    assert error.value.status == 409
    assert error.value.code == "execution_target_not_launchable"
    assert harness.client.get_task("task-1").state == TaskState.SUBMITTED.value
    dispatch = harness.client.get_dispatch("dispatch-catalog-only")
    assert dispatch.status == "BLOCKED"
    assert dispatch.failure_code == "EXECUTION_TARGET_NOT_LAUNCHABLE"
    events = harness.store.audit_events("task-1")
    assert any(event["event_type"] == "OWNER_DISPATCH_BLOCKED" for event in events)


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
            project_id=harness.project.project_id,
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
    payload = json.dumps({"task_id": "t" * 100, "request_id": "r", "intent": "x" * 70000}).encode()
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


def test_build_endpoint_reports_daemon_identity(harness):
    """The daemon must be able to state which commit produced it."""
    status, body = _raw_request(harness.socket_path, "GET", "/v1/build")

    assert status == 200
    payload = json.loads(body)
    assert set(payload) == {
        "commit_sha",
        "short_sha",
        "api_version",
        "configuration",
        "built_at",
    }
    assert payload["api_version"] == "v1"
    # Running from a checkout, identity resolves; either way it is never blank.
    assert payload["commit_sha"]
    if payload["commit_sha"] != "unknown":
        assert len(payload["commit_sha"]) == 40
        assert payload["short_sha"] == payload["commit_sha"][:7]


def test_build_endpoint_rejects_writes(harness):
    status, body = _raw_request(harness.socket_path, "POST", "/v1/build", body=b"{}")

    assert status == 405
    assert json.loads(body) == {"error": "method_not_allowed"}


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
    assert view.counts.verifying == 0
    assert view.counts.total == 1
    assert view.recent_tasks[0].task_id == "task-dash"
    assert view.basic_info.registered_projects == 1
    assert view.basic_info.discovered_providers == 1
    assert view.basic_info.running_tasks == 1
    assert view.basic_info.tasks_today >= 0
    assert view.task_trend
    assert any(bucket.submitted >= 1 for bucket in view.task_trend)
    assert any(
        slice.state == TaskState.RUNNING.value and slice.count == 1
        for slice in view.task_state_distribution
    )
    assert view.active_status.production_active == "DISABLED_BY_DESIGN"
    assert "explicit owner approval missing" in view.important_blockers
    assert any(risk.title == "Production automation is not authorized" for risk in view.risks)
    assert all(
        risk.raw_code is None or "credential" not in risk.raw_code.lower() for risk in view.risks
    )
    assert view.quota_history.retention_limit == 500
    assert view.quota_history.observations
    assert any(event.event_type == "TASK_STATE_CHANGED" for event in view.recent_events)

    raw = view.model_dump_json().lower()
    assert SECRET_MARKER not in raw
    assert "credential_ref" not in raw


def test_quota_history_is_bounded_and_deduplicated(harness):
    for index in range(505):
        harness.store.record_quota_observation(
            provider_id="minimax",
            quota_pool_id="pool",
            window_id="5h",
            observed_at=f"2026-08-31T00:{index:03d}:00Z",
            remaining_fraction=0.8,
            confidence="ESTIMATED",
            measurement_source="PROVIDER_API",
            reset_at=None,
            state="AVAILABLE",
        )

    harness.store.record_quota_observation(
        provider_id="minimax",
        quota_pool_id="pool",
        window_id="5h",
        observed_at="2026-08-31T00:504:00Z",
        remaining_fraction=0.8,
        confidence="ESTIMATED",
        measurement_source="PROVIDER_API",
        reset_at=None,
        state="AVAILABLE",
    )

    rows = harness.store.quota_observation_history(limit=600)
    assert len(rows) == 500
    assert sum(1 for row in rows if row["observed_at"] == "2026-08-31T00:504:00Z") == 1


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
    # No pid recorded → pid_alive stays None so the UI cannot pretend
    # either way.
    assert runs.runs[0].pid_alive is None

    single = harness.client.get_run("run-1")
    assert single.run_id == "run-1"


def test_run_view_pid_alive_true_for_running_process(harness) -> None:
    """A running RUNNING row with a real pid reports pid_alive=True."""
    import subprocess

    proc = subprocess.Popen(
        ["python", "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        harness.store.submit_task(
            task_id="task-pid-alive", request_id="req-pid-alive", intent="alive"
        )
        harness.store.start_run(
            run_id="run-alive",
            task_id="task-pid-alive",
            worker_id="glm",
            pid=proc.pid,
        )
        runs = harness.client.task_runs("task-pid-alive")
        assert len(runs.runs) == 1
        assert runs.runs[0].pid == proc.pid
        assert runs.runs[0].pid_alive is True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)


def test_run_view_pid_alive_false_when_process_is_gone(harness) -> None:
    """A RUNNING row whose pid is no longer in the OS reports pid_alive=False."""
    # Spawn a real child so we can obtain a pid, then kill it immediately
    # so the OS lookup definitively fails before the harness query.
    import subprocess

    proc = subprocess.Popen(
        ["python", "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=10)
    dead_pid = proc.pid

    harness.store.submit_task(task_id="task-pid-dead", request_id="req-pid-dead", intent="dead")
    harness.store.start_run(
        run_id="run-dead",
        task_id="task-pid-dead",
        worker_id="glm",
        pid=dead_pid,
    )
    runs = harness.client.task_runs("task-pid-dead")
    assert len(runs.runs) == 1
    assert runs.runs[0].pid == dead_pid
    assert runs.runs[0].pid_alive is False


def test_run_view_pid_alive_none_for_terminal_runs(harness) -> None:
    """A terminal run never probes the pid — pid_alive is None.

    The OS state is irrelevant once the run row is FAILED/FINISHED/etc.;
    the UI already knows the worker is gone from the status alone, and
    probing would just add noise.
    """

    harness.store.submit_task(
        task_id="task-pid-terminal", request_id="req-pid-terminal", intent="term"
    )
    harness.store.start_run(
        run_id="run-term",
        task_id="task-pid-terminal",
        worker_id="glm",
        pid=os.getpid(),  # any live pid, the test is about the status gate
    )
    harness.store.finish_run("run-term", status="FINISHED", result={"ok": True})
    runs = harness.client.task_runs("task-pid-terminal")
    assert len(runs.runs) == 1
    assert runs.runs[0].pid == os.getpid()
    assert runs.runs[0].status == "FINISHED"
    assert runs.runs[0].pid_alive is None


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
    assert target.execution_verified is True
    assert target.execution_verified_stale is False
    assert target.launch_authorized is True
    assert target.execution_verification_observed_at is None
    assert target.observed_availability is not None
    assert target.observed_availability.state == "UNKNOWN"
    assert target.observed_availability.confidence == EvidenceConfidence.UNKNOWN.value


def test_provider_health_keeps_expired_verified_history_but_denies_launch(harness) -> None:
    now = datetime.now(UTC)
    target = harness.service.registry.execution_targets["m3-sub"].model_copy(
        update={"execution_verified": False}
    )
    harness.service.registry = harness.service.registry.model_copy(
        update={"execution_targets": {"m3-sub": target}}
    )
    journal = ExecutionEvidenceJournal(harness.tmp_path / "execution-runtime")
    observed = now - timedelta(days=31)
    journal.append(
        build_execution_evidence(
            provider_id="minimax",
            execution_target_id="m3-sub",
            model_sku_id="m3",
            observed_at=observed,
            result=ExecutionVerificationOutcome.VERIFIED,
            reason_code="TEST_EXPIRED_VERIFIED",
        )
    )
    harness.service.execution_evidence_journal = journal

    target_view = harness.client.providers().providers[0].execution_targets[0]
    assert target_view.execution_verified is True
    assert target_view.execution_verified_stale is True
    assert target_view.launch_authorized is False
    assert target_view.execution_verification_observed_at == observed.isoformat()


def test_quota_is_connection_based_not_pool_based(harness):
    """Quota enumerates connected providers, never merely providers with pools.

    This harness has a catalog provider carrying a real quota pool but no
    connection registry, so the page must report NO_CONNECTED_PROVIDER rather
    than borrowing the catalog list.
    """

    view = harness.client.quota()
    assert view.state == "NO_CONNECTED_PROVIDER"
    assert view.providers == ()
    assert view.summary.connected_provider_count == 0
    assert view.summary.quota_observable_provider_count == 0
    # The catalog provider is still visible on the Providers page.
    assert harness.client.providers().providers[0].quota_pools


def test_active_status_fail_closed(harness):
    view = harness.client.active_status()
    assert view.production_active == "DISABLED_BY_DESIGN"
    assert view.authorized is False
    assert "explicit owner approval missing" in view.blocking_reasons
    assert "P3.5 Shadow evidence not accepted" in view.blocking_reasons


def test_approvals_are_listed_and_only_existing_records_can_be_resolved(harness):
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

    resolved = harness.client.resolve_approval(
        "approval-1",
        request_id="client-approval-1",
        approved=True,
    )
    assert resolved.status == "APPROVED"

    # A duplicate client message is idempotent; it cannot create a second
    # approval or second resolution transition.
    replayed = harness.client.resolve_approval(
        "approval-1",
        request_id="client-approval-1",
        approved=True,
    )
    assert replayed == resolved
    assert [
        event["event_type"]
        for event in harness.store.audit_events("task-a")
        if event["event_type"] == "APPROVAL_RESOLVED"
    ] == ["APPROVAL_RESOLVED"]

    with pytest.raises(ControlPlaneError) as conflicting:
        harness.client.resolve_approval(
            "approval-1",
            request_id="client-approval-opposite",
            approved=False,
        )
    assert conflicting.value.status == 409
    assert conflicting.value.code == "approval_already_resolved"

    # The client can never create a new approval; only the host Safety Kernel
    # can put an approval record into PENDING.
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

    assert (
        cli_main(
            [
                socket_arg,
                socket_value,
                "submit",
                "--task-id",
                "cli-1",
                "--request-id",
                "cli-req-1",
                "--project-id",
                harness.project.project_id,
                "--intent",
                "cli driven task",
            ]
        )
        == 0
    )
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
        repo = _make_repo(tmp_path / "wired-project")
        project = client.register_project(path=str(repo), display_name="Wired")
        view = client.submit(
            task_id="wire-1",
            request_id="wire-req-1",
            project_id=project.project_id,
            intent="wired",
        )
        assert view.state == TaskState.SUBMITTED.value
        status = client.active_status()
        assert status.production_active == "DISABLED_BY_DESIGN"
        assert status.authorized is False
    finally:
        server.stop()
        control_service.store.close()
        shutil.rmtree(socket_dir, ignore_errors=True)


def test_opencode_runtime_resolves_the_canonical_install_when_path_is_minimal(
    tmp_path, monkeypatch
):
    """A GUI-launched daemon sees only /usr/bin:/bin — the binary found by
    discovery at ~/.opencode/bin/opencode must still count as available."""

    from personal_ai_orchestrator import control_api

    # Simulate the minimal launch environment: nothing on PATH.
    monkeypatch.setattr(control_api.shutil, "which", lambda name: None)

    home = tmp_path / "gui-home"
    bin_dir = home / ".opencode" / "bin"
    bin_dir.mkdir(parents=True)
    binary = bin_dir / "opencode"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(control_api.Path, "home", staticmethod(lambda: home))

    assert control_api._opencode_binary_available() is True

    # Not executable -> not launchable, regardless of presence.
    binary.chmod(0o644)
    assert control_api._opencode_binary_available() is False

    # Missing entirely -> unavailable.
    binary.unlink()
    assert control_api._opencode_binary_available() is False


def test_opencode_runtime_prefers_path_resolution(tmp_path, monkeypatch):
    from personal_ai_orchestrator import control_api

    monkeypatch.setattr(control_api.shutil, "which", lambda name: "/usr/local/bin/opencode")
    # Even with no home install, PATH resolution wins.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setattr(control_api.Path, "home", staticmethod(lambda: empty_home))
    assert control_api._opencode_binary_available() is True
