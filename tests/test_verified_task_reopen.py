"""An owner can reopen accepted work without losing a byte of its history."""

from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.project_workflow import manual_workflow, task_patch
from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.verification_history import verification_history
from tests.test_project_workflow import _harness, _retry


def reopen_request(service, **overrides):
    return _retry(service, request_id="reopen-1", operation="REOPEN") | overrides


def readonly_result(service):
    return handle_local_client_v2(
        service,
        {
            "protocol_version": 2,
            "operation": "DETAIL",
            "request_id": "read-reopen",
            "task_id": "task-1",
        },
    )["payload"]["result"]


def test_reopen_retains_owner_edits_and_attestation_then_records_a_new_generation(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        harness.run(dispatch)
        accepted = service.verification_report("task-1")
        evidence = service.verification_journal.path_for(accepted.evidence_id)
        original_evidence = evidence.read_bytes()
        old_patch = task_patch(service, "task-1")
        root = Path(service.store.get_workspace("task-1").worktree_path)
        (root / "hello.txt").write_text("Owner review: retain this correction.\n")
        assert service.verification_report("task-1").status == "VERIFICATION_STALE"
        workflow = manual_workflow(service, "task-1")
        assert workflow["retry_available"] and workflow["recovery_operation"] == "REOPEN"
        before = service.store.get_task("task-1")
        request = reopen_request(service)
        response = handle_local_client_v2(service, request)
        assert response["payload"]["launch_performed"] is False
        assert service.store.get_task("task-1").state is TaskState.READY
        assert service.store.get_task("task-1").state_version == before.state_version + 2
        assert (root / "hello.txt").read_text() == "Owner review: retain this correction.\n"
        current = service.verification_report("task-1")
        assert current.status == "NOT_VERIFIED" and current.result is None
        assert readonly_result(service)["verification"]["status"] != "VERIFIED"
        assert handle_local_client_v2(service, request) == response
        assert service.store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        history = verification_history(service, "task-1")
        assert history["entries"][0]["status"] == "HISTORICAL"
        selected = verification_history(service, "task-1", accepted.evidence_id)["result"]
        assert selected["verification"]["status"] == "HISTORICAL_VERIFIED"
        assert selected["binding"]["patch_sha256"] == old_patch["sha256"]
        historical = task_patch(service, "task-1", evidence_id=accepted.evidence_id)
        assert historical["patch_base64"] == old_patch["patch_base64"]
        assert historical["workspace_status"] == "HISTORICAL"
        task = service.store.get_task("task-1")
        service.store.reserve_owner_dispatch(
            dispatch_id="second-dispatch",
            request_id="second-request",
            task_id="task-1",
            task_state_version=task.state_version,
            execution_target_id=task.manual_execution_target_id,
            authority="OWNER_INITIATED_EXECUTION",
        )
        harness.run("second-request")
        assert service.store.get_task("task-1").state is TaskState.VERIFIED
        assert evidence.read_bytes() == original_evidence
        entries = verification_history(service, "task-1")["entries"]
        assert len(entries) == 2
        assert [entry["status"] for entry in entries] == ["CURRENT", "HISTORICAL"]
        assert entries[0]["evidence_id"] != accepted.evidence_id
        assert task_patch(service, "task-1", evidence_id=accepted.evidence_id) == historical
        assert harness.main_unchanged()
    finally:
        service.store.close()


@pytest.mark.parametrize(
    "gate",
    ["version", "epoch", "store", "owner", "writer", "cleanup", "receipt", "missing-worktree"],
)
def test_reopen_denials_preserve_all_task_and_workspace_state(tmp_path, gate, monkeypatch):
    harness, service, dispatch = _harness(tmp_path)
    try:
        harness.run(dispatch)
        request = reopen_request(service)
        if gate == "version":
            request["task_state_version"] -= 1
        elif gate == "epoch":
            request["expected_process_epoch"] = "0" * 32
        elif gate == "store":
            request["expected_store_id"] = "0" * 32
        elif gate == "owner":
            service.owner_execution.set_enabled(False)
        elif gate == "writer":
            service.store.connection.execute("UPDATE workspaces SET writer_token='held'")
        elif gate == "cleanup":
            service.store.connection.execute("UPDATE worker_attempts SET cleanup_state='UNKNOWN'")
        elif gate == "receipt":

            def invalid_cleanup(*args):
                raise ValueError("cleanup facts do not match the immutable attempt")

            monkeypatch.setattr(service.store, "_check_worker_cleanup_facts", invalid_cleanup)
        elif gate == "missing-worktree":
            service.store.connection.execute("UPDATE workspaces SET branch='unrelated-branch'")
        before = service.store.get_task("task-1")
        audits = service.store.audit_events("task-1")
        with pytest.raises(ControlPlaneError):
            handle_local_client_v2(service, request)
        assert service.store.get_task("task-1") == before
        assert service.store.audit_events("task-1") == audits
        assert (
            service.store.connection.execute("SELECT count(*) FROM manual_task_reopens").fetchone()[
                0
            ]
            == 0
        )
    finally:
        service.store.close()


def test_worker_failure_after_reopen_cannot_reuse_the_old_passing_checks(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        harness.run(dispatch)
        evidence_id = service.verification_report("task-1").evidence_id
        old = task_patch(service, "task-1")
        handle_local_client_v2(service, reopen_request(service))
        harness.worker_bin.write_text("#!/bin/sh\nexit 70\n")
        task = service.store.get_task("task-1")
        service.store.reserve_owner_dispatch(
            dispatch_id="second-dispatch",
            request_id="second-request",
            task_id="task-1",
            task_state_version=task.state_version,
            execution_target_id=task.manual_execution_target_id,
            authority="OWNER_INITIATED_EXECUTION",
        )
        harness.run("second-request")
        current = service.verification_report("task-1")
        assert current.status == "NOT_VERIFIED" and current.result is None
        assert readonly_result(service)["tests"] == []
        assert task_patch(service, "task-1", evidence_id=evidence_id)["sha256"] == old["sha256"]
        assert verification_history(service, "task-1")["entries"][0]["status"] == "HISTORICAL"
    finally:
        service.store.close()


@pytest.mark.parametrize("identifier", ["../other", "verify-" + "f" * 24])
def test_history_selector_needs_this_tasks_attested_evidence(tmp_path, identifier):
    harness, service, dispatch = _harness(tmp_path)
    try:
        harness.run(dispatch)
        with pytest.raises(ControlPlaneError):
            task_patch(service, "task-1", evidence_id=identifier)
    finally:
        service.store.close()


def test_corrupt_historical_file_cannot_be_exported(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        harness.run(dispatch)
        current = service.verification_report("task-1")
        service.verification_journal.path_for(current.evidence_id).write_bytes(b"{}")
        assert verification_history(service, "task-1")["entries"][0]["available"] is False
        with pytest.raises(ControlPlaneError):
            task_patch(service, "task-1", evidence_id=current.evidence_id)
        with pytest.raises(ControlPlaneError):
            handle_local_client_v2(service, reopen_request(service))
    finally:
        service.store.close()
