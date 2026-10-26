"""Full authoritative dispatch must accept complete large streams and reject bad tails."""

import json
import stat
import sys

import pytest

from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_pi_dispatch_executor import _executor, _reserve, _run_row


@pytest.mark.parametrize("invalid_tail", [False, True])
def test_large_protocol_reaches_verifier_only_with_valid_tail(tmp_path, invalid_tail):
    executor, repo, state_db = _executor(tmp_path, complete=True)
    binary = tmp_path / "bin/pi"
    script = f"""#!{sys.executable}
import json,sys
from pathlib import Path
Path("hello.txt").write_text("Pi runtime acceptance\\n")
def event(value):
    sys.stdout.write(json.dumps(value)+"\\n")
event({{"type":"session"}})
event({{"type":"agent_start"}})
for _ in range(80):
    event({{"type":"message_update","payload":"private-stream-canary"+"x"*65536}})
event({{"type":"message_end","message":{{"role":"assistant","provider":"zai","model":"glm-5.3","stopReason":"stop"}}}})
event({{"type":"agent_end"}})
if {invalid_tail!r}: sys.stdout.write("bad-protocol\\n")
"""
    binary.write_text(script)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    _reserve(repo, state_db)
    executor.execute("dispatch-pi")
    store = SafetyKernelStore(state_db)
    try:
        run = _run_row(store)
        result = json.loads(run["result_json"])
        assert store.get_task("task-pi").state is (
            TaskState.BLOCKED if invalid_tail else TaskState.VERIFIED
        )
        assert run["status"] == ("FAILED" if invalid_tail else "FINISHED")
        assert result["pi_diagnostic_code"] == (
            "PI_INVALID_JSONL" if invalid_tail else "PI_COMPLETE"
        )
        assert result["stdout_bytes"] == 4 * 1024 * 1024
        assert result["pi_stream_observed_bytes"] > result["stdout_bytes"]
        assert result["output_truncated"] is True
        assert result["pi_stream_complete"] is True
        assert "private-stream-canary" not in json.dumps(result)
        assert not result.get("stdout_tail")
        assert store.get_workspace("task-pi").writer_token is None
        store.assert_running_invariant("task-pi")
    finally:
        store.close()
