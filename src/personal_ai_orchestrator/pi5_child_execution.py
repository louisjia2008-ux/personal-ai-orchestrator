"""Host-owned PI-5 child execution through the existing PAO dispatch chain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.delegation_shadow import (
    DelegationShadowJournal,
    build_delegation_shadow_record,
)
from personal_ai_orchestrator.dispatch_initiator import (
    AUTHORITY_DELEGATED_CHILD_EXECUTION,
    initiate_owner_dispatch,
)
from personal_ai_orchestrator.dispatch_recommendation_service import DispatchRecommendationService
from personal_ai_orchestrator.pi5_broker import DelegationChildPlan, DelegationChildResult
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
)
from personal_ai_orchestrator.scheduler import RoutingObjective

_TERMINAL = frozenset({"VERIFIED", "BLOCKED", "FAILED", "CANCELLED", "COMPLETED"})


class PAODelegationChildPort:
    def __init__(
        self,
        *,
        state_db: str | Path,
        executor: Any,
        recommendation_factory: Callable[[SafetyKernelStore], DispatchRecommendationService],
        registry_provider: Callable[[], Any],
        runtime_available_provider: Callable[[str], bool],
        provider_registry_manager: Any = None,
        execution_evidence_journal: Any = None,
        delegation_shadow_journal: DelegationShadowJournal | None = None,
        timeout_seconds: float = 300.0,
        poll_seconds: float = 0.05,
    ) -> None:
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("child wait bounds must be positive")
        self.state_db = state_db
        self.executor = executor
        self.recommendation_factory = recommendation_factory
        self.registry_provider = registry_provider
        self.runtime_available_provider = runtime_available_provider
        self.provider_registry_manager = provider_registry_manager
        self.execution_evidence_journal = execution_evidence_journal
        self.delegation_shadow_journal = delegation_shadow_journal
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    @staticmethod
    def _result(plan: DelegationChildPlan, state: str, target: str | None = None):
        # No worker prose is needed to communicate deterministic child truth.
        return DelegationChildResult(
            child_task_id=plan.child_task_id,
            final_state=state,
            selected_execution_target_id=target,
            verified=state == "VERIFIED",
        )

    @staticmethod
    def _block(store: SafetyKernelStore, task_id: str) -> None:
        task = store.get_task(task_id)
        if task.state in (TaskState.SUBMITTED, TaskState.READY):
            store.transition_task(
                task_id,
                TaskState.BLOCKED,
                expected_version=task.state_version,
                reason="delegated child was not admitted for execution",
            )

    def _record_shadow(
        self,
        *,
        store: SafetyKernelStore,
        plan: DelegationChildPlan,
        recommendation: Any,
        candidates: list[Any],
    ) -> None:
        """Best-effort observation only; never changes child execution semantics."""
        journal = self.delegation_shadow_journal
        if journal is None:
            return
        try:
            record = build_delegation_shadow_record(
                store=store,
                registry=self.registry_provider(),
                parent_task_id=plan.parent_task_id,
                parent_run_id=plan.parent_run_id,
                child_task_id=plan.child_task_id,
                recommendation=recommendation,
                candidates=candidates,
            )
            journal.append(record)
            try:
                store.record_system_event(
                    "DELEGATION_SHADOW_RECORDED",
                    {
                        "observation_id": record.observation_id,
                        "parent_task_id": plan.parent_task_id,
                        "child_task_id": plan.child_task_id,
                        "policy_version": record.decision.policy_version,
                        "verdict": record.decision.verdict.value,
                    },
                )
            except Exception:
                pass
        except Exception:
            # SHADOW capture is deliberately non-authoritative.  Do not leak
            # provider/filesystem exception text into durable audit evidence.
            try:
                store.record_system_event(
                    "DELEGATION_SHADOW_RECORD_FAILED",
                    {
                        "parent_task_id": plan.parent_task_id,
                        "child_task_id": plan.child_task_id,
                        "reason_code": "SHADOW_CAPTURE_FAILED",
                    },
                )
            except Exception:
                pass

    async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
        store = SafetyKernelStore(self.state_db)
        target = None
        request_id = f"pi5-child-dispatch-{plan.child_task_id}"
        try:
            try:
                parent = store.get_task(plan.parent_task_id)
                run = store.connection.execute(
                    "SELECT task_id,status FROM runs WHERE run_id=?", (plan.parent_run_id,)
                ).fetchone()
                if (
                    run is None
                    or run["task_id"] != parent.task_id
                    or run["status"] != "RUNNING"
                    or parent.state is not TaskState.RUNNING
                    or parent.project_id != plan.project_id
                    or parent.base_sha != plan.base_sha
                    or parent.working_subpath != plan.working_subpath
                    or parent.task_id == plan.child_task_id
                    or parent.request_id.startswith("pi5-child-submit-")
                ):
                    return self._result(plan, "BLOCKED")
                store.get_project(plan.project_id)
                policy = RoutingObjective(parent.scheduling_policy or RoutingObjective.BALANCED)
                if policy is RoutingObjective.MANUAL or self.executor is None:
                    return self._result(plan, "BLOCKED")
                child = store.submit_task(
                    task_id=plan.child_task_id,
                    request_id=f"pi5-child-submit-{plan.child_task_id}",
                    intent=plan.intent,
                    project_id=plan.project_id,
                    base_sha=plan.base_sha,
                    working_subpath=parent.working_subpath,
                    scheduling_policy=policy.value,
                    min_tier=parent.min_tier,
                    delegated_parent=(parent.task_id, plan.parent_run_id),
                )
            except (KeyError, ValueError):
                return self._result(plan, "BLOCKED")

            # A durable dispatch freezes the target across replay/re-entry.
            try:
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                if (
                    dispatch.authority != AUTHORITY_DELEGATED_CHILD_EXECUTION
                    or dispatch.task_id != child.task_id
                ):
                    return self._result(plan, "BLOCKED")
                target = dispatch.execution_target_id
            except KeyError:
                if child.state.value in _TERMINAL:
                    return self._result(plan, child.state.value)
                service = self.recommendation_factory(store)
                recommendation, candidates, invalid_tier = service.recommend_for_task(
                    child,
                    policy=policy,
                )
                self._record_shadow(
                    store=store,
                    plan=plan,
                    recommendation=recommendation,
                    candidates=candidates,
                )
                pick = recommendation.top_pick
                if invalid_tier is not None or pick is None or not pick.admitted:
                    self._block(store, child.task_id)
                    return self._result(plan, "BLOCKED")
                target = pick.execution_target_id
                try:
                    initiate_owner_dispatch(
                        store,
                        self.executor,
                        task=child,
                        request_id=request_id,
                        task_state_version=child.state_version,
                        execution_target_id=target,
                        authority=AUTHORITY_DELEGATED_CHILD_EXECUTION,
                        project_provider=store.get_project,
                        registry_provider=self.registry_provider,
                        provider_registry_manager=self.provider_registry_manager,
                        runtime_available_provider=self.runtime_available_provider,
                        execution_evidence_journal=self.execution_evidence_journal,
                        expected_state=TaskState.READY,
                    )
                except Exception:
                    self._block(store, child.task_id)
                    return self._result(plan, "BLOCKED", target)

            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
            while True:
                child = store.get_task(plan.child_task_id)
                if child.state.value in _TERMINAL:
                    return self._result(plan, child.state.value, target)
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                if dispatch.status is OwnerDispatchStatus.BLOCKED:
                    self._block(store, child.task_id)
                    return self._result(plan, "BLOCKED", target)
                if asyncio.get_running_loop().time() >= deadline:
                    # Timeout is non-verification, never a fabricated terminal state.
                    return self._result(plan, child.state.value, target)
                await asyncio.sleep(self.poll_seconds)
        finally:
            store.close()
