"""Bridge deterministic scheduler output to the fail-closed OpenCode adapter contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import (
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
)
from personal_ai_orchestrator.scheduler import SchedulerDecision


def _scheduler_explanation(
    scheduler: SchedulerDecision,
    registry: ModelRegistry,
    *,
    catalog_snapshot_id: str,
    policy_snapshot_id: str,
    quota_snapshot_ids: tuple[str, ...],
    policy_resolution: dict[str, object] | None = None,
) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for evaluation in scheduler.evaluations:
        model = registry.models.get(evaluation.model_sku_id)
        provider = registry.providers.get(model.provider_id) if model is not None else None
        candidates.append(
            {
                "execution_target_id": evaluation.execution_target_id,
                "model_sku_id": evaluation.model_sku_id,
                "model_display_name": (
                    model.display_name if model is not None else evaluation.model_sku_id
                ),
                "provider_id": model.provider_id if model is not None else None,
                "provider_display_name": provider.display_name if provider is not None else None,
                "eligible": evaluation.eligible,
                "admitted": evaluation.admitted,
                "selected": evaluation.execution_target_id
                == scheduler.selected_execution_target_id,
                "score": evaluation.score,
                "reasons": list(evaluation.reasons),
                "why_not_selected": None
                if evaluation.execution_target_id == scheduler.selected_execution_target_id
                else (
                    "; ".join(evaluation.reasons)
                    if evaluation.reasons
                    else "lower score under resolved scheduling policy"
                ),
                "quota_pool_id": evaluation.quota_pool_id,
                "quota_snapshot_id": evaluation.quota_snapshot_id,
                "availability_evidence_state": evaluation.observed_availability_state,
                "scarcity_class": evaluation.scarcity_class.value,
                "score_components": [
                    component.model_dump(mode="json") for component in evaluation.score_components
                ],
            }
        )
    selected = next((item for item in candidates if item["selected"]), None)
    return {
        "policy_id": scheduler.policy_id,
        "policy_objective": scheduler.policy_objective.value,
        "policy_resolution": policy_resolution,
        "decision_reason": scheduler.decision_reason,
        "catalog_snapshot_id": catalog_snapshot_id,
        "policy_snapshot_id": policy_snapshot_id,
        "quota_snapshot_ids": list(quota_snapshot_ids),
        "selected_execution_target_id": scheduler.selected_execution_target_id,
        "selected_model_sku_id": scheduler.selected_model_sku_id,
        "why_selected": (
            "; ".join(selected["reasons"]) if selected is not None and selected["reasons"] else None
        ),
        "eligible_candidates": [
            item for item in candidates if item["eligible"] and item["admitted"]
        ],
        "ineligible_candidates": [
            item for item in candidates if not item["eligible"] or not item["admitted"]
        ],
        "candidates": candidates,
    }


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
    activation_gate: ActiveRoutingGate | None = None,
    decided_at: datetime | None = None,
    policy_resolution: dict[str, object] | None = None,
) -> RoutingDecision:
    """Create the adapter-facing immutable decision from one scheduler recommendation.

    Production ACTIVE switching remains fail-closed unless the structured activation gate proves
    P0/P1/adapter/Shadow/BYPASS acceptance. SHADOW always records without side effects.
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

    common = {
        "decision_id": decision_id,
        "request_id": request.request_id,
        "mode": request.mode,
        "task_state_version": request.task_state_version,
        "catalog_snapshot_id": catalog_snapshot_id,
        "policy_snapshot_id": policy_snapshot_id,
        "quota_snapshot_ids": quota_snapshot_ids,
        "decided_at": decided_at or datetime.now(UTC),
        "explanation": _scheduler_explanation(
            scheduler,
            registry,
            catalog_snapshot_id=catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot_id,
            quota_snapshot_ids=quota_snapshot_ids,
            policy_resolution=policy_resolution,
        ),
    }

    if request.mode is RoutingMode.BYPASS:
        return RoutingDecision(
            **common,
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
            **common,
            selected_execution_target_id=None,
            switch_requested=False,
            explanation_ref=f"routing-decisions/{decision_id}.json",
            fallback_reason=scheduler.decision_reason,
        )

    active_authorized = activation_gate is not None and activation_gate.authorized
    switch_requested = request.mode is RoutingMode.ACTIVE and active_authorized
    fallback_reason = None
    if request.mode is RoutingMode.ACTIVE and not active_authorized:
        blockers = (
            activation_gate.blocking_reasons()
            if activation_gate is not None
            else ("production activation evidence absent",)
        )
        fallback_reason = "production ACTIVE gate not authorized: " + "; ".join(blockers)

    return RoutingDecision(
        **common,
        selected_model=selected_model,
        selected_execution_target_id=selected_target_id,
        switch_requested=switch_requested,
        explanation_ref=f"routing-decisions/{decision_id}.json",
        fallback_reason=fallback_reason,
    )


__all__ = ["build_routing_decision"]
