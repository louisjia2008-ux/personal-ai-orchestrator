"""Bounded result delivery uses host evidence, never a worker's final-answer claim."""

from __future__ import annotations

import io
import json
import os
from hashlib import sha256

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator import client_cli
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    record_worker_exit,
)
from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.result_delivery import (
    AUDIT_CHARACTER_LIMIT,
    AUDIT_EVENT_LIMIT,
    EVIDENCE_BYTE_LIMIT,
    FILE_LIMIT,
    GOAL_LIMIT,
    TEST_LIMIT,
)
from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.verifier import VerificationResult, VerificationStage
from tests.test_local_client_v2 import _http, counts, request, start_request, submit
from tests.test_local_client_v2 import service as service


def transition(service, state):
    current = service.store.get_task("task-1")
    service.store.transition_task(
        "task-1", state, expected_version=current.state_version, reason="fixture"
    )


def worker_finished(service, *, intent="Implement the requested fixture", exit_code=0):
    submit(service, intent=intent)
    transition(service, TaskState.READY)
    transition(service, TaskState.RUNNING)
    service.store.start_run(run_id="run-1", task_id="task-1", worker_id="fixture")
    record_worker_exit(
        service.store,
        task_id="task-1",
        run_id="run-1",
        exit_code=exit_code,
        worker_result={
            "exit_code": exit_code,
            "stdout_tail": "ALL COMPLETE secret=PRIVATE_PROVIDER_PAYLOAD /private/log",
            "stderr_tail": "PRIVATE_PROVIDER_PAYLOAD",
        },
    )


def verified(
    service, *, intent="Implement the requested fixture", main_repo_unchanged=True, **fields
):
    worker_finished(service, intent=intent)
    begin_verification(service.store, task_id="task-1")
    evidence = VerificationResult(
        **(
            {
                "profile": "host-profile",
                "passed": True,
                "changed_paths": ("src/fixture.py",),
                "unexpected_paths": (),
                "stages": (
                    VerificationStage(
                        name="unit tests",
                        argv=("/private/python", "-m", "pytest"),
                        returncode=0,
                        stdout="PRIVATE_VERIFIER_OUTPUT",
                        stderr="PRIVATE_VERIFIER_OUTPUT",
                    ),
                ),
                "evidence_id": "verify-" + "a" * 24,
            }
            | fields
        )
    )
    service.verification_journal.append(evidence)
    apply_verification_result(
        service.store,
        task_id="task-1",
        result=evidence,
        evidence_journal=service.verification_journal,
        shadow_main_repo_unchanged=main_repo_unchanged,
    )
    return evidence


def detail(service):
    return handle_local_client_v2(service, request(service, "DETAIL", task_id="task-1"))["payload"]


def continuation(service):
    return handle_local_client_v2(service, request(service, "CONTINUATION", task_id="task-1"))[
        "payload"
    ]


def digest(body):
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_host_report_complete_immutable_and_legacy_report_agree(service):
    evidence = verified(service)
    before = counts(service)
    result = detail(service)["result"]
    assert result["schema_version"] == 1
    assert result["kind"] == "HOST_VERIFICATION_REPORT"
    assert result["completeness"] == "COMPLETE"
    assert result["verification"] == {"status": "VERIFIED", "evidence_id": evidence.evidence_id}
    assert result["binding"]["task_id"] == "task-1"
    assert result["binding"]["run_id"] == "run-1"
    assert result["binding"]["base_commit"] == service.store.get_task("task-1").base_sha
    assert result["binding"]["result_commit"] is None
    assert result["changed_files"] == ["src/fixture.py"]
    assert result["tests"] == [{"name": "unit tests", "status": "PASSED"}]
    assert result["result_id"] == "result-" + digest(
        {key: value for key, value in result.items() if key != "result_id"}
    )
    path = service.verification_journal.path_for(evidence.evidence_id)
    assert result["artifacts"][0]["sha256"] == sha256(path.read_bytes()).hexdigest()
    assert result["binding"]["verification_evidence_sha256"] == result["artifacts"][0]["sha256"]
    assert result == detail(service)["result"]
    assert service.verification_report("task-1").result == evidence
    assert counts(service) == before
    rendered = json.dumps(result)
    for forbidden in ("PRIVATE", "/private", "argv", "stdout", "stderr", "result_json"):
        assert forbidden not in rendered


def test_worker_exit_zero_is_missing_and_never_answer_complete(service):
    worker_finished(service)
    result = detail(service)
    assert result["task"]["state"] == "WORKER_FINISHED"
    assert result["answer_completeness"] == "MISSING"
    assert result["result"]["tests"] == []
    assert "PRIVATE_PROVIDER_PAYLOAD" not in json.dumps(result)
    assert continuation(service)["continuation"]["completed"] == []


def test_verification_in_progress_never_complete(service):
    worker_finished(service)
    begin_verification(service.store, task_id="task-1")
    result = detail(service)
    assert result["verification"]["status"] == "IN_PROGRESS"
    assert result["answer_completeness"] == "MISSING"


@pytest.mark.parametrize("state", [TaskState.FAILED, TaskState.CANCELLED])
def test_failure_and_cancel_are_explicit(service, state):
    worker_finished(service)
    transition(service, state)
    assert detail(service)["answer_completeness"] == "FAILED"


def test_worker_failure_is_explicit_without_output(service):
    worker_finished(service, exit_code=1)
    result = detail(service)
    assert result["answer_completeness"] == "FAILED"
    assert result["verification"]["status"] == "NOT_OBSERVED"
    assert "PRIVATE_PROVIDER_PAYLOAD" not in json.dumps(result)


def test_failed_verification_keeps_legacy_truth_without_raw_failure_reason(service):
    verified(service, passed=False, failure_reason="secret=DO_NOT_EXPORT /private/failure")
    result = detail(service)
    assert result["verification"]["status"] == "FAILED_VERIFICATION"
    assert result["answer_completeness"] == "FAILED"
    assert "DO_NOT_EXPORT" not in json.dumps(result)
    assert service.verification_report("task-1").status == "FAILED_VERIFICATION"


def test_interrupted_verification_is_not_a_failed_verifier_claim(service):
    worker_finished(service)
    begin_verification(service.store, task_id="task-1")
    transition(service, TaskState.BLOCKED)
    result = detail(service)
    assert result["answer_completeness"] == "FAILED"
    assert result["verification"]["status"] == "NOT_OBSERVED"
    assert "VERIFICATION_OUTCOME_NOT_ESTABLISHED" in result["result"]["limitations"]
    assert continuation(service)["continuation"]["completed"] == []


def test_main_repository_safety_block_is_not_a_failed_verifier_claim(service):
    verified(service, main_repo_unchanged=False)
    result = detail(service)
    assert result["answer_completeness"] == "FAILED"
    assert result["verification"]["status"] == "NOT_OBSERVED"
    assert "VERIFICATION_OUTCOME_NOT_ESTABLISHED" in result["result"]["limitations"]


def test_failed_verification_from_earlier_run_is_not_attached_to_later_run(service):
    verified(service, passed=False, failure_reason="test failed")
    assert detail(service)["verification"]["status"] == "FAILED_VERIFICATION"
    service.store.start_run(run_id="later-run", task_id="task-1", worker_id="fixture")
    assert detail(service)["verification"]["status"] == "NOT_OBSERVED"


@pytest.mark.parametrize("missing", ["journal", "file", "audit", "run_started", "run_finished"])
def test_missing_authority_fails_closed(service, missing):
    evidence = verified(service)
    if missing == "journal":
        service.verification_journal = None
    elif missing == "file":
        service.verification_journal.path_for(evidence.evidence_id).unlink()
    else:
        event_type = {
            "audit": "TASK_STATE_CHANGED",
            "run_started": "RUN_STARTED",
            "run_finished": "RUN_FINISHED",
        }[missing]
        service.store.connection.execute(
            "DELETE FROM audit_events WHERE event_type=?", (event_type,)
        )
    result = detail(service)
    assert result["answer_completeness"] == "MISSING"
    assert result["verification"]["status"] == "VERIFIED_EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "corruption", ["id", "json", "base", "run", "later_run", "symlink", "fifo"]
)
def test_wrong_or_untrusted_binding_fails_closed(service, tmp_path, corruption):
    evidence = verified(service)
    path = service.verification_journal.path_for(evidence.evidence_id)
    connection = service.store.connection
    if corruption == "id":
        path.write_text(
            evidence.model_copy(update={"evidence_id": "verify-" + "b" * 24}).model_dump_json()
        )
    elif corruption == "json":
        path.write_text('{"raw":"PRIVATE_PROVIDER_PAYLOAD"')
    elif corruption == "base":
        connection.execute("UPDATE tasks SET base_sha=?", ("b" * 40,))
    elif corruption == "run":
        connection.execute("UPDATE runs SET run_id='other-run'")
    elif corruption == "later_run":
        service.store.start_run(run_id="later-run", task_id="task-1", worker_id="fixture")
    elif corruption == "symlink":
        target = tmp_path / "sensitive.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        path.unlink()
        os.mkfifo(path)
    result = detail(service)
    assert result["answer_completeness"] == "MISSING"
    assert result["result"]["artifacts"] == []
    assert "PRIVATE_PROVIDER_PAYLOAD" not in json.dumps(result)


def test_directory_symlink_is_not_followed(service, tmp_path):
    verified(service)
    path = service.verification_journal.directory
    target = tmp_path / "moved-evidence"
    path.rename(target)
    path.symlink_to(target, target_is_directory=True)
    assert detail(service)["answer_completeness"] == "MISSING"


@pytest.mark.parametrize("change", ["paths", "stage", "whitespace"])
def test_same_id_modified_content_does_not_match_accepted_evidence(service, change):
    evidence = verified(service)
    original = detail(service)["result"]
    path = service.verification_journal.path_for(evidence.evidence_id)
    if change == "paths":
        changed = evidence.model_copy(update={"changed_paths": ("src/other.py",)})
        path.write_text(changed.model_dump_json(indent=2) + "\n")
    elif change == "stage":
        changed = evidence.model_copy(
            update={
                "stages": (VerificationStage(name="forged check", argv=("check",), returncode=0),)
            }
        )
        path.write_text(changed.model_dump_json(indent=2) + "\n")
    else:
        path.write_text(path.read_text() + "\n")
    result = detail(service)["result"]
    assert result["completeness"] == "MISSING"
    assert result["verification"]["status"] == "VERIFIED_EVIDENCE_UNAVAILABLE"
    assert "EVIDENCE_DIGEST_MISMATCH" in result["limitations"]
    assert result["artifacts"] == []
    assert result["result_id"] != original["result_id"]


@pytest.mark.parametrize("digest", [None, "", "not-a-digest", "a" * 65])
def test_legacy_or_malformed_digest_attestation_remains_missing(service, digest):
    verified(service)
    service.store.connection.execute(
        "UPDATE audit_events SET payload_json=json_set(payload_json, "
        "'$.verification_evidence_sha256', ?) WHERE event_type='TASK_STATE_CHANGED' "
        "AND json_extract(payload_json,'$.to')='VERIFIED'",
        (digest,),
    )
    result = detail(service)["result"]
    assert result["completeness"] == "MISSING"
    assert "EVIDENCE_DIGEST_UNATTESTED" in result["limitations"]
    assert result["artifacts"] == []
    assert service.verification_report("task-1").status == "VERIFIED"


def test_oversized_audit_fields_cannot_establish_binding(service):
    verified(service)
    service.store.connection.execute(
        "UPDATE audit_events SET payload_json=json_set(payload_json, '$.extra', ?) "
        "WHERE event_type='RUN_FINISHED'",
        ("x" * AUDIT_CHARACTER_LIMIT,),
    )
    assert detail(service)["answer_completeness"] == "MISSING"


def test_old_verification_outside_bounded_audit_window_is_not_promoted(service):
    verified(service)
    for _ in range(AUDIT_EVENT_LIMIT):
        service.store._audit("task-1", "TASK_STATE_CHANGED", {"from": "UNKNOWN", "to": "UNKNOWN"})
    assert detail(service)["answer_completeness"] == "MISSING"


def test_oversized_evidence_is_truncated_without_loading(service):
    evidence = verified(service)
    service.verification_journal.path_for(evidence.evidence_id).write_bytes(
        b"PRIVATE_PROVIDER_PAYLOAD" + b" " * EVIDENCE_BYTE_LIMIT
    )
    result = detail(service)
    assert result["answer_completeness"] == "TRUNCATED"
    assert result["result"]["artifacts"] == []
    assert "PRIVATE_PROVIDER_PAYLOAD" not in json.dumps(result)


def test_file_and_test_bounds_report_omissions(service):
    verified(
        service,
        changed_paths=tuple(f"src/file{i}.py" for i in range(FILE_LIMIT + 1)),
        stages=tuple(
            VerificationStage(name=f"check {i}", argv=("check",), returncode=0)
            for i in range(TEST_LIMIT + 1)
        ),
    )
    result = detail(service)["result"]
    assert result["completeness"] == "TRUNCATED"
    assert len(result["changed_files"]) == FILE_LIMIT
    assert len(result["tests"]) == TEST_LIMIT
    assert result["truncated"] == {"goal": False, "changed_files": True, "tests": True}
    assert len(json.dumps(result)) < 40000


@pytest.mark.parametrize(
    "unsafe",
    [
        "/private/file",
        "../escape",
        "src/../escape",
        "C:\\Users\\private",
        ".codex/sessions/a",
        ".env",
        "key=PRIVATE_SECRET",
        "private\npath",
        "a" * 257,
    ],
)
def test_unsafe_changed_file_is_omitted(service, unsafe):
    verified(service, changed_paths=(unsafe,))
    result = detail(service)["result"]
    assert result["changed_files"] == []
    assert result["completeness"] == "TRUNCATED"


@pytest.mark.parametrize(
    "unsafe", ["secret=PRIVATE_SECRET", "/private/test", "test\x1b[31m", "t" * 81]
)
def test_unsafe_test_name_is_omitted(service, unsafe):
    verified(service, stages=(VerificationStage(name=unsafe, argv=("check",), returncode=0),))
    result = detail(service)["result"]
    assert result["tests"] == []
    assert result["completeness"] == "TRUNCATED"


@pytest.mark.parametrize(
    "fields",
    [
        {"stages": ()},
        {"unexpected_paths": ("outside",)},
        {"stages": (VerificationStage(name="tests", argv=("test",), returncode=1),)},
    ],
)
def test_inconsistent_passing_evidence_is_failed(service, fields):
    verified(service, **fields)
    result = detail(service)
    assert result["answer_completeness"] == "FAILED"
    assert result["verification"]["status"] == "EVIDENCE_CONFLICT"


@pytest.mark.parametrize(
    "unsafe",
    [
        "Use token PRIVATE_SECRET",
        "Open /private/secret/folder",
        "Use sk-PRIVATE_SECRET",
        "Goal\x1b[31m PRIVATE_SECRET",
        "C:\\Users\\PRIVATE_SECRET",
        "`cat PRIVATE_SECRET`",
        "Use authorization: PRIVATE_SECRET",
        "a" * (GOAL_LIMIT + 1),
    ],
)
def test_goal_scrubbed_in_every_export(service, unsafe):
    verified(service, intent=unsafe)
    result = continuation(service)
    rendered = json.dumps(result)
    assert "PRIVATE_SECRET" not in rendered and "/private" not in rendered
    assert result["result"]["goal_redacted"] is True
    assert result["result"]["completeness"] == "TRUNCATED"
    assert result["continuation"]["goal"] == result["result"]["goal"]


def test_manual_continuation_is_bound_repeatable_readonly_and_copyable(service):
    verified(service)
    before = counts(service)
    result = continuation(service)
    summary = result["continuation"]
    assert summary["manual_only"] is True
    assert summary["result_id"] == result["result"]["result_id"]
    assert summary["binding"] == result["result"]["binding"]
    assert result["result"]["artifacts"][0]["sha256"] in summary["text"]
    assert service.store.store_id in summary["text"]
    for label in ("Goal:", "Completed:", "Pending:", "Evidence:", "Next step:"):
        assert label in summary["text"]
    assert summary["summary_id"] == "continuation-" + digest(
        {key: value for key, value in summary.items() if key != "summary_id"}
    )
    assert result == continuation(service)
    assert counts(service) == before and service.dispatch_executor.calls == []
    original_id = summary["result_id"]
    transition(service, TaskState.COMPLETED)
    assert continuation(service)["result"]["result_id"] != original_id


def test_continuation_http_and_cli_contract_without_socket(service, monkeypatch, capsys):
    verified(service)
    call = request(service, "CONTINUATION", task_id="task-1")
    code, response = _http(service, call)
    assert code == 200 and response["payload"]["continuation"]["manual_only"]
    calls = []

    def downstream(_client, method, path, *, payload=None):
        calls.append((method, path))
        return handle_local_client_v2(service, payload)

    monkeypatch.setattr(ControlPlaneClient, "_request", downstream)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call)))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 0
    assert json.loads(capsys.readouterr().out) == response
    assert calls == [("POST", "/v2/local-client")]


@pytest.mark.parametrize(
    "extra", [{"retry": True}, {"intent": "run"}, {"expected_process_epoch": "f" * 32}]
)
def test_continuation_exact_shape_never_adds_authority(service, extra):
    submit(service)
    before = counts(service)
    with pytest.raises(ValidationError):
        handle_local_client_v2(service, request(service, "CONTINUATION", task_id="task-1") | extra)
    assert counts(service) == before


def test_startup_recovery_required_survives_current_epoch(service):
    submit(service)
    call = start_request(service)
    handle_local_client_v2(service, call)
    assert service.dispatch_executor.started.wait(2)
    service.store.connection.execute(
        "UPDATE owner_dispatches SET status='BLOCKED',failure_code='STARTUP_RECOVERY_REQUIRED'"
    )
    result = handle_local_client_v2(service, call | {"operation": "DISPATCH_STATUS"})
    assert result["payload"]["recovery_required"] is True
    assert result["payload"]["failure_code"] == "STARTUP_RECOVERY_REQUIRED"
    assert service.dispatch_executor.calls == ["start-1"]
