"""Project-owned checks and a real process failure/retry without dropping edits."""

import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.control_api import ControlPlaneError, ControlPlaneService
from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.project_preparation import preparation_report
from personal_ai_orchestrator.project_workflow import (
    ProjectVerificationRequest,
    load_project_verifier,
    manual_workflow,
    probe_execution_target,
    project_verification,
    task_patch,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_dispatch_executor import ExecutorHarness


def _harness(tmp_path):
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()
    store = SafetyKernelStore(harness.state_db)
    # The product creates this binding at submission, before any dispatch.
    store.connection.execute(
        "UPDATE tasks SET scheduling_policy='MANUAL',manual_execution_target_id=?",
        ("zai-coding-plan-glm-5.3",),
    )
    service = ControlPlaneService(
        registry=harness.registry,
        store=store,
        dispatch_executor=harness.executor,
        runtime_availability={"zai-coding-plan-glm-5.3": True},
        owner_execution=OwnerExecutionSettings(tmp_path / "owner.json", initial=True),
        verification_journal=harness.executor._verification_journal,
    )
    return harness, service, request_id


def _retry(service, request_id="retry-1", **overrides):
    task = service.store.get_task("task-1")
    workflow = manual_workflow(service, task.task_id)
    return {
        "protocol_version": 2,
        "operation": "RETRY",
        "request_id": request_id,
        "task_id": task.task_id,
        "task_state_version": task.state_version,
        "prior_dispatch_id": workflow["prior_dispatch_id"],
        "expected_store_id": service.store.store_id,
        "expected_process_epoch": service.process_epoch,
    } | overrides


def test_project_checks_override_default_and_retry_keeps_patch(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        project_verification(
            service,
            "project-main",
            {
                "command_lines": ["test -f hello.txt"],
                "allowed_paths": ["README.md"],
            },
        )
        harness.executor.config = replace(harness.executor.config, verifier_profile=None)
        harness.run(dispatch)
        assert service.store.get_task("task-1").state is TaskState.BLOCKED
        failed_report = service.verification_report("task-1")
        assert failed_report.result.changed_paths == ("hello.txt",)
        assert failed_report.result.unexpected_paths == ("hello.txt",)
        patch = task_patch(service, "task-1")
        assert "+Personal AI Orchestrator real worker acceptance." in patch["patch"]
        workspace = service.store.get_workspace("task-1")
        content = Path(workspace.worktree_path, "hello.txt").read_bytes()
        project_verification(
            service,
            "project-main",
            {
                "command_lines": ["test -f hello.txt"],
                "allowed_paths": ["hello.txt"],
            },
        )
        request = _retry(service)
        result = handle_local_client_v2(service, request)
        assert result["payload"]["launch_performed"] is False
        assert service.store.get_task("task-1").state is TaskState.READY
        assert Path(workspace.worktree_path, "hello.txt").read_bytes() == content
        assert handle_local_client_v2(service, request) == result
        assert service.store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        task = service.store.get_task("task-1")
        service.store.reserve_owner_dispatch(
            dispatch_id="owner-dispatch-dispatch-2",
            request_id="dispatch-2",
            task_id="task-1",
            task_state_version=task.state_version,
            execution_target_id=task.manual_execution_target_id,
            authority="OWNER_INITIATED_EXECUTION",
        )
        harness.run("dispatch-2")
        assert service.store.get_task("task-1").state is TaskState.VERIFIED
        assert service.verification_report("task-1").failure_reason is None
        assert service.store.get_workspace("task-1").worktree_path == workspace.worktree_path
        assert task_patch(service, "task-1")["sha256"] == patch["sha256"]
        assert harness.main_unchanged()
    finally:
        service.store.close()


def test_dependency_failure_stops_before_model_and_retry_prepares_same_worktree(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        config = {
            "command_lines": ["test -f hello.txt"],
            "preparation_command_lines": [
                shlex.join([sys.executable, "-c", "raise SystemExit(7)"])
            ],
            "allowed_paths": ["hello.txt", "dependency-ready"],
        }
        project_verification(service, "project-main", config)
        harness.run(dispatch)
        report = preparation_report(service.store, "task-1")
        assert report["status"] == "FAILED"
        assert report["cleanup_state"] == "CONFIRMED"
        assert report["stages"][0]["returncode"] == 7
        assert service.store.get_task("task-1").state is TaskState.BLOCKED
        assert service.store.get_workspace("task-1").writer_token is None
        assert service.store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert (
            service.store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0]
            == 0
        )
        workspace = service.store.get_workspace("task-1").worktree_path
        assert manual_workflow(service, "task-1")["retry_available"]
        config["preparation_command_lines"] = [
            shlex.join(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('dependency-ready').write_text('prepared')",
                ]
            )
        ]
        project_verification(service, "project-main", config)
        handle_local_client_v2(service, _retry(service))
        assert service.store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        task = service.store.get_task("task-1")
        service.store.reserve_owner_dispatch(
            dispatch_id="owner-dispatch-dispatch-2",
            request_id="dispatch-2",
            task_id="task-1",
            task_state_version=task.state_version,
            execution_target_id=task.manual_execution_target_id,
            authority="OWNER_INITIATED_EXECUTION",
        )
        harness.run("dispatch-2")
        assert service.store.get_task("task-1").state is TaskState.VERIFIED
        assert service.store.get_workspace("task-1").worktree_path == workspace
        assert preparation_report(service.store, "task-1")["status"] == "PASSED"
        assert harness.main_unchanged()
    finally:
        service.store.close()


def test_dependency_timeout_is_cleaned_and_unconfirmed_cleanup_blocks_retry(tmp_path):
    harness, service, dispatch = _harness(tmp_path)
    try:
        project_verification(
            service,
            "project-main",
            {
                "command_lines": ["test -f hello.txt"],
                "preparation_command_lines": [
                    shlex.join(
                        [
                            sys.executable,
                            "-c",
                            "import time; print('starting', flush=True); time.sleep(30)",
                        ]
                    )
                ],
                "timeout_seconds": 1,
            },
        )
        harness.run(dispatch)
        report = preparation_report(service.store, "task-1")
        assert report["status"] == "FAILED"
        assert report["stages"][0]["error"] == "TimeoutError"
        assert report["stages"][0]["cleanup"]["child_reaped"] is True
        assert report["stages"][0]["cleanup"]["group_empty"] is True
        assert "starting" in report["stages"][0]["stdout"]
        assert manual_workflow(service, "task-1")["retry_available"]
        service.store.connection.execute(
            "UPDATE task_preparations SET cleanup_state='UNKNOWN',status='RUNNING'"
        )
        assert preparation_report(service.store, "task-1")["status"] == "UNKNOWN"
        assert manual_workflow(service, "task-1")["blocking_reason"] == (
            "preparation_cleanup_unconfirmed"
        )
        with pytest.raises(ControlPlaneError):
            handle_local_client_v2(service, _retry(service))
    finally:
        service.store.close()


@pytest.mark.parametrize("gate", ["cleanup", "version", "epoch", "writer", "authority"])
def test_retry_fails_closed_without_mutating_task_or_workspace(tmp_path, gate):
    harness, service, dispatch = _harness(tmp_path)
    try:
        project_verification(
            service,
            "project-main",
            {
                "command_lines": ["test -f missing-file"],
                "allowed_paths": ["hello.txt"],
            },
        )
        harness.run(dispatch)
        task = service.store.get_task("task-1")
        request = _retry(service)
        if gate == "cleanup":
            service.store.connection.execute("UPDATE worker_attempts SET cleanup_state='UNKNOWN'")
        elif gate == "version":
            request["task_state_version"] -= 1
        elif gate == "epoch":
            request["expected_process_epoch"] = "0" * 32
        elif gate == "writer":
            service.store.connection.execute("UPDATE workspaces SET writer_token='held'")
        elif gate == "authority":
            service.owner_execution.set_enabled(False)
        with pytest.raises(ControlPlaneError):
            handle_local_client_v2(service, request)
        assert service.store.get_task("task-1") == task
        assert Path(service.store.get_workspace("task-1").worktree_path, "hello.txt").is_file()
    finally:
        service.store.close()


def test_profile_persists_and_remove_project_cascades(tmp_path):
    harness, service, _ = _harness(tmp_path)
    try:
        assert not project_verification(service, "project-main")["configured"]
        project_verification(
            service,
            "project-main",
            {
                "command_lines": ["python -c 'print(123)'"],
                "allowed_paths": ["docs"],
            },
        )
        reopened = SafetyKernelStore(harness.state_db)
        try:
            profile = load_project_verifier(reopened, "project-main")
            assert profile.commands[0].argv == ("python", "-c", "print(123)")
        finally:
            reopened.close()
        project_verification(service, "project-main", {"command_lines": []})
        assert not project_verification(service, "project-main")["configured"]
        service.store.remove_project("project-main")
        assert load_project_verifier(service.store, "project-main") is None
    finally:
        service.store.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"command_lines": ["\x00"]},
        {"command_lines": ["'unterminated"]},
        {"command_lines": ["echo yes"], "allowed_paths": ["../outside"]},
    ],
)
def test_bad_configuration_rejected(payload):
    with pytest.raises(ValidationError):
        ProjectVerificationRequest.model_validate(payload)


def test_model_probe_derives_model_and_runtime_on_host(tmp_path, monkeypatch):
    harness, service, _ = _harness(tmp_path)
    from personal_ai_orchestrator import execution_probe
    from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
    from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor

    calls = []

    def record(**kwargs):
        calls.append(kwargs)
        return {
            "execution_target_id": kwargs["execution_target_id"],
            "result": "UNKNOWN",
            "reason_code": "PROBE_EXIT_1",
        }

    service.execution_evidence_journal = ExecutionEvidenceJournal(harness.runtime_root)
    service.dispatch_executor = RuntimeDispatchExecutor(
        state_db=harness.state_db,
        registry_provider=lambda: harness.registry,
        executors={"opencode": harness.executor},
    )
    monkeypatch.setattr(execution_probe, "run_execution_probe", record)
    try:
        result = probe_execution_target(service, "zai-coding-plan-glm-5.3")
        assert result["result"] == "UNKNOWN"
        assert len(calls) == 1
        assert calls[0]["opencode_bin"] == harness.executor.config.opencode_bin
        assert calls[0]["model_sku_id"] == "zai-coding-plan/glm-5.3"
        assert calls[0]["timeout_seconds"] == 90
        assert calls[0]["cwd"] != harness.main_repo
        assert not calls[0]["cwd"].exists()
        service.owner_execution.set_enabled(False)
        with pytest.raises(ControlPlaneError, match="owner_initiated_execution_disabled"):
            probe_execution_target(service, "zai-coding-plan-glm-5.3")
        assert len(calls) == 1
    finally:
        service.store.close()


def test_interrupted_manual_task_uses_canonical_recovery_operation(tmp_path):
    from tests.test_local_client_recovery import recovery_request
    from tests.test_owner_dispatch_recovery import _recoverable

    _, service, _ = _harness(tmp_path)
    service.store.close()
    recovery_root = tmp_path / "interrupted"
    recovery_root.mkdir()
    service.store = _recoverable(recovery_root)
    try:
        view = manual_workflow(service, "task")
        assert view["retry_available"] is True
        assert view["recovery_operation"] == "RECOVER"
        response = handle_local_client_v2(service, recovery_request(service))
        assert response["payload"]["launch_performed"] is False
        assert service.store.get_task("task").state is TaskState.READY
    finally:
        service.store.close()
