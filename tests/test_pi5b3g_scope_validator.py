from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.pi5_broker import (
    DelegationBrokerContext,
    DelegationBrokerSession,
    DelegationBrokerStatus,
    DelegationChildPlan,
    DelegationChildResult,
)
from personal_ai_orchestrator.pi5_contract import DelegationRequest
from personal_ai_orchestrator.pi5b3g_scope_validator import (
    PI5B3G_SCOPE_VALIDATOR_VERSION,
    validate_pi5b3g_child_scope,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "pi5b3g_dynamic_scope_corpus.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_offline_adversarial_corpus(case: dict[str, object]) -> None:
    result = validate_pi5b3g_child_scope(
        observation_number=int(case["observation"]),
        intent=str(case["intent"]),
        reason=str(case["reason"]),
    )

    assert result.decision.value == case["decision"]
    assert result.rule_id == case["rule_id"]
    assert result.category == case["category"]
    assert result.validator_version == PI5B3G_SCOPE_VALIDATOR_VERSION
    assert result.stage == "PRE_CHILD_FORWARD"
    assert result.child_forwarded is False
    assert result.child_worker_started is False


def test_rule_id_and_sanitized_trace_are_deterministic() -> None:
    arguments = {
        "observation_number": 1,
        "intent": (
            'Create pi5b3g_obs1_child.json with '
            '{"status":"PI5B3G_CHILD_VERIFIED"} and modify README.md.'
        ),
        "reason": "Required verification artifact.",
    }

    first = validate_pi5b3g_child_scope(**arguments)
    second = validate_pi5b3g_child_scope(**arguments)
    payload = first.model_dump(mode="json")

    assert first == second
    assert first.rule_id == "SCOPE_SECOND_FILE"
    assert first.category == "FILE_SCOPE"
    assert "intent" not in payload
    assert "reason" not in payload
    assert arguments["intent"] not in first.model_dump_json()
    assert arguments["reason"] not in first.model_dump_json()
    assert set(payload) >= {
        "validator_version",
        "normalized_intent_length",
        "intent_sha256",
        "reason_length",
        "reason_sha256",
        "rule_id",
        "category",
        "stage",
        "observation_number",
        "expected_filename",
        "child_forwarded",
        "child_worker_started",
    }


@pytest.mark.asyncio
async def test_broker_rejection_consumes_budget_without_starting_child() -> None:
    class CountingPort:
        starts = 0

        async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
            self.starts += 1
            raise AssertionError("rejected scope must never reach child execution")

    traces = []
    port = CountingPort()
    session = DelegationBrokerSession(
        context=DelegationBrokerContext(
            parent_task_id="pi5b3g-obs1-parent",
            parent_run_id="run-1",
            project_id="disposable-fixture",
            base_sha="fixture-sha",
            max_children=1,
        ),
        child_port=port,
        scope_validator=lambda request: validate_pi5b3g_child_scope(
            observation_number=1,
            intent=request.intent,
            reason=request.reason,
        ),
        scope_trace_sink=traces.append,
    )
    request = DelegationRequest(
        tool_call_id="call-1",
        ordinal=1,
        intent=(
            'Run shell, then create pi5b3g_obs1_child.json with '
            '{"status":"PI5B3G_CHILD_VERIFIED"}.'
        ),
        reason="Required verification artifact.",
    )

    rejected = await session.handle(request)
    exhausted = await session.handle(
        request.model_copy(update={"tool_call_id": "call-2"})
    )

    assert rejected.status is DelegationBrokerStatus.REJECTED
    assert rejected.reason_code == "SCOPE_SHELL_CAPABILITY"
    assert exhausted.reason_code == "DELEGATION_BUDGET_EXHAUSTED"
    assert port.starts == 0
    assert len(traces) == 1
    assert traces[0].child_forwarded is False
    assert traces[0].child_worker_started is False


def test_forbidden_reason_is_validated_as_well_as_intent() -> None:
    result = validate_pi5b3g_child_scope(
        observation_number=1,
        intent=(
            'Create only pi5b3g_obs1_child.json with '
            '{"status":"PI5B3G_CHILD_VERIFIED"}.'
        ),
        reason="Use a fallback if needed.",
    )

    assert result.rule_id == "SCOPE_FALLBACK_REQUEST"
    assert result.input_field == "reason"


@pytest.mark.asyncio
async def test_validator_error_fails_closed_before_child_execution() -> None:
    port = type("NeverPort", (), {"execute_child": pytest.fail})()
    session = DelegationBrokerSession(
        context=DelegationBrokerContext(
            parent_task_id="parent",
            parent_run_id="run",
            project_id="fixture",
            base_sha="sha",
            max_children=1,
        ),
        child_port=port,
        scope_validator=lambda request: 1 / 0,
    )
    response = await session.handle(
        DelegationRequest(
            tool_call_id="call",
            ordinal=1,
            intent="bounded intent",
            reason="bounded reason",
        )
    )

    assert response.status is DelegationBrokerStatus.REJECTED
    assert response.reason_code == "SCOPE_VALIDATOR_ERROR"
