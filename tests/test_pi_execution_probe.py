from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
)
from personal_ai_orchestrator.pi_execution_probe import (
    PROBE_MARKER,
    PROBE_PROMPT,
    run_pi_execution_probe,
)
from personal_ai_orchestrator.pi_runtime import summarize_pi_json_stream

NOW = datetime(2026, 9, 12, 22, 30, tzinfo=UTC)


def _successful_pi_stream() -> bytes:
    return b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            (
                b'{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
                b'"delta":"PERSONAL-AI-ORCHESTRATOR-PI-EXECUTION-PROBE-OK"}}'
            ),
            (
                b'{"type":"message_end","message":{"role":"assistant",'
                b'"provider":"zai","model":"glm-5.3","stopReason":"stop","content":[]}}'
            ),
            b'{"type":"agent_end","messages":[]}',
        )
    )


def test_successful_pi_probe_records_verified_exact_target(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_execution_probe as module

    calls = []

    async def fake_probe(**kwargs):
        calls.append(kwargs)
        return 0, _successful_pi_stream(), b"", False

    monkeypatch.setattr(module, "_probe", fake_probe)

    rendered = run_pi_execution_probe(
        pi_bin="/usr/local/bin/pi",
        provider_id="zai-coding-plan",
        execution_target_id="pi-zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=tmp_path / "evidence",
        timeout_seconds=15.0,
    )

    assert rendered["result"] == ExecutionVerificationOutcome.VERIFIED.value
    assert calls
    assert calls[0]["pi_bin"] == "/usr/local/bin/pi"
    assert calls[0]["model_ref"] == "zai/glm-5.3"
    assert calls[0]["cwd"] == tmp_path

    evidence = ExecutionEvidenceJournal(tmp_path / "evidence").latest_for_target(
        "pi-zai-coding-plan-glm-5.3"
    )
    assert evidence is not None
    assert evidence.establishes_verified is True
    assert evidence.provider_id == "zai-coding-plan"
    assert evidence.model_sku_id == "zai-coding-plan/glm-5.3"


def test_probe_target_identity_mismatch_fails_closed_without_runtime_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_execution_probe as module

    async def fail_probe(**_kwargs):
        raise AssertionError("runtime must not start for an invalid target identity")

    monkeypatch.setattr(module, "_probe", fail_probe)

    rendered = run_pi_execution_probe(
        pi_bin="/usr/local/bin/pi",
        provider_id="zai-coding-plan",
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=tmp_path / "evidence",
    )

    assert rendered["result"] == ExecutionVerificationOutcome.UNKNOWN.value
    journal = ExecutionEvidenceJournal(tmp_path / "evidence")
    evidence = journal.latest_for_target("zai-coding-plan-glm-5.3")
    assert evidence is not None
    assert evidence.reason_code == "PI_PROBE_TARGET_IDENTITY_MISMATCH"


def test_probe_does_not_verify_wrong_model_or_missing_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_execution_probe as module

    wrong_model = b"\n".join(
        (
            b'{"type":"session","version":3,"id":"s1","cwd":"/tmp/w"}',
            b'{"type":"agent_start"}',
            (
                b'{"type":"message_end","message":{"role":"assistant",'
                b'"provider":"zai","model":"glm-5.2","stopReason":"stop","content":[]}}'
            ),
            b'{"type":"agent_end","messages":[]}',
        )
    )

    async def fake_probe(**_kwargs):
        return 0, wrong_model, b"", False

    monkeypatch.setattr(module, "_probe", fake_probe)

    rendered = run_pi_execution_probe(
        pi_bin="pi",
        provider_id="zai-coding-plan",
        execution_target_id="pi-zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan/glm-5.3",
        cwd=tmp_path,
        evidence_root=tmp_path / "evidence",
    )
    assert rendered["result"] == ExecutionVerificationOutcome.UNKNOWN.value


def test_probe_protocol_helpers_are_bounded_and_sanitized(tmp_path: Path) -> None:
    assert isinstance(PROBE_PROMPT, str)
    assert PROBE_MARKER not in json.dumps({"provider": "zai-coding-plan"})
    summary = summarize_pi_json_stream(_successful_pi_stream())
    assert summary.completed is True
    assert summary.matches_model_ref("zai/glm-5.3") is True
    assert (tmp_path / "evidence").exists() is False
