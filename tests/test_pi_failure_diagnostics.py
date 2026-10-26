"""Persisted protocol failure evidence must survive the host privacy boundary."""

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import _failure_result_payload
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_pi_dispatch_executor import _executor, _reserve, _run_row


@pytest.mark.parametrize(
    ("options", "code", "failure_class"),
    [
        ({"complete": False}, "PI_MISSING_AGENT_END", "PROTOCOL_ERROR"),
        ({"complete": True, "model": "glm-5.2"}, "PI_MODEL_MISMATCH", "PROTOCOL_ERROR"),
        (
            {
                "complete": True,
                "stop_reason": "error",
                "error_message": "HTTP 503: overloaded_error private-canary",
            },
            "PI_PROVIDER_ERROR",
            "SERVER_OVERLOADED",
        ),
        (
            {"complete": True, "stop_reason": "error", "error_message": "private-canary"},
            "PI_PROVIDER_ERROR",
            "UNCLASSIFIED",
        ),
    ],
)
def test_failed_pi_run_keeps_protocol_evidence(
    tmp_path: Path, options: dict, code: str, failure_class: str
) -> None:
    executor, repo, state_db = _executor(tmp_path, **options)
    _reserve(repo, state_db)
    executor.execute("dispatch-pi")
    store = SafetyKernelStore(state_db)
    try:
        run = _run_row(store)
        result = json.loads(run["result_json"])
        assert store.get_task("task-pi").state is TaskState.BLOCKED
        assert run["status"] == "FAILED"
        assert result["exit_code"] == 70
        assert result["worker_process_exit_code"] == 0
        assert result["runtime"] == "pi-json"
        assert result["pi_diagnostic_code"] == code
        assert result["worker_failure_class"] == failure_class
        assert result["pi_protocol_valid"] is False
        assert result["pi_session_seen"] is True
        assert result["pi_agent_start_seen"] is True
        assert result["pi_agent_end_seen"] is options["complete"]
        assert "private-canary" not in json.dumps(result)
        assert "stdout_tail" not in result and "stderr_tail" not in result
        assert store.get_workspace("task-pi").writer_token is None
        store.assert_running_invariant("task-pi")
    finally:
        store.close()


def test_failure_boundary_rejects_untrusted_pi_text_and_invalid_metadata() -> None:
    result = _failure_result_payload(
        exit_code=70,
        signal=None,
        worker_result={
            "runtime": "pi-json",
            "worker_failure_class": "UNCLASSIFIED",
            "worker_failure_message": "private-canary",
            "pi_error_message": "private-canary",
            "pi_diagnostic_code": "PI_INVALID_JSONL",
            "pi_parse_error": "private-canary",
            "pi_provider": "private-canary",
            "pi_model": "private-canary",
            "pi_stop_reason": "private-canary",
            "pi_protocol_valid": False,
            "pi_event_count": -1,
            "pi_tool_start_count": True,
            "pi_tool_end_count": 2**63,
            "pi_extension_error_count": 1,
            "worker_process_exit_code": 0,
        },
    )
    assert result["pi_diagnostic_code"] == "PI_INVALID_JSONL"
    assert result["worker_process_exit_code"] == 0
    assert result["pi_extension_error_count"] == 1
    assert "private-canary" not in json.dumps(result)
    assert not {
        "pi_provider",
        "pi_model",
        "pi_stop_reason",
        "pi_parse_error",
        "pi_event_count",
        "pi_tool_start_count",
        "pi_tool_end_count",
    }.intersection(result)
