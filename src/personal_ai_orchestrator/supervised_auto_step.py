"""M1 WP5a-2 — host-owned SUPERVISED_AUTO planning / grace / dispatch tick.

Authority chain (§4 of the WP5a-2 ruling)::

    DaemonSupervisor
        → supervised_auto_step (this module)
        → Safety Kernel durable task truth
        → scheduling mode + project settings
        → host recommendation (dispatch recommendation service)
        → frozen RoutingDecision
        → AUTO_PLANNED / AUTO_GRACE
        → host dispatch authority (``SUPERVISED_AUTO``)
        → DispatchExecutor
        → worker
        → deterministic verifier
        → final state

The tick is deterministic and fake-clock friendly: one invocation performs
one bounded sweep, never sleeps, never spawns threads, never reads
credentials and never executes shell commands. The ``DaemonSupervisor``
owns the periodic cadence; this module owns the per-tick semantics.

Safety invariants enforced here:

- Six hard gates (§3.4 step 1) evaluated before any side effect; an
  UNKNOWN / failing gate means ``AUTO_SKIPPED`` and no side effect.
- One frozen ``RoutingDecision`` per planning cycle — later ticks reuse the
  exact durable decision via the ``supervised-auto-{auto_decision_id}``
  request id (never re-scheduled, never a second decision).
- Attended projects (``unattended_allowed == False``) never count down
  before the owner's ack; the deadline is set once from the ack time and
  an ACK retry never extends it.
- Every dispatch revalidates mode, project opt-in, task state version and
  lease truth immediately before reserving (TOCTOU §8); the settings
  handler's same-transaction abort plus the executor's exact-expected-
  state guard close the remaining races fail-closed.
- 24h unacknowledged tasks abort back to READY (never parked forever).
- Every lifecycle exit either finalizes or discards the pending shadow;
  nothing hangs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from personal_ai_orchestrator.dispatch_initiator import (
    AUTHORITY_SUPERVISED_AUTO_EXECUTION,
    initiate_owner_dispatch,
)
from personal_ai_orchestrator.dispatch_recommender import (
    DispatchCandidateInput,
)
from personal_ai_orchestrator.execution_controller import (
    validate_execution_target_launch,
)
from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.opencode_contract import (
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
)
from personal_ai_orchestrator.policy_snapshot import PolicySnapshot
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import (
    ProjectRecord,
    SafetyKernelStore,
    TaskRecord,
    TaskState,
)
from personal_ai_orchestrator.scheduler import (
    RoutingPolicy,
    SchedulerDecision,
)
from personal_ai_orchestrator.shadow_evidence import (
    PendingShadowObservation,
    ShadowEvidenceJournal,
)
from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority

#: Public step name registered on the ``DaemonSupervisor``. Surfaced by
#: ``/v1/health`` so the owner can prove the autonomous tick is (not)
#: running — the control-only daemon deliberately never registers it.
SUPERVISED_AUTO_STEP_NAME = "supervised-auto"

#: Routing request id contract (§9): stable per auto decision, no
#: timestamps, no per-tick randomness.
def supervised_auto_routing_request_id(auto_decision_id: str) -> str:
    return f"supervised-auto-{auto_decision_id}"


#: Durable dispatch request id contract (§16).
def supervised_auto_dispatch_request_id(auto_decision_id: str) -> str:
    return f"supervised-auto-dispatch-{auto_decision_id}"


#: One planning cycle's stable decision id: derived from the task id and
#: the READY ``state_version`` at planning. A veto / abort / timeout bumps
#: the version, so the next planning cycle gets a fresh id while replays
#: of the same cycle (crash recovery) derive the same id and reuse the
#: frozen decision.
def supervised_auto_decision_id(task: TaskRecord) -> str:
    return f"auto-{task.task_id}-v{task.state_version}"


#: §3.4 step 3: an attended task that was never acked aborts after this
#: window instead of parking in AUTO_GRACE forever.
UNACKED_TIMEOUT_SECONDS = 24 * 3600

#: Evidence freshness cap for the planning gate (mirrors the recommender's
#: ``_EVIDENCE_FRESH_DAYS`` — a target without fresh real-execution
#: evidence is not eligible for autonomous planning).
_EVIDENCE_FRESH_SECONDS = 7 * 86_400

#: Quota states the planning gate accepts for autonomous dispatch (§3.4
#: step 1: UNKNOWN / UNCERTAIN_LOCKED / COOLDOWN never auto-dispatch).
_AUTO_OK_QUOTA_STATES = {
    QuotaAvailabilityState.AVAILABLE_OBSERVED,
    QuotaAvailabilityState.AVAILABLE_UNMETERED,
}

#: Post-RUNNING terminal states whose auto metadata is inert (§32): the
#: tick's sweep closes any lifecycle the executor failed to close.
_AUTO_TERMINAL_STATES = {
    TaskState.BLOCKED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.COMPLETED,
    TaskState.VERIFIED,
}


class SupervisedAutoStep:
    """One bounded SUPERVISED_AUTO sweep over the durable task truth."""

    def __init__(
        self,
        *,
        store: SafetyKernelStore,
        scheduling_settings: Any,
        owner_execution_enabled: Callable[[], bool],
        recommendation_service_factory: Callable[[], Any],
        project_provider: Callable[[str], ProjectRecord],
        registry_provider: Callable[[], Any],
        provider_registry_manager: Any | None,
        runtime_available_provider: Callable[[str], bool],
        execution_evidence_journal: Any | None,
        executor: Any | None,
        shadow_journal: ShadowEvidenceJournal | None,
        catalog_snapshot_id: str,
    ) -> None:
        self._store = store
        self._scheduling_settings = scheduling_settings
        self._owner_execution_enabled = owner_execution_enabled
        self._recommendation_service_factory = recommendation_service_factory
        self._project_provider = project_provider
        self._registry_provider = registry_provider
        self._provider_registry_manager = provider_registry_manager
        self._runtime_available_provider = runtime_available_provider
        self._execution_evidence_journal = execution_evidence_journal
        self._executor = executor
        self._shadow_journal = shadow_journal
        self._catalog_snapshot_id = catalog_snapshot_id
        self._lease_authority = SwitchLeaseAuthority(store)

    # ------------------------------------------------------------------
    # entrypoint
    # ------------------------------------------------------------------

    def tick(self, now: datetime) -> None:
        """One bounded sweep. Never raises for per-task gate failures."""

        try:
            self._abort_tasks_with_revoked_mode(now)
            self._abort_tasks_with_revoked_project(now)
            if self._mode() == "SUPERVISED_AUTO":
                for task in self._tasks_in_state(TaskState.READY):
                    self._try_plan(task, now)
                for task in self._tasks_in_state(
                    TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE
                ):
                    self._advance(task, now)
            self._reconcile_supervised_auto_dispatches(now)
            self._close_terminal_auto_lifecycles()
        except Exception as error:  # noqa: BLE001 — bounded tick, audit + continue
            self._safe_system_event(
                "SUPERVISED_AUTO_TICK_FAILED",
                {"exc_type": type(error).__name__, "message": str(error)[:256]},
            )

    # ------------------------------------------------------------------
    # mode / project revocation sweeps (fail-closed §8)
    # ------------------------------------------------------------------

    def _abort_tasks_with_revoked_mode(self, now: datetime) -> None:
        """AUTO_* tasks are illegal once the mode left SUPERVISED_AUTO.

        The settings PUT aborts them in the same handler transaction; this
        sweep is the crash-recovery backstop (mode file rewritten while the
        daemon was down, or an abort write lost to a crash).
        """

        if self._mode() == "SUPERVISED_AUTO":
            return
        for task in self._tasks_in_state(TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE):
            self._abort_auto_task(task, now, reason="mode_changed")

    def _abort_tasks_with_revoked_project(self, now: datetime) -> None:
        """AUTO_* tasks whose project revoked ``supervised_auto_allowed``.

        The project-settings PUT aborts them inline; this sweep is the
        crash-recovery backstop.
        """

        for task in self._tasks_in_state(TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE):
            project = self._project_for_task(task)
            if project is not None and not project.supervised_auto_allowed:
                self._abort_auto_task(task, now, reason="project_auto_disabled")

    # ------------------------------------------------------------------
    # planning (§3.4 step 1 + 2)
    # ------------------------------------------------------------------

    def _try_plan(self, task: TaskRecord, now: datetime) -> None:
        project = self._project_for_task(task)
        # Pre-conditions whose failure keeps the task READY with a deduped
        # skip reason (§3.4 step 1 scan conditions).
        if task.scheduling_policy == "MANUAL":
            self._skip(task, "task_policy_manual")
            return
        if project is None:
            self._skip(task, "task_has_no_project")
            return
        if not project.supervised_auto_allowed:
            self._skip(task, "project_supervised_auto_disabled")
            return
        if not self._owner_execution_enabled():
            self._skip(task, "owner_initiated_execution_disabled")
            return

        auto_decision_id = supervised_auto_decision_id(task)
        request_id = supervised_auto_routing_request_id(auto_decision_id)

        # Frozen-decision contract (§9/§10): a durable decision for this
        # cycle already exists → reuse it byte-for-byte. This covers the
        # crash boundary "decision persisted, task not yet AUTO_PLANNED"
        # without ever scheduling a second decision for the same cycle.
        decision = self._frozen_decision(request_id, task)
        candidates_by_id: dict[str, DispatchCandidateInput] = {}
        if decision is None:
            recommendation, candidates, invalid_min_tier = (
                self._recommendation_service_factory().recommend_for_task(
                    task,
                    policy=self._planning_policy(task),
                    now=now,
                )
            )
            if invalid_min_tier is not None:
                self._skip(task, "min_tier_invalid")
                return
            candidates_by_id = {c.execution_target_id: c for c in candidates}
            top = recommendation.top_pick
            if top is None or not top.admitted:
                self._skip(task, "no_admitted_target")
                return
            candidate = candidates_by_id.get(top.execution_target_id)
            if candidate is None:  # pragma: no cover — recommender invariant
                self._skip(task, "no_admitted_target")
                return

            # --- six hard gates (§3.4 step 1), all before side effects --
            if candidate.availability_state not in _AUTO_OK_QUOTA_STATES:
                self._skip(
                    task,
                    f"quota_state_{candidate.availability_state.value}",
                )
                return
            if not self._evidence_fresh(candidate, now):
                self._skip(task, "evidence_stale")
                return
            # tier ≥ min_tier is guaranteed by the recommender's admission
            # (tier_below_minimum is a hard-ineligibility reason there).
            try:
                validate_execution_target_launch(
                    self._registry_provider(),
                    execution_target_id=top.execution_target_id,
                    runtime_available=self._runtime_available_provider(
                        top.execution_target_id
                    ),
                    execution_evidence_journal=self._execution_evidence_journal,
                )
            except RuntimeError:
                # Reuses the existing execution-repo launch policy as the
                # "protected execution surface" gate (§3.4 step 1 item 5).
                self._skip(task, "execution_target_not_launchable")
                return
            if self._lease_authority.has_active_lease(task.task_id, now=now):
                self._skip(task, "active_switch_lease")
                return
            if self._has_active_run(task.task_id):
                self._skip(task, "active_run")
                return

            decision = self._freeze_decision(
                task=task,
                request_id=request_id,
                target_id=top.execution_target_id,
                decision_reason="; ".join(top.reasons),
                now=now,
            )
            if decision is None or decision.selected_execution_target_id is None:
                # The frozen record could not prove a target — fail closed
                # and keep the task READY for the owner.
                self._skip(task, "no_admitted_target")
                return

        target_id = decision.selected_execution_target_id
        if target_id is None:  # pragma: no cover — guarded above
            self._abort_auto_task(task, now, reason="frozen_decision_target_missing")
            return

        # Pending shadow (裁决 15): pending_id == auto_decision_id.
        self._append_pending_shadow(
            task=task,
            auto_decision_id=auto_decision_id,
            decision=decision,
            target_id=target_id,
            now=now,
        )

        planned = self._store.transition_task(
            task.task_id,
            TaskState.AUTO_PLANNED,
            expected_version=task.state_version,
            reason="supervised auto planning passed all hard gates",
            auto_decision_id=auto_decision_id,
            auto_reason=f"AUTO_PLANNED target={target_id}",
        )
        self._audit(
            task.task_id,
            "AUTO_PLANNED",
            {
                "auto_decision_id": auto_decision_id,
                "routing_decision_id": decision.decision_id,
                "target": target_id,
            },
        )
        self._promote_to_grace(planned, project, now)

    def _promote_to_grace(
        self, task: TaskRecord, project: ProjectRecord, now: datetime
    ) -> TaskRecord:
        """AUTO_PLANNED → AUTO_GRACE with the §3.4 step 3 deadline rules."""

        deadline: str | None = None
        if project.unattended_allowed:
            deadline = (now + timedelta(seconds=project.grace_seconds)).isoformat()
        if deadline is not None:
            updated = self._store.transition_task(
                task.task_id,
                TaskState.AUTO_GRACE,
                expected_version=task.state_version,
                reason="supervised auto grace window started",
                auto_grace_deadline_at=deadline,
            )
        else:
            updated = self._store.transition_task(
                task.task_id,
                TaskState.AUTO_GRACE,
                expected_version=task.state_version,
                reason="supervised auto grace window awaiting owner ack",
            )
        return updated

    # ------------------------------------------------------------------
    # AUTO_* advancement (grace countdown / dispatch / timeouts)
    # ------------------------------------------------------------------

    def _advance(self, task: TaskRecord, now: datetime) -> None:
        if task.state is TaskState.AUTO_PLANNED:
            # Crash recovery: the planning transaction committed the
            # AUTO_PLANNED edge but died before AUTO_GRACE. Complete the
            # promotion using the frozen decision's target.
            project = self._project_for_task(task)
            if project is None or not project.supervised_auto_allowed:
                self._abort_auto_task(task, now, reason="project_auto_disabled")
                return
            if self._mode() != "SUPERVISED_AUTO":
                self._abort_auto_task(task, now, reason="mode_changed")
                return
            self._promote_to_grace(task, project, now)
            return

        # task.state is AUTO_GRACE — revalidate the lifecycle preconditions
        # before doing anything else (mode + project are the §8 aborts).
        if self._mode() != "SUPERVISED_AUTO":
            self._abort_auto_task(task, now, reason="mode_changed")
            return
        project = self._project_for_task(task)
        if project is None or not project.supervised_auto_allowed:
            self._abort_auto_task(task, now, reason="project_auto_disabled")
            return

        deadline_raw = task.auto_grace_deadline_at
        if deadline_raw is None:
            # Attended and not yet acked: no countdown. But §3.4 step 3's
            # 24h cap applies — an unacked task must not park forever.
            # ``updated_at`` for an unacked AUTO_GRACE row is exactly the
            # grace-entry timestamp (the ack write is the only later write
            # and it implies a deadline).
            if task.auto_acked_at is not None:
                # Acked rows always carry a deadline; a missing deadline on
                # an acked row is corrupt control state — fail closed.
                self._abort_auto_task(task, now, reason="grace_deadline_missing")
                return
            entered_at = task.updated_at
            if now - entered_at > timedelta(seconds=UNACKED_TIMEOUT_SECONDS):
                self._abort_auto_task(task, now, reason="unacked_timeout")
                return
            return  # still waiting for the owner's ack — no dispatch

        deadline = datetime.fromisoformat(deadline_raw)
        if now < deadline:
            return  # countdown still running
        self._dispatch_auto_task(task, now, trigger="grace_expired")

    def _dispatch_auto_task(
        self, task: TaskRecord, now: datetime, *, trigger: str
    ) -> None:
        """§3.4 step 4: re-admit and dispatch, or abort fail-closed.

        Revalidation happens immediately before the durable reservation so
        a mode change / veto / version bump between the grace read and this
        point can never dispatch (TOCTOU §8). Fresh quota admission runs
        inside the executor at worker-start time — planning-time data is
        never trusted for the billable launch.
        """

        from personal_ai_orchestrator.control_api import ControlPlaneError

        # TOCTOU revalidation: re-read the authoritative row.
        fresh = self._store.get_task(task.task_id)
        if fresh.state is not TaskState.AUTO_GRACE or fresh.auto_decision_id is None:
            return  # a concurrent veto/abort owns the outcome
        if fresh.state_version != task.state_version:
            return  # state moved under us — next tick re-evaluates
        if self._mode() != "SUPERVISED_AUTO":
            self._abort_auto_task(fresh, now, reason="mode_changed")
            return
        project = self._project_for_task(fresh)
        if project is None or not project.supervised_auto_allowed:
            self._abort_auto_task(fresh, now, reason="project_auto_disabled")
            return
        decision = self._frozen_decision(
            supervised_auto_routing_request_id(fresh.auto_decision_id), fresh
        )
        target_id = decision.selected_execution_target_id if decision else None
        if decision is None or target_id is None:
            self._abort_auto_task(fresh, now, reason="frozen_decision_missing")
            return
        if self._lease_authority.has_active_lease(fresh.task_id, now=now):
            self._skip(fresh, "active_switch_lease")
            return
        if self._has_active_run(fresh.task_id):
            return

        request_id = supervised_auto_dispatch_request_id(fresh.auto_decision_id)
        # Idempotent dispatch (§16): a durable reservation for this exact
        # decision already exists → never reserve a second one. Crash
        # boundary C (reserved but the executor thread died before the
        # worker started) re-enters the executor — ``execute_async``
        # re-validates the RESERVED status + expected state atomically, so
        # a re-spawn can never produce a second worker.
        try:
            existing_dispatch = self._store.get_owner_dispatch_by_request_id(request_id)
        except KeyError:
            existing_dispatch = None
        if existing_dispatch is not None:
            if (
                existing_dispatch.status == "RESERVED"
                and self._executor is not None
                and not self._has_active_run(fresh.task_id)
            ):
                live_registry = getattr(
                    self._executor, "execution_supervisor", None
                )
                live = None if live_registry is None else live_registry.get(fresh.task_id)
                if live is None:
                    threading.Thread(
                        target=self._executor.execute,
                        args=(request_id,),
                        name=f"supervised-auto-recovery-{request_id}",
                        daemon=True,
                    ).start()
                    self._audit(
                        fresh.task_id,
                        "AUTO_DISPATCHED",
                        {
                            "auto_decision_id": fresh.auto_decision_id,
                            "target": target_id,
                            "trigger": trigger,
                            "request_id": request_id,
                            "recovery": True,
                        },
                    )
            return
        try:
            initiate_owner_dispatch(
                self._store,
                self._executor,
                task=fresh,
                request_id=request_id,
                task_state_version=fresh.state_version,
                execution_target_id=target_id,
                authority=AUTHORITY_SUPERVISED_AUTO_EXECUTION,
                project_provider=self._project_provider,
                registry_provider=self._registry_provider,
                provider_registry_manager=self._provider_registry_manager,
                runtime_available_provider=self._runtime_available_provider,
                execution_evidence_journal=self._execution_evidence_journal,
                expected_state=TaskState.AUTO_GRACE,
            )
        except (ControlPlaneError, ValueError, RuntimeError, KeyError):
            # Validation failed after the reservation attempt. The dispatch
            # row carries the sanitized failure; the task returns to the
            # owner-controlled READY state with the reason audited (§3.4
            # step 4's fail branch, generalized to every rejection code).
            self._abort_auto_task(
                fresh,
                now,
                reason="dispatch_rejected",
            )
            return
        self._audit(
            fresh.task_id,
            "AUTO_DISPATCHED",
            {
                "auto_decision_id": fresh.auto_decision_id,
                "target": target_id,
                "trigger": trigger,
                "request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # dispatch reconciliation (§20 crash boundaries B–D)
    # ------------------------------------------------------------------

    def _reconcile_supervised_auto_dispatches(self, now: datetime) -> None:
        """Reconcile SUPERVISED_AUTO dispatch rows that ended pre-worker.

        A dispatch that never started a worker (no run row, terminal
        BLOCKED status) is §3.4 step 4's fail branch: the task returns to
        READY, the pending shadow is discarded and the abort is audited.
        Dispatches that DID start a worker keep their outcome — the
        verifier + executor path owns that truth.
        """

        rows = self._store.connection.execute(
            """
            SELECT d.request_id, d.task_id, d.status, d.failure_code
            FROM owner_dispatches d
            WHERE d.authority = ?
              AND d.status IN ('BLOCKED', 'CANCELLED')
            ORDER BY d.created_at
            """,
            (AUTHORITY_SUPERVISED_AUTO_EXECUTION,),
        ).fetchall()
        for row in rows:
            try:
                task = self._store.get_task(row["task_id"])
            except KeyError:
                continue
            has_run = self._store.connection.execute(
                "SELECT 1 FROM runs WHERE task_id=? LIMIT 1", (row["task_id"],)
            ).fetchone()
            if has_run is not None:
                continue  # a real worker ran — executor path owns the truth
            if task.state not in {TaskState.BLOCKED, TaskState.AUTO_GRACE}:
                continue
            reason = f"admission_failed:{row['failure_code'] or row['status']}"
            task = self._store.transition_task(
                task.task_id,
                TaskState.READY,
                expected_version=task.state_version,
                reason=f"supervised auto dispatch aborted pre-worker: {reason}",
            )
            self._audit(
                task.task_id,
                "AUTO_ABORTED",
                {
                    "auto_decision_id": task.auto_decision_id,
                    "reason": reason,
                    "target": None,
                },
            )
            self._finalize_abort(task, now, reason=reason)

    # ------------------------------------------------------------------
    # terminal lifecycle close-out (§32)
    # ------------------------------------------------------------------

    def _close_terminal_auto_lifecycles(self) -> None:
        """Post-RUNNING terminal tasks must not carry active-auto metadata.

        The executor clears the metadata itself on every close-out; this
        sweep is the durable backstop when the executor died between the
        terminal transition and the clear (crash boundary D).
        """

        rows = self._store.connection.execute(
            "SELECT task_id FROM tasks WHERE auto_decision_id IS NOT NULL "
            "AND state IN (?,?,?,?,?)",
            tuple(state.value for state in _AUTO_TERMINAL_STATES),
        ).fetchall()
        for row in rows:
            try:
                task = self._store.get_task(row["task_id"])
            except KeyError:
                continue
            if task.auto_decision_id is None:  # pragma: no cover — raced clear
                continue
            self._discard_pending(task.auto_decision_id)
            self._store.clear_auto_state_metadata(
                task.task_id,
                expected_version=task.state_version,
                reason="terminal state sweep closed the auto lifecycle",
            )

    # ------------------------------------------------------------------
    # shared abort path
    # ------------------------------------------------------------------

    def _abort_auto_task(self, task: TaskRecord, now: datetime, *, reason: str) -> None:
        """Fail-closed exit: AUTO_* → READY + audit + pending discard + clear."""

        fresh = self._store.get_task(task.task_id)
        if fresh.state not in {TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE}:
            return
        updated = self._store.transition_task(
            task.task_id,
            TaskState.READY,
            expected_version=fresh.state_version,
            reason=f"supervised auto aborted: {reason}",
            auto_reason=f"AUTO_ABORTED reason={reason}",
        )
        self._audit(
            task.task_id,
            "AUTO_ABORTED",
            {
                "auto_decision_id": fresh.auto_decision_id,
                "reason": reason,
                "target": None,
            },
        )
        self._finalize_abort(updated, now, reason=reason)

    def _finalize_abort(self, task: TaskRecord, now: datetime, *, reason: str) -> None:
        """Discard the pending shadow and clear metadata after an abort."""

        if task.auto_decision_id is not None:
            self._discard_pending(task.auto_decision_id)
        try:
            self._store.clear_auto_state_metadata(
                task.task_id,
                expected_version=task.state_version,
                reason=f"supervised auto aborted: {reason}",
            )
        except RuntimeError:
            # Version moved during the abort — the next sweep retries; the
            # READY transition already made the state owner-controlled.
            pass

    # ------------------------------------------------------------------
    # frozen decision helpers
    # ------------------------------------------------------------------

    def _frozen_decision(self, request_id: str, task: TaskRecord) -> RoutingDecision | None:
        import json as _json

        row = self._store.routing_decision_by_request_id(request_id)
        if row is None:
            return None
        if row["task_id"] != task.task_id:
            return None  # request id collision on a different task — abort path
        try:
            return RoutingDecision.model_validate(_json.loads(row["payload_json"]))
        except ValueError:
            return None

    def _freeze_decision(
        self,
        *,
        task: TaskRecord,
        request_id: str,
        target_id: str,
        decision_reason: str,
        now: datetime,
    ) -> RoutingDecision | None:
        """Create + persist the one frozen RoutingDecision for this cycle."""

        registry = self._registry_provider()
        target = registry.execution_targets.get(target_id)
        if target is None:  # pragma: no cover — admission implies registry
            return None
        policy_snapshot = PolicySnapshot.from_policy(RoutingPolicy())
        scheduler = SchedulerDecision(
            task_id=task.task_id,
            policy_id=policy_snapshot.id,
            policy_objective=policy_snapshot.policy.objective,
            selected_execution_target_id=target_id,
            selected_model_sku_id=target.model_sku_id,
            evaluations=(),
            decision_reason=decision_reason,
        )
        request = RoutingRequest(
            request_id=request_id,
            session_id="supervised-auto",
            task_id=task.task_id,
            task_state_version=task.state_version,
            mode=RoutingMode.SUPERVISED_AUTO,
            requested_at=now,
        )
        try:
            decision = build_routing_decision(
                request,
                scheduler,
                registry,
                catalog_snapshot_id=self._catalog_snapshot_id,
                policy_snapshot_id=policy_snapshot.id,
                activation_gate=None,
                decided_at=now,
            )
            self._store.record_routing_decision(
                decision_id=decision.decision_id,
                request_id=decision.request_id,
                task_id=task.task_id,
                payload=decision.model_dump(mode="json"),
            )
        except (ValueError, KeyError):
            # A conflicting durable decision for this request id means a
            # previous cycle persisted it — reuse is handled by the caller
            # reading the frozen row first; any other failure fails closed.
            return None
        return decision

    def _append_pending_shadow(
        self,
        *,
        task: TaskRecord,
        auto_decision_id: str,
        decision: RoutingDecision,
        target_id: str,
        now: datetime,
    ) -> None:
        if self._shadow_journal is None:
            return
        registry = self._registry_provider()
        target = registry.execution_targets.get(target_id)
        provider_id = None
        if target is not None:
            model = registry.models.get(target.model_sku_id)
            if model is not None:
                provider_id = model.provider_id
        try:
            quota_snapshot_ids = decision.quota_snapshot_ids
            if not quota_snapshot_ids:
                # ``ShadowObservation`` requires at least one quota
                # snapshot id. The frozen decision (built from the
                # recommendation path) may carry none — derive the real
                # registry snapshot for the selected target's provider,
                # falling back to the established ``quota-unknown``
                # convention when the registry has no pool for it.
                derived: str | None = None
                if provider_id is not None and target is not None:
                    for pool in registry.quota_pools.values():
                        if (
                            pool.snapshot is not None
                            and pool.snapshot.provider_id == provider_id
                        ):
                            derived = pool.snapshot.id
                            break
                quota_snapshot_ids = (derived or "quota-unknown",)
            self._shadow_journal.append_pending(
                PendingShadowObservation(
                    pending_id=auto_decision_id,
                    task_id=task.task_id,
                    request_id=decision.request_id,
                    decision_id=decision.decision_id,
                    manual_execution_target_id=target_id,
                    scheduler_execution_target_id=target_id,
                    catalog_snapshot_id=decision.catalog_snapshot_id
                    or self._catalog_snapshot_id,
                    policy_snapshot_id=decision.policy_snapshot_id
                    or "policy-unknown",
                    quota_snapshot_ids=quota_snapshot_ids,
                    provider_id=provider_id,
                    task_family="unknown",
                    quota_confidence=EvidenceConfidence.UNKNOWN,
                    started_at=now,
                )
            )
        except (OSError, ValueError):
            # The pending shadow is observability, not authority; a journal
            # failure must not block (nor silently enable) the lifecycle.
            self._safe_system_event(
                "SUPERVISED_AUTO_PENDING_SHADOW_FAILED",
                {"auto_decision_id": auto_decision_id, "task_id": task.task_id},
            )

    def _discard_pending(self, pending_id: str) -> None:
        if self._shadow_journal is None:
            return
        try:
            self._shadow_journal.discard_pending(pending_id)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    # small utilities
    # ------------------------------------------------------------------

    def _mode(self) -> str:
        try:
            return str(self._scheduling_settings.mode)
        except Exception:  # pragma: no cover — settings are fail-closed
            return "MANUAL"

    def _planning_policy(self, task: TaskRecord):
        from personal_ai_orchestrator.scheduler import RoutingObjective

        name = task.scheduling_policy or "BALANCED"
        try:
            return RoutingObjective(name)
        except ValueError:
            return RoutingObjective.BALANCED

    def _project_for_task(self, task: TaskRecord) -> ProjectRecord | None:
        if task.project_id is None:
            return None
        try:
            return self._project_provider(task.project_id)
        except KeyError:
            return None

    def _tasks_in_state(self, *states: TaskState) -> list[TaskRecord]:
        rows = self._store.connection.execute(
            "SELECT task_id FROM tasks WHERE state IN "
            f"({','.join('?' for _ in states)}) ORDER BY task_id",
            tuple(state.value for state in states),
        ).fetchall()
        tasks: list[TaskRecord] = []
        for row in rows:
            try:
                tasks.append(self._store.get_task(row["task_id"]))
            except KeyError:  # pragma: no cover — concurrent delete
                continue
        return tasks

    def _has_active_run(self, task_id: str) -> bool:
        row = self._store.connection.execute(
            "SELECT 1 FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
            (task_id,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _evidence_fresh(candidate: DispatchCandidateInput, now: datetime) -> bool:
        if candidate.evidence_observed_at is None:
            return False
        age = (now - candidate.evidence_observed_at).total_seconds()
        return 0 <= age <= _EVIDENCE_FRESH_SECONDS

    def _skip(self, task: TaskRecord, reason: str) -> None:
        try:
            self._store.set_auto_reason(task.task_id, reason=reason)
        except (RuntimeError, ValueError):
            pass  # concurrent state change owns the row — next tick retries

    def _audit(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self._store.connection.execute("BEGIN IMMEDIATE")
            self._store._audit(task_id, event_type, payload)
            self._store.connection.execute("COMMIT")
        except Exception:
            if self._store.connection.in_transaction:
                self._store.connection.execute("ROLLBACK")

    def _safe_system_event(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self._store.record_system_event(event_type, payload)
        except Exception:
            pass


__all__ = [
    "SUPERVISED_AUTO_STEP_NAME",
    "SupervisedAutoStep",
    "UNACKED_TIMEOUT_SECONDS",
    "supervised_auto_decision_id",
    "supervised_auto_dispatch_request_id",
    "supervised_auto_routing_request_id",
]
