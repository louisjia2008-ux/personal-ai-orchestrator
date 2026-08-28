"""Typed boundary between OpenCode and the orchestrator scheduler.

The OpenCode adapter is intentionally thin.  It reports non-secret session context,
receives a routing decision, and decides whether to keep, record, or switch the
session model.  Scheduler policy and provider execution remain outside this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoutingMode(StrEnum):
    """How aggressively the adapter may apply scheduler recommendations."""

    BYPASS = "BYPASS"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


class AdapterAction(StrEnum):
    """Concrete action the thin OpenCode adapter should take."""

    KEEP_CURRENT = "KEEP_CURRENT"
    RECORD_ONLY = "RECORD_ONLY"
    SWITCH_MODEL = "SWITCH_MODEL"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRef(FrozenModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    variant: str | None = None


class RoutingRequest(FrozenModel):
    """Retry-safe, non-secret request emitted by an OpenCode adapter."""

    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    project_id: str | None = None
    location: str | None = None
    current_model: ModelRef | None = None
    agent: str | None = None
    mode: RoutingMode = RoutingMode.SHADOW
    task_id: str | None = None
    task_state_version: int | None = Field(default=None, ge=0)
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoutingDecision(FrozenModel):
    """Auditable scheduler output consumed by the OpenCode adapter."""

    decision_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    mode: RoutingMode
    selected_model: ModelRef | None = None
    switch_requested: bool = False
    explanation_ref: str | None = None
    catalog_snapshot_id: str | None = None
    policy_snapshot_id: str | None = None
    quota_snapshot_ids: tuple[str, ...] = ()
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fallback_reason: str | None = None

    @model_validator(mode="after")
    def validate_mode_semantics(self) -> RoutingDecision:
        if self.mode is not RoutingMode.ACTIVE and self.switch_requested:
            raise ValueError("only ACTIVE decisions may request a model switch")
        if self.switch_requested and self.selected_model is None:
            raise ValueError("switch_requested requires selected_model")
        if self.mode is RoutingMode.BYPASS and self.selected_model is not None:
            raise ValueError("BYPASS decisions must not select a model")
        return self


class AdapterOutcome(FrozenModel):
    """Resolved adapter behavior after applying fail-safe contract rules."""

    action: AdapterAction
    target_model: ModelRef | None = None
    decision_id: str | None = None
    reason: str = Field(min_length=1)


def resolve_adapter_outcome(
    request: RoutingRequest,
    decision: RoutingDecision | None,
    *,
    daemon_error: str | None = None,
) -> AdapterOutcome:
    """Translate a daemon result into a conservative OpenCode adapter action.

    Any missing, mismatched, or unavailable daemon response falls back to keeping
    OpenCode's current model.  This function intentionally contains no scheduling
    policy; it only enforces the integration contract.
    """

    if request.mode is RoutingMode.BYPASS:
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            reason="bypass mode preserves OpenCode's current model",
        )

    if decision is None:
        detail = f": {daemon_error}" if daemon_error else ""
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            reason=f"orchestrator unavailable or returned no decision{detail}",
        )

    if decision.request_id != request.request_id:
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            decision_id=decision.decision_id,
            reason="decision request_id does not match the current routing request",
        )

    if decision.mode != request.mode:
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            decision_id=decision.decision_id,
            reason="decision mode does not match the adapter request mode",
        )

    if request.mode is RoutingMode.SHADOW:
        return AdapterOutcome(
            action=AdapterAction.RECORD_ONLY,
            target_model=decision.selected_model,
            decision_id=decision.decision_id,
            reason="shadow mode records the recommendation without switching models",
        )

    if not decision.switch_requested or decision.selected_model is None:
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            target_model=decision.selected_model,
            decision_id=decision.decision_id,
            reason=decision.fallback_reason or "active decision requested no model switch",
        )

    if decision.selected_model == request.current_model:
        return AdapterOutcome(
            action=AdapterAction.KEEP_CURRENT,
            target_model=decision.selected_model,
            decision_id=decision.decision_id,
            reason="selected model already matches the OpenCode session model",
        )

    return AdapterOutcome(
        action=AdapterAction.SWITCH_MODEL,
        target_model=decision.selected_model,
        decision_id=decision.decision_id,
        reason="active routing decision requests a session-scoped model switch",
    )


class IdempotentDecisionLedger:
    """Small in-memory spike helper proving request-id idempotency semantics.

    Production persistence belongs in the durable daemon store.  This helper exists
    only so Stage A can test the rule that one request_id cannot acquire conflicting
    routing decisions when an OpenCode hook is retried.
    """

    def __init__(self) -> None:
        self._decisions: dict[str, RoutingDecision] = {}

    def record(self, decision: RoutingDecision) -> RoutingDecision:
        existing = self._decisions.get(decision.request_id)
        if existing is None:
            self._decisions[decision.request_id] = decision
            return decision
        if existing != decision:
            raise ValueError("request_id already has a different routing decision")
        return existing
