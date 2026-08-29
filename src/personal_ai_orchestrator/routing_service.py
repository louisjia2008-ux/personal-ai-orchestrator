"""Durable service boundary joining OpenCode requests to the deterministic scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingDecision, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshot
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.scheduler import (
    SchedulerDecision,
    TargetTelemetry,
    TaskProfile,
    RoutingPolicy,
    route_task,
)


@dataclass
class RoutingService:
    """Own scheduler inputs and persist every adapter-facing decision before returning it."""

    registry: ModelRegistry
    store: SafetyKernelStore
    catalog_snapshot_id: str
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)
    activation_gate: ActiveRoutingGate = field(default_factory=ActiveRoutingGate)
    task_profiles: dict[str, TaskProfile] = field(default_factory=dict)
    runtime_availability: dict[str, bool] = field(default_factory=dict)
    telemetry: dict[str, TargetTelemetry] = field(default_factory=dict)

    @property
    def policy_snapshot(self) -> PolicySnapshot:
        return PolicySnapshot.from_policy(self.policy)

    def set_task_profile(self, profile: TaskProfile) -> None:
        self.task_profiles[profile.task_id] = profile

    def route(self, request: RoutingRequest, *, now: datetime | None = None) -> RoutingDecision:
        reference = now or datetime.now(UTC)
        profile = self.task_profiles.get(request.task_id or "")
        if profile is None:
            scheduler = SchedulerDecision(
                task_id=request.task_id or "unattached",
                selected_execution_target_id=None,
                selected_model_sku_id=None,
                evaluations=(),
                decision_reason="no authoritative TaskProfile is attached to this routing request",
            )
        else:
            scheduler = route_task(
                self.registry,
                task=profile,
                now=reference,
                known_at=request.requested_at,
                runtime_availability=self.runtime_availability,
                telemetry=self.telemetry,
                policy=self.policy,
            )

        decision = build_routing_decision(
            request,
            scheduler,
            self.registry,
            catalog_snapshot_id=self.catalog_snapshot_id,
            policy_snapshot_id=self.policy_snapshot.id,
            activation_gate=self.activation_gate,
            decided_at=reference,
        )
        self.store.record_routing_decision(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            task_id=request.task_id,
            payload=decision.model_dump(mode="json"),
        )
        return decision


__all__ = ["RoutingService"]
