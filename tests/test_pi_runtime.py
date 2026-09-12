from __future__ import annotations

import json
from pathlib import Path

from personal_ai_orchestrator.pi_runtime import (
    PI_ALLOWED_TOOLS,
    PI_PROTOCOL_ERROR_EXIT,
    PI_WORKTREE_GUARD_SOURCE,
    PiRuntimeConfig,
    build_pi_json_argv,
    pi_model_ref,
    seed_pi_worktree_guard,
    summarize_pi_json_stream,
)


def _assistant_message_end(
    *,
    provider: str = "zai",
    model: str = "glm-5.3",
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> bytes:
    message: dict[str, object] = {
        "role": "assistant",
        "provider": provider,
        "model": model,
        "stopReason": stop_reason,
        "content": [],
    }
    if error_message is not None:
        message["errorMessage"] = error_message
    return json.dumps({"type": "message_end", "message": message}).encode()


def test_pi_model_ref_translates_existing_pao_provider_surfaces() -> None:
    assert (
        pi_model_ref(
            provider_id="zai-coding-plan",
            model_sku_id="zai-coding-plan/glm-5.3",
        )
        == "zai/glm-5.3"
    )
    assert (
        pi_model_ref(
            provider_id="minimax-cn-coding-plan",
            model_sku_id="minimax-cn-coding-plan/MiniMax-M2.5",
        )
        == "minimax-cn/MiniMax-M2.5"
    )


def test_pi_argv_is_one_shot_fail_closed_and_has_no_shell_tool(tmp_path: Path) -> None:
    guard = tmp_path / ".pao" / "guard.ts"
    argv = build_pi_json_argv(
        config=PiRuntimeConfig(pi_bin="/opt/pi/bin/pi"),
        model_ref="zai/glm-5.3",
        intent="Fix the bug",
        guard_path=guard,
    )

    assert argv[0] == "/opt/pi/bin/pi"
    assert argv[1:3] == ("--mode", "json")
    assert "--no-session" in argv
    assert "--no-approve" in argv
    assert "--no-extensions" in argv
    assert "--no-skills" in argv
    assert "--no-prompt-templates" in argv
    assert "--no-context-files" in argv
    assert argv[argv.index("-e") + 1] == str(guard)
    assert argv[argv.index("--model") + 1] == "zai/glm-5.3"
    assert argv[argv.index("--tools") + 1] == ",".join(PI_ALLOWED_TOOLS)
    assert "bash" not in PI_ALLOWED_TOOLS
    assert "powershell" not in PI_ALLOWED_TOOLS
    assert argv[-2:] == ("--", "Fix the bug")


def test_seed_pi_guard_overwrites_weakened_previous_copy(tmp_path: Path) -> None:
    target = seed_pi_worktree_guard(tmp_path)
    target.write_text("// weakened", encoding="utf-8")

    second = seed_pi_worktree_guard(tmp_path)

    assert second == target
    assert second.read_text(encoding="utf-8") == PI_WORKTREE_GUARD_SOURCE
    assert "path escapes assigned worktree" in PI_WORKTREE_GUARD_SOURCE
    assert "symlink escapes assigned worktree" in PI_WORKTREE_GUARD_SOURCE
    assert "protected host path" in PI_WORKTREE_GUARD_SOURCE
    assert 'OPTIONAL_PATH_TOOLS = new Set(["grep", "find", "ls"])' in PI_WORKTREE_GUARD_SOURCE


def test_pi_json_summary_accepts_exact_model_complete_tool_lifecycle() -> None:
    stream = b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            b'{"type":"tool_execution_start","toolCallId":"t1","toolName":"write"}',
            b'{"type":"tool_execution_end","toolCallId":"t1","toolName":"write"}',
            _assistant_message_end(),
            b'{"type":"agent_end","messages":[]}',
        )
    )

    summary = summarize_pi_json_stream(stream)

    assert summary.completed is True
    assert summary.matches_model_ref("zai/glm-5.3") is True
    assert summary.matches_model_ref("zai/glm-5.2") is False
    assert summary.event_count == 6
    assert summary.tool_start_count == 1
    assert summary.tool_end_count == 1
    assert summary.final_stop_reason == "stop"


def test_pi_json_summary_rejects_clean_exit_without_agent_end() -> None:
    stream = b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            _assistant_message_end(),
        )
    )

    summary = summarize_pi_json_stream(stream)

    assert summary.protocol_valid is True
    assert summary.completed is False
    assert PI_PROTOCOL_ERROR_EXIT != 0


def test_pi_json_summary_rejects_provider_error_and_preserves_bounded_reason() -> None:
    stream = b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            _assistant_message_end(
                stop_reason="error",
                error_message="429: usage limit reached",
            ),
            b'{"type":"agent_end","messages":[]}',
        )
    )

    summary = summarize_pi_json_stream(stream)

    assert summary.protocol_valid is True
    assert summary.completed is False
    assert summary.final_stop_reason == "error"
    assert summary.final_error_message == "429: usage limit reached"


def test_pi_json_summary_rejects_malformed_jsonl_and_extension_error() -> None:
    malformed = summarize_pi_json_stream(
        b'{"type":"session"}\nnot-json\n{"type":"agent_end"}'
    )
    assert malformed.completed is False
    assert malformed.parse_error == "invalid_jsonl"

    extension_error = summarize_pi_json_stream(
        b"\n".join(
            (
                b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
                b'{"type":"agent_start"}',
                b'{"type":"extension_error","error":"guard failed"}',
                _assistant_message_end(),
                b'{"type":"agent_end","messages":[]}',
            )
        )
    )
    assert extension_error.protocol_valid is True
    assert extension_error.extension_error_count == 1
    assert extension_error.completed is False


def test_pi_json_summary_rejects_unpaired_tool_ids() -> None:
    stream = b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            b'{"type":"tool_execution_start","toolCallId":"t1","toolName":"write"}',
            b'{"type":"tool_execution_end","toolCallId":"other","toolName":"write"}',
        )
    )

    summary = summarize_pi_json_stream(stream)

    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_tool_lifecycle"
