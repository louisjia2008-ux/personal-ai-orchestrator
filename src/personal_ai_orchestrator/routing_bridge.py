"""Bridge deterministic scheduler output to the fail-closed OpenCode adapter contract."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import (
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
)
from personal_ai_orchestrator.scheduler import SchedulerDecision


def _decision_id(
    request: RoutingRequest,
    scheduler: SchedulerDecision,
    *,
    catalog_snapshot_id: str,
    policy_snapshot_id: str,
    quota_snapshot_ids: tuple[str, ...],
) -> str:
    payload = {
        "request": request.model_dump(mode="json"),
        "scheduler": scheduler.model_dump(mode="json"),
        "catalog_snapshot_id": catalog_snapshot_id,
        "policy_snapshot_id": policy_snapshot_id,
        "quota_snapshot_ids": quota_snapshot_ids,
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"route-{digest[:24]}"


def build_routing_decision(
    request: RoutingRequest,
    scheduler: SchedulerDecision,
    registry: ModelRegistry,
    *,
    catalog_snapshot_id: str,
    policy_snapshot_id: str,
    production_active_authorized: bool = False,
    decided_at: datetime | None = None,
) -> RoutingDecision:
    """Create the adapter-facing immutable decision from one scheduler recommendation.

    Production ACTIVE switching remains fail-closed unless the caller proves the P0/P1/P3.5
    activation gate separately. SHADOW always records a recommendation without side effects.
    """

    if not catalog_snapshot_id:
        raise ValueError("catalog_snapshot_id is required for replayable routing")
    if not policy_snapshot_id:
        raise ValueError("policy_snapshot_id is required for replayable routing")

    quota_snapshot_ids = tuple(
        sorted(
            {
                evaluation.quota_snapshot_id
                for evaluation in scheduler.evaluations
                if evaluation.quota_snapshot_id is not None
            }
        )
    )
    decision_id = _decision_id(
        request,
        scheduler,
        catalog_snapshot_id=catalog_snapshot_id,
        policy_snapshot_id=policy_snapshot_id,
        quota_snapshot_ids=quota_snapshot_ids,
    )

    if request.mode is RoutingMode.BYPASS:
        return RoutingDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            mode=request.mode,
            catalog_snapshot_id=catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot_id,
            quota_snapshot_ids=quota_snapshot_ids,
            decided_at=decided_at or datetime.now(UTC),
            fallback_reason="adapter requested BYPASS",
        )

    selected_target_id = scheduler.selected_execution_target_id
    selected_model = None
    if selected_target_id is not None:
        target = registry.execution_targets[selected_target_id]
        model = registry.models[target.model_sku_id]
        selected_model = ModelRef(
            provider_id=model.provider_id,
            model_id=model.id,
            variant=target.variant,
        )

    if selected_model is None:
        return RoutingDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            mode=request.mode,
            selected_execution_target_id=None,
            switch_requested=False,
            explanation_ref=f"routing-decisions/{decision_id}.json",
            catalog_snapshot_id=catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot_id,
            quota_snapshot_ids=quota_snapshot_ids,
            decided_at=decided_at or datetime.now(UTC),
            fallback_reason=scheduler.decision_reason,
        )

    switch_requested = request.mode is RoutingMode.ACTIVE and production_active_authorized
    fallback_reason = None
    if request.mode is RoutingMode.ACTIVE and not production_active_authorized:
        fallback_reason = "production ACTIVE gate not authorized; recommendation recorded only"

    return RoutingDecision(
        decision_id=decision_id,
        request_id=request.request_id,
        mode=request.mode,
        selected_model=selected_model,
        selected_execution_target_id=selected_target_id,
        switch_requested=switch_requested,
        explanation_ref=f"routing-decisions/{decision_id}.json",
        catalog_snapshot_id=catalog_snapshot_id,
        policy_snapshot_id=policy_snapshot_id,
        quota_snapshot_ids=quota_snapshot_ids,
        decided_at=decided_at or datetime.now(UTC),
        fallback_reason=fallback_reason,
    )


__all__ = ["build_routing_decision"]
