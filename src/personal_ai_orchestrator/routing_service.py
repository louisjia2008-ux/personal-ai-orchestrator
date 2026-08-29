"""Durable service boundary joining OpenCode requests to the deterministic scheduler."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingDecision, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshot, PolicySnapshotJournal
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.scheduler import (
    RoutingPolicy,
    SchedulerDecision,
    TargetTelemetry,
    TaskProfile,
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
    policy_journal: PolicySnapshotJournal | None = None
    task_profiles: dict[str, TaskProfile] = field(default_factory=dict)
    runtime_availability: dict[str, bool] = field(default_factory=dict)
    telemetry: dict[str, TargetTelemetry] = field(default_factory=dict)

    @property
    def policy_snapshot(self) -> PolicySnapshot:
        return PolicySnapshot.from_policy(self.policy)

    def set_task_profile(self, profile: TaskProfile) -> None:
        self.task_profiles[profile.task_id] = profile

    def _existing_decision(self, request: RoutingRequest) -> RoutingDecision | None:
        """Return the exact durable decision for an idempotent retry.

        Reusing a request ID for a different task or routing mode is invalid. A real retry should
        receive the original decision byte-for-byte rather than recomputing it with a new
        ``decided_at`` timestamp or newer quota/policy state.
        """

        row = self.store.connection.execute(
            "SELECT task_id,payload_json FROM routing_decisions WHERE request_id=?",
            (request.request_id,),
        ).fetchone()
        if row is None:
            return None
        if row["task_id"] != request.task_id:
            raise ValueError("request_id already belongs to a different routing task")
        decision = RoutingDecision.model_validate(json.loads(row["payload_json"]))
        if decision.mode is not request.mode:
            raise ValueError("request_id already belongs to a different routing mode")
        return decision

    def route(self, request: RoutingRequest, *, now: datetime | None = None) -> RoutingDecision:
        existing = self._existing_decision(request)
        if existing is not None:
            return existing

        reference = now or datetime.now(UTC)
        policy_snapshot = self.policy_snapshot
        if self.policy_journal is not None:
            self.policy_journal.append(policy_snapshot)

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
            policy_snapshot_id=policy_snapshot.id,
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
