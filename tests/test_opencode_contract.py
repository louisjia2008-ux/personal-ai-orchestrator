from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.opencode_contract import (
    AdapterAction,
    IdempotentDecisionLedger,
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
    resolve_adapter_outcome,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)
M3 = ModelRef(provider_id="minimax", model_id="m3")
GLM = ModelRef(provider_id="zai", model_id="glm-5.3")


def request(
    *,
    request_id: str = "req-1",
    session_id: str = "session-a",
    mode: RoutingMode = RoutingMode.SHADOW,
    current_model: ModelRef | None = M3,
    task_state_version: int = 3,
) -> RoutingRequest:
    return RoutingRequest(
        request_id=request_id,
        session_id=session_id,
        project_id="fixture",
        location="/tmp/disposable-fixture",
        current_model=current_model,
        mode=mode,
        task_id="task-1",
        task_state_version=task_state_version,
        requested_at=NOW,
    )


def decision(
    *,
    request_id: str = "req-1",
    decision_id: str = "dec-1",
    mode: RoutingMode = RoutingMode.SHADOW,
    selected_model: ModelRef | None = GLM,
    switch_requested: bool = False,
    task_state_version: int | None = 3,
) -> RoutingDecision:
    return RoutingDecision(
        decision_id=decision_id,
        request_id=request_id,
        mode=mode,
        selected_model=selected_model,
        selected_execution_target_id="target-glm",
        switch_requested=switch_requested,
        task_state_version=task_state_version,
        explanation_ref="explanations/dec-1.json",
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        quota_snapshot_ids=("quota-zai-1", "quota-minimax-1"),
        decided_at=NOW,
    )


def test_shadow_mode_records_without_switching() -> None:
    outcome = resolve_adapter_outcome(request(), decision())
    assert outcome.action is AdapterAction.RECORD_ONLY
    assert outcome.target_model == GLM
    assert outcome.decision_id == "dec-1"


def test_active_mode_switches_to_selected_model() -> None:
    outcome = resolve_adapter_outcome(
        request(mode=RoutingMode.ACTIVE),
        decision(mode=RoutingMode.ACTIVE, switch_requested=True),
    )
    assert outcome.action is AdapterAction.SWITCH_MODEL
    assert outcome.target_model == GLM


def test_active_mode_keeps_current_model_when_already_selected() -> None:
    outcome = resolve_adapter_outcome(
        request(mode=RoutingMode.ACTIVE, current_model=GLM),
        decision(mode=RoutingMode.ACTIVE, switch_requested=True),
    )
    assert outcome.action is AdapterAction.KEEP_CURRENT
    assert "already matches" in outcome.reason


def test_active_mode_rejects_mismatched_task_state_version() -> None:
    outcome = resolve_adapter_outcome(
        request(mode=RoutingMode.ACTIVE, task_state_version=3),
        decision(mode=RoutingMode.ACTIVE, switch_requested=True, task_state_version=2),
    )
    assert outcome.action is AdapterAction.KEEP_CURRENT
    assert "state version" in outcome.reason


def test_daemon_failure_is_safe_bypass() -> None:
    outcome = resolve_adapter_outcome(
        request(mode=RoutingMode.ACTIVE),
        None,
        daemon_error="timeout",
    )
    assert outcome.action is AdapterAction.KEEP_CURRENT
    assert "timeout" in outcome.reason


def test_mismatched_request_id_fails_closed() -> None:
    outcome = resolve_adapter_outcome(
        request(request_id="req-current", mode=RoutingMode.ACTIVE),
        decision(request_id="req-stale", mode=RoutingMode.ACTIVE, switch_requested=True),
    )
    assert outcome.action is AdapterAction.KEEP_CURRENT


def test_mismatched_mode_fails_closed() -> None:
    outcome = resolve_adapter_outcome(
        request(mode=RoutingMode.ACTIVE),
        decision(mode=RoutingMode.SHADOW),
    )
    assert outcome.action is AdapterAction.KEEP_CURRENT


def test_shadow_decision_cannot_request_switch() -> None:
    with pytest.raises(ValidationError, match="only ACTIVE decisions"):
        decision(mode=RoutingMode.SHADOW, switch_requested=True)


def test_active_switch_requires_selected_model() -> None:
    with pytest.raises(ValidationError, match="requires selected_model"):
        decision(mode=RoutingMode.ACTIVE, selected_model=None, switch_requested=True)


def test_active_switch_requires_task_state_version() -> None:
    with pytest.raises(ValidationError, match="requires task_state_version"):
        decision(mode=RoutingMode.ACTIVE, switch_requested=True, task_state_version=None)


def test_duplicate_request_id_rejects_conflicting_decision() -> None:
    ledger = IdempotentDecisionLedger()
    ledger.record(decision(decision_id="dec-a", selected_model=M3))
    with pytest.raises(ValueError, match="different routing decision"):
        ledger.record(decision(decision_id="dec-b", selected_model=GLM))
