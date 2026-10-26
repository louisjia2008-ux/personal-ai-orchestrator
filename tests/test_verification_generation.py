"""A retry failing before verification must not inherit an earlier report."""

import shlex
import sys

import pytest

from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.project_workflow import project_verification
from personal_ai_orchestrator.safety_kernel import TaskState
from tests.test_project_workflow import _harness, _retry


@pytest.mark.parametrize("failure", ["worker", "preparation"])
def test_retry_before_verification_never_reuses_previous_failed_checks(tmp_path, failure):
    harness, service, dispatch = _harness(tmp_path)
    try:
        config = {
            "command_lines": [shlex.join([sys.executable, "-c", "raise SystemExit(1)"])],
            "allowed_paths": ["hello.txt"],
        }
        project_verification(service, "project-main", config)
        harness.run(dispatch)
        first = service.verification_report("task-1")
        assert first.status == "FAILED_VERIFICATION"
        assert first.result is not None
        path = service.verification_journal.path_for(first.evidence_id)
        historical_bytes = path.read_bytes()
        response = handle_local_client_v2(service, _retry(service))
        assert response["payload"]["launch_performed"] is False
        if failure == "worker":
            harness.worker_bin.write_text("#!/bin/sh\nexit 70\n")
        else:
            config["preparation_command_lines"] = [
                shlex.join([sys.executable, "-c", "raise SystemExit(7)"])
            ]
            project_verification(service, "project-main", config)
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
        assert service.store.get_task("task-1").state is TaskState.BLOCKED
        before = service.store.audit_events("task-1")
        current = service.verification_report("task-1")
        assert current.status == "NOT_VERIFIED"
        assert current.evidence_id is None
        assert current.result is None
        assert current.failure_reason is None
        assert service.task_detail("task-1").verification.status == "NOT_VERIFIED"
        assert service.store.audit_events("task-1") == before
        assert path.read_bytes() == historical_bytes
    finally:
        service.store.close()
