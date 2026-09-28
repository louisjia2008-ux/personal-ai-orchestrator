"""Host-owned PI-5 child execution through the existing PAO dispatch chain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.delegation_evidence import (
    DelegationOutcomeJournal,
    DelegationOutcomePhase,
    build_delegation_outcome_record,
)
from personal_ai_orchestrator.delegation_quota_calibration import (
    DelegationQuotaCalibrationJournal,
    build_quota_baseline,
    compare_quota_after,
)
from personal_ai_orchestrator.delegation_shadow import (
    DelegationShadowJournal,
    build_delegation_shadow_record,
)
from personal_ai_orchestrator.dispatch_initiator import (
    AUTHORITY_DELEGATED_CHILD_EXECUTION,
    initiate_owner_dispatch,
)
from personal_ai_orchestrator.dispatch_recommendation_service import (
    DispatchRecommendationService,
)
from personal_ai_orchestrator.pi5_broker import (
    DelegationChildPlan,
    DelegationChildResult,
)
from personal_ai_orchestrator.pi5_identity import (
    delegation_child_dispatch_request_id,
    delegation_child_submit_request_id,
)
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
)
from personal_ai_orchestrator.scheduler import RoutingObjective

_TERMINAL = frozenset({"VERIFIED", "BLOCKED", "FAILED", "CANCELLED", "COMPLETED"})
_QUOTA_PAIR_MISSING = "comparable_quota_before_after_not_captured"


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
        delegation_outcome_journal: DelegationOutcomeJournal | None = None,
        task_profile_provider: Callable[[str], Any | None] | None = None,
        policy_resolution_provider: Callable[[SafetyKernelStore, Any], Any | None] | None = None,
        quota_calibration_enabled: bool = False,
        quota_refresh_service: Any = None,
        quota_calibration_journal: DelegationQuotaCalibrationJournal | None = None,
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
        self.delegation_outcome_journal = delegation_outcome_journal
        self.task_profile_provider = task_profile_provider
        self.policy_resolution_provider = policy_resolution_provider
        self.quota_calibration_enabled = quota_calibration_enabled
        self.quota_refresh_service = quota_refresh_service
        self.quota_calibration_journal = quota_calibration_journal
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    @staticmethod
    def _result(
        plan: DelegationChildPlan,
        state: str,
        target: str | None = None,
    ) -> DelegationChildResult:
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

    def _target_provider_id(self, execution_target_id: str | None) -> str | None:
        if execution_target_id is None:
            return None
        try:
            registry = self.registry_provider()
            target = registry.execution_targets[execution_target_id]
            return registry.models[target.model_sku_id].provider_id
        except Exception:
            return None

    def _capture_quota_baseline(self, store: SafetyKernelStore, shadow: Any) -> None:
        """Freeze current cached quota truth only; never performs network I/O."""
        if (
            not self.quota_calibration_enabled
            or self.quota_refresh_service is None
            or self.quota_calibration_journal is None
            or shadow.child_quota_pool_id is None
        ):
            return
        provider_id = self._target_provider_id(shadow.selected_child_execution_target_id)
        if provider_id is None:
            return
        try:
            snapshot = self.quota_refresh_service.snapshot_for_pool(shadow.child_quota_pool_id)
            baseline = build_quota_baseline(
                observation_id=shadow.observation_id,
                provider_id=provider_id,
                quota_pool_id=shadow.child_quota_pool_id,
                snapshot=snapshot,
            )
            if baseline is None:
                raise ValueError("baseline unavailable")
            self.quota_calibration_journal.append(baseline)
            store.record_system_event(
                "DELEGATION_QUOTA_BASELINE_RECORDED",
                {
                    "observation_id": shadow.observation_id,
                    "quota_pool_id": baseline.quota_pool_id,
                    "snapshot_id": baseline.snapshot_id,
                },
            )
        except Exception:
            try:
                store.record_system_event(
                    "DELEGATION_QUOTA_BASELINE_RECORD_FAILED",
                    {
                        "observation_id": shadow.observation_id,
                        "reason_code": "QUOTA_BASELINE_CAPTURE_FAILED",
                    },
                )
            except Exception:
                pass

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
                task_profile_provider=self.task_profile_provider,
                policy_resolution_provider=self.policy_resolution_provider,
            )
            journal.append(record)
            self._capture_quota_baseline(store, record)
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
            # SHADOW capture is deliberately non-authoritative. Do not leak
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

    def _quota_pair_for_child(self, shadow: Any) -> tuple[str | None, str | None, str | None]:
        """Perform at most one post-child read-only refresh in explicit campaign mode."""
        if (
            not self.quota_calibration_enabled
            or self.quota_refresh_service is None
            or self.quota_calibration_journal is None
        ):
            return None, None, None
        baseline = self.quota_calibration_journal.load(shadow.observation_id)
        if baseline is None:
            return None, None, "quota_calibration_baseline_missing"
        provider_id = self._target_provider_id(shadow.selected_child_execution_target_id)
        if provider_id is None or provider_id != baseline.provider_id:
            return None, None, "quota_calibration_provider_identity_mismatch"
        try:
            # Existing QuotaRefreshService is structurally read-only. This is the
            # single allowed post-child network observation for an enabled campaign.
            self.quota_refresh_service.refresh(provider_id)
            after = self.quota_refresh_service.snapshot_for_pool(baseline.quota_pool_id)
            comparison = compare_quota_after(baseline, after)
        except Exception:
            return None, None, "quota_calibration_refresh_failed"
        if not comparison.comparable:
            return (
                None,
                None,
                f"quota_calibration_{comparison.reason.value.lower()}",
            )
        return (
            comparison.quota_before_snapshot_id,
            comparison.quota_after_snapshot_id,
            None,
        )

    @staticmethod
    def _with_quota_pair(
        record: Any,
        before_id: str | None,
        after_id: str | None,
        limitation: str | None = None,
    ) -> Any:
        limitations = list(record.limitations)
        if before_id is not None and after_id is not None:
            limitations = [item for item in limitations if item != _QUOTA_PAIR_MISSING]
        elif limitation is not None and limitation not in limitations:
            limitations.append(limitation)
        return record.model_copy(
            update={
                "quota_before_snapshot_id": before_id,
                "quota_after_snapshot_id": after_id,
                "limitations": tuple(limitations),
            }
        )

    def _record_child_outcome(
        self,
        *,
        store: SafetyKernelStore,
        plan: DelegationChildPlan,
    ) -> None:
        shadow_journal = self.delegation_shadow_journal
        outcome_journal = self.delegation_outcome_journal
        if shadow_journal is None or outcome_journal is None:
            return
        try:
            observation_id = shadow_journal.observation_id(
                parent_run_id=plan.parent_run_id,
                child_task_id=plan.child_task_id,
            )
            # Append-only first observation wins. Re-entry must not trigger a
            # second calibration refresh or rewrite an earlier outcome.
            if (
                outcome_journal.load(
                    observation_id=observation_id,
                    phase=DelegationOutcomePhase.CHILD_FINAL,
                )
                is not None
            ):
                return
            shadow = shadow_journal.load(observation_id)
            if shadow is None:
                return
            record = build_delegation_outcome_record(
                store=store,
                registry=self.registry_provider(),
                shadow_record=shadow,
                phase=DelegationOutcomePhase.CHILD_FINAL,
            )
            before_id, after_id, limitation = self._quota_pair_for_child(shadow)
            record = self._with_quota_pair(record, before_id, after_id, limitation)
            outcome_journal.append(record)
            try:
                store.record_system_event(
                    "DELEGATION_CHILD_OUTCOME_RECORDED",
                    {
                        "observation_id": observation_id,
                        "outcome_id": record.outcome_id,
                        "child_task_id": plan.child_task_id,
                        "child_state": record.child_state,
                        "verified": record.child_verified,
                        "quota_pair_captured": bool(before_id and after_id),
                    },
                )
            except Exception:
                pass
        except Exception:
            try:
                store.record_system_event(
                    "DELEGATION_CHILD_OUTCOME_RECORD_FAILED",
                    {
                        "parent_task_id": plan.parent_task_id,
                        "child_task_id": plan.child_task_id,
                        "reason_code": "OUTCOME_CAPTURE_FAILED",
                    },
                )
            except Exception:
                pass

    def _finish_result(
        self,
        *,
        store: SafetyKernelStore,
        plan: DelegationChildPlan,
        state: str,
        target: str | None = None,
    ) -> DelegationChildResult:
        result = self._result(plan, state, target)
        if state in _TERMINAL:
            self._record_child_outcome(store=store, plan=plan)
        return result

    def record_parent_final_outcomes(
        self,
        *,
        parent_task_id: str,
        parent_run_id: str,
    ) -> None:
        """Append parent-final phases after the parent executor reaches terminal truth."""
        shadow_journal = self.delegation_shadow_journal
        outcome_journal = self.delegation_outcome_journal
        if shadow_journal is None or outcome_journal is None:
            return
        store = SafetyKernelStore(self.state_db)
        try:
            try:
                parent = store.get_task(parent_task_id)
            except KeyError:
                return
            if parent.state.value not in _TERMINAL:
                return
            for shadow in shadow_journal.records_for_parent_run(parent_run_id):
                try:
                    if (
                        outcome_journal.load(
                            observation_id=shadow.observation_id,
                            phase=DelegationOutcomePhase.PARENT_FINAL,
                        )
                        is not None
                    ):
                        continue
                    record = build_delegation_outcome_record(
                        store=store,
                        registry=self.registry_provider(),
                        shadow_record=shadow,
                        phase=DelegationOutcomePhase.PARENT_FINAL,
                    )
                    child_outcome = outcome_journal.load(
                        observation_id=shadow.observation_id,
                        phase=DelegationOutcomePhase.CHILD_FINAL,
                    )
                    if child_outcome is not None:
                        record = self._with_quota_pair(
                            record,
                            child_outcome.quota_before_snapshot_id,
                            child_outcome.quota_after_snapshot_id,
                        )
                    outcome_journal.append(record)
                except Exception:
                    try:
                        store.record_system_event(
                            "DELEGATION_PARENT_OUTCOME_RECORD_FAILED",
                            {
                                "parent_task_id": parent_task_id,
                                "parent_run_id": parent_run_id,
                                "reason_code": "OUTCOME_CAPTURE_FAILED",
                            },
                        )
                    except Exception:
                        pass
        finally:
            store.close()

    async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult:
        store = SafetyKernelStore(self.state_db)
        target = None
        request_id = delegation_child_dispatch_request_id(plan.child_task_id)
        try:
            try:
                parent = store.get_task(plan.parent_task_id)
                run = store.connection.execute(
                    "SELECT task_id,status FROM runs WHERE run_id=?",
                    (plan.parent_run_id,),
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
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                    )
                store.get_project(plan.project_id)
                policy = RoutingObjective(parent.scheduling_policy or RoutingObjective.BALANCED)
                if policy is RoutingObjective.MANUAL or self.executor is None:
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                    )
                child = store.submit_task(
                    task_id=plan.child_task_id,
                    request_id=delegation_child_submit_request_id(plan.child_task_id),
                    intent=plan.intent,
                    project_id=plan.project_id,
                    base_sha=plan.base_sha,
                    working_subpath=parent.working_subpath,
                    scheduling_policy=policy.value,
                    min_tier=parent.min_tier,
                    delegated_parent=(parent.task_id, plan.parent_run_id),
                )
            except (KeyError, ValueError):
                return self._finish_result(
                    store=store,
                    plan=plan,
                    state="BLOCKED",
                )

            # A durable dispatch freezes the target across replay/re-entry.
            try:
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                if (
                    dispatch.authority != AUTHORITY_DELEGATED_CHILD_EXECUTION
                    or dispatch.task_id != child.task_id
                ):
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                    )
                target = dispatch.execution_target_id
            except KeyError:
                if child.state.value in _TERMINAL:
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state=child.state.value,
                    )
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
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                    )
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
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                        target=target,
                    )

            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
            while True:
                child = store.get_task(plan.child_task_id)
                if child.state.value in _TERMINAL:
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state=child.state.value,
                        target=target,
                    )
                dispatch = store.get_owner_dispatch_by_request_id(request_id)
                if dispatch.status is OwnerDispatchStatus.BLOCKED:
                    self._block(store, child.task_id)
                    return self._finish_result(
                        store=store,
                        plan=plan,
                        state="BLOCKED",
                        target=target,
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    # Timeout is non-verification, never a fabricated terminal state.
                    return self._result(plan, child.state.value, target)
                await asyncio.sleep(self.poll_seconds)
        finally:
            store.close()
