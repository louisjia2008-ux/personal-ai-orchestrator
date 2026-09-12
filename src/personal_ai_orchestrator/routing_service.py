"""Durable service boundary joining OpenCode requests to the deterministic scheduler."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingDecision, RoutingMode, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshot, PolicySnapshotJournal
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import (
    RoutingPolicy,
    SchedulingPolicyResolution,
    SchedulerDecision,
    TargetTelemetry,
    TaskProfile,
    policy_from_name,
    route_task,
    resolve_scheduling_policy,
)
from personal_ai_orchestrator.shadow_evidence import PendingShadowObservation, ShadowEvidenceJournal


_ROUTABLE_TASK_STATES = {TaskState.READY, TaskState.RUNNING}


@dataclass
class RoutingService:
    """Own scheduler inputs and persist every adapter-facing decision before returning it."""

    registry: ModelRegistry
    registry_provider: Callable[[], ModelRegistry] | None = None
    store: SafetyKernelStore
    catalog_snapshot_id: str
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)
    project_policy_overrides: dict[str, RoutingPolicy] = field(default_factory=dict)
    task_policy_overrides: dict[str, RoutingPolicy] = field(default_factory=dict)
    activation_gate: ActiveRoutingGate = field(default_factory=ActiveRoutingGate)
    policy_journal: PolicySnapshotJournal | None = None
    task_profiles: dict[str, TaskProfile] = field(default_factory=dict)
    runtime_availability: dict[str, bool] = field(default_factory=dict)
    telemetry: dict[str, TargetTelemetry] = field(default_factory=dict)
    connected_provider_ids: frozenset[str] | None = None
    connected_provider_ids_provider: Callable[[], frozenset[str]] | None = None
    shadow_journal: ShadowEvidenceJournal | None = None
    shadow_actual_execution_targets: dict[str, str] = field(default_factory=dict)
    scheduling_settings: Any = None

    def _effective_registry(self) -> ModelRegistry:
        return self.registry_provider() if self.registry_provider is not None else self.registry

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

    def _global_policy(self) -> RoutingPolicy:
        """The global default, preferring durable host-side settings when attached."""

        if self.scheduling_settings is None:
            return self.policy
        resolved = policy_from_name(
            self.scheduling_settings.default_policy,
            base=self.policy,
        )
        return resolved if resolved is not None else self.policy

    def _resolved_policy(self, request: RoutingRequest) -> SchedulingPolicyResolution:
        """Resolve task > project > global using durable state.

        The in-memory ``*_policy_overrides`` dicts remain supported and win over the
        durable record, so callers that construct a service directly (tests, embedded
        runs) keep working. In the product path they are empty and every level comes
        from SQLite, which is what makes a submitted task's policy outlive the App.
        """

        task = None
        if request.task_id is not None:
            try:
                task = self.store.get_task(request.task_id)
            except KeyError:
                task = None

        task_policy = (
            self.task_policy_overrides.get(request.task_id)
            if request.task_id is not None
            else None
        )
        if task_policy is None and task is not None:
            task_policy = policy_from_name(
                task.scheduling_policy,
                manual_execution_target_id=task.manual_execution_target_id,
                base=self.policy,
            )

        project_id = task.project_id if task is not None else request.project_id
        project_policy = (
            self.project_policy_overrides.get(project_id) if project_id is not None else None
        )
        if project_policy is None and project_id is not None:
            try:
                project = self.store.get_project(project_id)
            except KeyError:
                project = None
            if project is not None:
                project_policy = policy_from_name(
                    project.scheduling_policy,
                    manual_execution_target_id=project.manual_execution_target_id,
                    base=self.policy,
                )

        return resolve_scheduling_policy(
            global_default=self._global_policy(),
            project_override=project_policy,
            task_override=task_policy,
        )

    def _no_selection(
        self,
        request: RoutingRequest,
        reason: str,
        *,
        policy: RoutingPolicy,
    ) -> SchedulerDecision:
        return SchedulerDecision(
            task_id=request.task_id or "unattached",
            policy_id=policy.objective.value,
            policy_objective=policy.objective,
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
        policy: RoutingPolicy,
    ) -> SchedulerDecision:
        profile = self.task_profiles.get(request.task_id or "")
        if profile is None:
            return self._no_selection(
                request,
                "no authoritative TaskProfile is attached to this routing request",
                policy=policy,
            )

        try:
            task = self.store.get_task(profile.task_id)
        except KeyError:
            return self._no_selection(
                request,
                "task profile is not backed by durable Safety Kernel task state",
                policy=policy,
            )

        if task.state not in _ROUTABLE_TASK_STATES:
            return self._no_selection(
                request,
                f"task state {task.state.value} is not eligible for model routing",
                policy=policy,
            )
        if (
            request.task_state_version is not None
            and request.task_state_version != task.state_version
        ):
            return self._no_selection(
                request,
                "routing request carries a stale task state version",
                policy=policy,
            )
        if request.mode is RoutingMode.ACTIVE and request.task_state_version is None:
            return self._no_selection(
                request,
                "ACTIVE routing requires an exact task state version",
                policy=policy,
            )

        return route_task(
            self._effective_registry(),
            task=profile,
            now=reference,
            known_at=request.requested_at,
            runtime_availability=self.runtime_availability,
            telemetry=self.telemetry,
            policy=policy,
            connected_provider_ids=(
                self.connected_provider_ids_provider()
                if self.connected_provider_ids_provider is not None
                else self.connected_provider_ids
            ),
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
        actual_target = self._effective_registry().execution_targets.get(manual_target_id)
        if actual_target is not None:
            target = actual_target
            model = self._effective_registry().models[target.model_sku_id]
            provider_id = model.provider_id
        if actual_evaluation is not None:
            quota_pool_id = actual_evaluation.quota_pool_id
            predicted_burn_fraction = actual_evaluation.predicted_burn_fraction
        elif selected_evaluation is not None:
            quota_pool_id = selected_evaluation.quota_pool_id
            predicted_burn_fraction = selected_evaluation.predicted_burn_fraction
        if quota_pool_id is not None:
            snapshot = self._effective_registry().quota_pools[quota_pool_id].snapshot
            quota_confidence = snapshot.confidence
            if snapshot.confidence.value == "UNKNOWN":
                collector_status = QuotaCollectionStatus.UNKNOWN
        profile = self.task_profiles.get(request.task_id)
        task_family = "unknown"
        if profile is not None:
            task_family = (
                profile.pool.value.lower()
                if profile.task_family == "unknown"
                else profile.task_family
            )

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
                task_family=task_family,
                quota_confidence=quota_confidence,
                collector_status=collector_status,
                predicted_burn_fraction=predicted_burn_fraction,
                started_at=reference,
            )
        )

    def _policy_resolution_record(
        self,
        request: RoutingRequest,
        resolution: SchedulingPolicyResolution,
    ) -> dict[str, object]:
        """Freeze how this decision's policy was chosen, so history stays truthful.

        Every level is recorded, not just the winner. A later Settings change rewrites
        none of this: the decision row already holds the global default that applied
        at the time it was made.
        """

        task_requested: str | None = None
        manual_target: str | None = None
        project_requested: str | None = None
        if request.task_id is not None:
            try:
                task = self.store.get_task(request.task_id)
            except KeyError:
                task = None
            if task is not None:
                task_requested = task.scheduling_policy
                manual_target = task.manual_execution_target_id
                if task.project_id is not None:
                    try:
                        project_requested = self.store.get_project(
                            task.project_id
                        ).scheduling_policy
                    except KeyError:
                        project_requested = None
        return {
            "requested_task_policy": task_requested,
            "manual_execution_target_id": manual_target,
            "project_policy": project_requested,
            "global_default_policy": self._global_policy().objective.value,
            "resolved_policy": resolution.policy.objective.value,
            "resolution_source": resolution.resolved_level.value,
        }

    def route(self, request: RoutingRequest, *, now: datetime | None = None) -> RoutingDecision:
        existing = self._existing_decision(request)
        if existing is not None:
            return existing

        reference = now or datetime.now(UTC)
        resolved_policy = self._resolved_policy(request)
        policy_snapshot = PolicySnapshot.from_policy(resolved_policy.policy)
        if self.policy_journal is not None:
            self.policy_journal.append(policy_snapshot)

        scheduler = self._scheduler_decision(
            request,
            reference=reference,
            policy=resolved_policy.policy,
        )
        decision = build_routing_decision(
            request,
            scheduler,
            self._effective_registry(),
            catalog_snapshot_id=self.catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot.id,
            activation_gate=self.activation_gate,
            decided_at=reference,
            policy_resolution=self._policy_resolution_record(request, resolved_policy),
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
