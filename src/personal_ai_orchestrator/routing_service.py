"""Durable service boundary joining OpenCode requests to the deterministic scheduler."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingDecision, RoutingMode, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshot, PolicySnapshotJournal
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import (
    RoutingPolicy,
    SchedulerDecision,
    TargetTelemetry,
    TaskProfile,
    route_task,
)
from personal_ai_orchestrator.shadow_evidence import PendingShadowObservation, ShadowEvidenceJournal


_ROUTABLE_TASK_STATES = {TaskState.READY, TaskState.RUNNING}


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
    shadow_journal: ShadowEvidenceJournal | None = None
    shadow_actual_execution_targets: dict[str, str] = field(default_factory=dict)

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

    @staticmethod
    def _no_selection(request: RoutingRequest, reason: str) -> SchedulerDecision:
        return SchedulerDecision(
            task_id=request.task_id or "unattached",
            selected_execution_target_id=None,
            selected_model_sku_id=None,
            evaluations=(),
            decision_reason=reason,
        )

    def _scheduler_decision(
        self,
        request: RoutingRequest,
        *,
        reference: datetime,
    ) -> SchedulerDecision:
        profile = self.task_profiles.get(request.task_id or "")
        if profile is None:
            return self._no_selection(
                request,
                "no authoritative TaskProfile is attached to this routing request",
            )

        try:
            task = self.store.get_task(profile.task_id)
        except KeyError:
            return self._no_selection(
                request,
                "task profile is not backed by durable Safety Kernel task state",
            )

        if task.state not in _ROUTABLE_TASK_STATES:
            return self._no_selection(
                request,
                f"task state {task.state.value} is not eligible for model routing",
            )
        if (
            request.task_state_version is not None
            and request.task_state_version != task.state_version
        ):
            return self._no_selection(
                request,
                "routing request carries a stale task state version",
            )
        if request.mode is RoutingMode.ACTIVE and request.task_state_version is None:
            return self._no_selection(
                request,
                "ACTIVE routing requires an exact task state version",
            )

        return route_task(
            self.registry,
            task=profile,
            now=reference,
            known_at=request.requested_at,
            runtime_availability=self.runtime_availability,
            telemetry=self.telemetry,
            policy=self.policy,
        )

    def _record_pending_shadow(
        self,
        *,
        request: RoutingRequest,
        decision: RoutingDecision,
        scheduler: SchedulerDecision,
        reference: datetime,
    ) -> None:
        if self.shadow_journal is None or request.mode is not RoutingMode.SHADOW:
            return
        if request.task_id is None:
            return
        manual_target_id = self.shadow_actual_execution_targets.get(request.task_id)
        if manual_target_id is None:
            return

        selected_target_id = decision.selected_execution_target_id
        actual_evaluation = next(
            (
                evaluation
                for evaluation in scheduler.evaluations
                if evaluation.execution_target_id == manual_target_id
            ),
            None,
        )
        selected_evaluation = next(
            (
                evaluation
                for evaluation in scheduler.evaluations
                if evaluation.execution_target_id == selected_target_id
            ),
            None,
        )
        provider_id = None
        quota_pool_id = None
        predicted_burn_fraction = None
        quota_confidence = EvidenceConfidence.UNKNOWN
        collector_status = None
        actual_target = self.registry.execution_targets.get(manual_target_id)
        if actual_target is not None:
            target = actual_target
            model = self.registry.models[target.model_sku_id]
            provider_id = model.provider_id
        if actual_evaluation is not None:
            quota_pool_id = actual_evaluation.quota_pool_id
            predicted_burn_fraction = actual_evaluation.predicted_burn_fraction
        elif selected_evaluation is not None:
            quota_pool_id = selected_evaluation.quota_pool_id
            predicted_burn_fraction = selected_evaluation.predicted_burn_fraction
        if quota_pool_id is not None:
            snapshot = self.registry.quota_pools[quota_pool_id].snapshot
            quota_confidence = snapshot.confidence
            if snapshot.confidence.value == "UNKNOWN":
                collector_status = QuotaCollectionStatus.UNKNOWN
        profile = self.task_profiles.get(request.task_id)

        self.shadow_journal.append_pending(
            PendingShadowObservation(
                pending_id=f"pending-{decision.decision_id}",
                task_id=request.task_id,
                request_id=request.request_id,
                decision_id=decision.decision_id,
                manual_execution_target_id=manual_target_id,
                scheduler_execution_target_id=selected_target_id,
                catalog_snapshot_id=decision.catalog_snapshot_id or self.catalog_snapshot_id,
                policy_snapshot_id=decision.policy_snapshot_id or self.policy_snapshot.id,
                quota_snapshot_ids=decision.quota_snapshot_ids,
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                task_family="unknown" if profile is None else profile.pool.value.lower(),
                quota_confidence=quota_confidence,
                collector_status=collector_status,
                predicted_burn_fraction=predicted_burn_fraction,
                started_at=reference,
            )
        )

    def route(self, request: RoutingRequest, *, now: datetime | None = None) -> RoutingDecision:
        existing = self._existing_decision(request)
        if existing is not None:
            return existing

        reference = now or datetime.now(UTC)
        policy_snapshot = self.policy_snapshot
        if self.policy_journal is not None:
            self.policy_journal.append(policy_snapshot)

        scheduler = self._scheduler_decision(request, reference=reference)
        decision = build_routing_decision(
            request,
            scheduler,
            self.registry,
            catalog_snapshot_id=self.catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot.id,
            activation_gate=self.activation_gate,
            decided_at=reference,
        )
        try:
            self.store.record_routing_decision(
                decision_id=decision.decision_id,
                request_id=decision.request_id,
                task_id=request.task_id,
                payload=decision.model_dump(mode="json"),
            )
        except ValueError:
            # Two identical retries may race through separate HTTP threads. The first durable
            # writer wins; the loser returns that exact decision instead of surfacing a 503.
            existing = self._existing_decision(request)
            if existing is not None:
                self._record_pending_shadow(
                    request=request,
                    decision=existing,
                    scheduler=scheduler,
                    reference=reference,
                )
                return existing
            raise
        self._record_pending_shadow(
            request=request,
            decision=decision,
            scheduler=scheduler,
            reference=reference,
        )
        return decision


__all__ = ["RoutingService"]
