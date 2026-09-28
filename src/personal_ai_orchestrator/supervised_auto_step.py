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
one bounded sweep of host-owned work — it never sleeps, never reads
credentials and never executes shell commands. Its own dispatch
admission hands execution to the existing ``DispatchExecutor``
boundary, and the reserved-dispatch crash-recovery path may spawn an
executor thread for an already-durable reservation (round 6 §37: the
tick itself performs no worker execution; the only thread hand-off is
re-entering the executor for a reservation the executor owns). The
``DaemonSupervisor`` owns the periodic cadence; this module owns the
per-tick semantics.

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

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
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
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    ProjectRecord,
    SafetyKernelStore,
    ShadowFinalizationIntent,
    TaskRecord,
    TaskState,
    owner_dispatch_matches_expected,
    shadow_identity_payload,
)
from personal_ai_orchestrator.scheduler import (
    RoutingPolicy,
    SchedulerDecision,
)
from personal_ai_orchestrator.shadow_evidence import (
    PendingShadowObservation,
    ShadowEvidenceJournal,
    ShadowFailureClass,
    ShadowFailureStage,
    ShadowObservation,
    ShadowQualityOutcome,
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


#: Durable dispatch request id contract (§16). The prefix is the
#: RESERVED INTERNAL NAMESPACE of the SUPERVISED_AUTO authority (round
#: 5 §4): owner-supplied dispatch request ids may never occupy it, and
#: AUTO crash recovery only ever re-admits a row whose request id uses
#: it with an exact identity match.
SUPERVISED_AUTO_DISPATCH_REQUEST_PREFIX = "supervised-auto-dispatch-"


def is_reserved_auto_dispatch_request_id(request_id: str) -> bool:
    """Round 5 §4 — does this request id occupy the internal AUTO namespace?"""

    return request_id.startswith(SUPERVISED_AUTO_DISPATCH_REQUEST_PREFIX)


def supervised_auto_dispatch_request_id(auto_decision_id: str) -> str:
    return f"{SUPERVISED_AUTO_DISPATCH_REQUEST_PREFIX}{auto_decision_id}"


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
    # Strong positive evidence after proven exhaustion is a recovered,
    # host-observed capacity state. Keep the state distinct for audit while
    # admitting it wherever ordinary observed quota is safe for AUTO.
    QuotaAvailabilityState.RECOVERED_OBSERVED,
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


def drain_auto_shadow_cleanup_outbox(
    store: SafetyKernelStore,
    shadow_journal: ShadowEvidenceJournal | None,
    *,
    limit: int = 64,
) -> int:
    """Drain durable shadow-cleanup intents (crash-consistent closeout).

    The abort/veto/metadata-clear transactions in ``SafetyKernelStore``
    commit the authoritative SQLite truth (READY + metadata NULL) and an
    outbox row promising the pending-shadow discard. This drain fulfils
    that promise *after* the commit, so a crash at any point is safe:

    - crash after COMMIT, before discard → the row is still open → the
      next drain retries the discard;
    - crash after discard, before the completed marker → the next drain
      re-discards (``discard_pending`` is idempotent — an absent file is
      success) and marks completed;
    - a nonexistent pending is treated as a successful cleanup;
    - an unavailable / malformed journal only leaves the row open for the
      next drain — it can never roll back the already-safe task state.

    Returns the number of intents completed by this call.
    """

    completed = 0
    for row in store.pending_shadow_cleanups(limit=limit):
        pending_id = row["pending_id"]
        if store.open_shadow_finalize_intent(pending_id):
            # §16 conflict rule: finalize outranks discard — never let
            # the cleanup path remove a pending whose real-execution
            # observation is still owed.
            continue
        if shadow_journal is None:
            # §17: no journal means the discard cannot be performed;
            # the intent must stay OPEN for a later drain, never be
            # marked completed.
            continue
        try:
            shadow_journal.discard_pending(pending_id)
        except (OSError, ValueError):
            # Journal unavailable or malformed: leave the intent open;
            # the SQLite task truth is already owner-safe.
            continue
        store.complete_shadow_cleanup(pending_id)
        completed += 1
    return completed


def _finalize_kwargs_from_intent_row(row: Any) -> dict[str, Any]:
    """Rebuild ``finalize_pending`` kwargs from a durable intent row."""

    def _enum(cls: type, value: Any) -> Any:
        return cls(value) if value is not None else None

    return {
        "reset_cycle_ids": tuple(json.loads(row["reset_cycle_ids"])),
        "quota_after_snapshot_ids": tuple(json.loads(row["quota_after_snapshot_ids"])),
        "observed_burn_fraction": row["observed_burn_fraction"],
        "execution_success": bool(row["execution_success"]),
        "verification_success": (
            None if row["verification_success"] is None else bool(row["verification_success"])
        ),
        "quality_outcome": _enum(ShadowQualityOutcome, row["quality_outcome"]),
        "failure_class": _enum(ShadowFailureClass, row["failure_class"]),
        "failure_stage": _enum(ShadowFailureStage, row["failure_stage"]),
        "verified": bool(row["verified"]),
        "regression_detected": bool(row["regression_detected"]),
        "attempts_to_green": row["attempts_to_green"],
        "time_to_green_seconds": row["time_to_green_seconds"],
        "handoff_count": row["handoff_count"],
        "observed_at": datetime.fromisoformat(row["observed_at"]),
    }


def _expected_observation_from_intent(row: Any) -> ShadowObservation | None:
    """Build the exact observation the durable intent demands (§16).

    Returns ``None`` when the intent predates the frozen-identity
    schema (empty ``identity_json``) — such an intent can never prove a
    replay and must fail closed instead of guessing identity fields.
    """

    if not row["identity_json"] or row["identity_json"] == "{}":
        return None
    identity = json.loads(row["identity_json"])
    kwargs = _finalize_kwargs_from_intent_row(row)
    collector_status = identity.get("collector_status")
    return ShadowObservation.build(
        task_id=row["task_id"],
        request_id=row["request_id"],
        decision_id=row["decision_id"],
        manual_execution_target_id=identity["manual_execution_target_id"],
        scheduler_execution_target_id=identity.get("scheduler_execution_target_id"),
        catalog_snapshot_id=identity["catalog_snapshot_id"],
        policy_snapshot_id=identity["policy_snapshot_id"],
        quota_snapshot_ids=tuple(identity["quota_snapshot_ids"]),
        provider_id=identity.get("provider_id"),
        quota_pool_id=identity.get("quota_pool_id"),
        task_family=identity.get("task_family") or "unknown",
        quota_confidence=EvidenceConfidence(
            identity.get("quota_confidence") or EvidenceConfidence.UNKNOWN.value
        ),
        collector_status=(QuotaCollectionStatus(collector_status) if collector_status else None),
        predicted_burn_fraction=identity.get("predicted_burn_fraction"),
        **kwargs,
    )


def _pending_proves_intent_identity(pending: Any, row: Any) -> bool:
    """Round 5 §14 — a PRESENT pending must equal the frozen intent.

    After the finalization intent is enqueued the durable row — not
    mutable filesystem state — is the authority. A pending file with the
    same id but a different lifecycle identity (stale, replaced,
    tampered-but-parseable) is NOT intent fulfillment. The check is the
    intent's own frozen payload: the four correlation columns plus a
    canonical-JSON comparison of ``shadow_identity_payload(pending)``
    against the frozen ``identity_json``. Legacy intents without a
    frozen identity (``{}``) fail closed — never silently upgraded from
    filesystem contents.
    """

    if not row["identity_json"] or row["identity_json"] == "{}":
        return False
    return (
        pending.pending_id == row["pending_id"]
        and pending.task_id == row["task_id"]
        and pending.request_id == row["request_id"]
        and pending.decision_id == row["decision_id"]
        and shadow_identity_payload(pending) == row["identity_json"]
    )


def _observation_proves_intent(journal: ShadowEvidenceJournal, row: Any) -> bool:
    """§14/§15: prove the observation is the intent's EXACT semantic result.

    ``observation_id`` is only a digest of the identity payload — it
    does not cover the verdict, taxonomy, quota-after data or the
    pinned ``observed_at``. Proof therefore REBUILDS the expected
    :class:`ShadowObservation` from the durable intent (verdict columns
    + frozen pending identity) and demands exact model equality; a
    divergent ``failure_class``, ``verification_success``,
    ``observed_at``, ``quota_after_snapshot_ids`` or burn fraction is
    NOT intent fulfillment and must fail closed.
    """

    expected = _expected_observation_from_intent(row)
    if expected is None:
        return False
    matches = [
        observation
        for observation in journal.load_all()
        if observation.observation_id == expected.observation_id
    ]
    return len(matches) == 1 and matches[0] == expected


def drain_auto_shadow_finalize_outbox(
    store: SafetyKernelStore,
    shadow_journal: ShadowEvidenceJournal | None,
    *,
    limit: int = 64,
) -> int:
    """Drain durable shadow-FINALIZATION intents (round 3 closeout).

    A real worker executed and the authoritative verdict is already in
    SQLite — the pending shadow MUST become a truthful observation, so
    this drain (unlike the cleanup drain) replays a filesystem
    finalize, never a discard:

    - pending exists → prove the pending identity equals the frozen
      intent (round 5 §14), then ``finalize_pending`` with the EXACT
      durable payload (``observed_at`` pinned at enqueue →
      byte-identical replay; the journal accepts identical content
      idempotently), then prove the exact expected observation is now
      durably present (§16), then discard, then the completed marker;
    - pending missing (crash after a completed finalize+discard, or
      torn state) → mark completed ONLY when the finalized observation
      is proven present with a matching verdict (§12); otherwise the
      intent stays OPEN and a sanitized system event records the
      retry failure — evidence loss is never treated as success;
    - journal unavailable → all intents stay OPEN (never completed).

    Returns the number of intents completed by this call.
    """

    completed = 0
    if shadow_journal is None:
        return 0
    for row in store.pending_shadow_finalizations(limit=limit):
        pending_id = row["pending_id"]
        try:
            pending = shadow_journal.load_pending(pending_id)
        except FileNotFoundError:
            if _observation_proves_intent(shadow_journal, row):
                store.complete_shadow_finalization(row["finalization_id"])
                completed += 1
            else:
                try:
                    store.record_system_event(
                        "AUTO_SHADOW_FINALIZE_RETRY_FAILED",
                        {
                            "finalization_id": row["finalization_id"],
                            "task_id": row["task_id"],
                            "pending_id": pending_id,
                            "exc_type": "PendingMissingObservationUnproven",
                        },
                    )
                except Exception:  # noqa: BLE001 — event best-effort
                    pass
            continue
        except (OSError, ValueError):
            # Journal unavailable or malformed: leave the intent open.
            continue
        # Round 5 §14/§15 — a PRESENT pending must still equal the
        # frozen immutable identity. A parseable-but-divergent pending
        # (stale / replaced / tampered) must never be finalized,
        # discarded or completed.
        if not _pending_proves_intent_identity(pending, row):
            try:
                store.record_system_event(
                    "AUTO_SHADOW_FINALIZE_RETRY_FAILED",
                    {
                        "finalization_id": row["finalization_id"],
                        "task_id": row["task_id"],
                        "pending_id": pending_id,
                        "exc_type": "PendingIdentityMismatch",
                    },
                )
            except Exception:  # noqa: BLE001 — event best-effort
                pass
            continue
        # Round 5 §16 — the exact expected observation is demanded BEFORE
        # any success marker: a plain ``finalize_pending`` return value
        # is not durable evidence proof. The intent stays OPEN and the
        # pending file stays in place when the proof fails, so a retry
        # (or a crash at any point) converges safely.
        expected = _expected_observation_from_intent(row)
        if expected is None:
            try:
                store.record_system_event(
                    "AUTO_SHADOW_FINALIZE_RETRY_FAILED",
                    {
                        "finalization_id": row["finalization_id"],
                        "task_id": row["task_id"],
                        "pending_id": pending_id,
                        "exc_type": "IntentIdentityNotFrozen",
                    },
                )
            except Exception:  # noqa: BLE001 — event best-effort
                pass
            continue
        try:
            shadow_journal.finalize_pending(pending_id, **_finalize_kwargs_from_intent_row(row))
        except (OSError, ValueError) as error:
            # Leave the intent open; the retry event is sanitized.
            try:
                store.record_system_event(
                    "AUTO_SHADOW_FINALIZE_RETRY_FAILED",
                    {
                        "finalization_id": row["finalization_id"],
                        "task_id": row["task_id"],
                        "pending_id": pending_id,
                        "exc_type": type(error).__name__,
                    },
                )
            except Exception:  # noqa: BLE001 — event best-effort
                pass
            continue
        matches = [
            observation
            for observation in shadow_journal.load_all()
            if observation.observation_id == expected.observation_id
        ]
        if not (len(matches) == 1 and matches[0] == expected):
            # §21: finalize returned but the exact expected observation
            # is not durably present — NOT success. The pending is NOT
            # discarded and the intent stays OPEN.
            try:
                store.record_system_event(
                    "AUTO_SHADOW_FINALIZE_RETRY_FAILED",
                    {
                        "finalization_id": row["finalization_id"],
                        "task_id": row["task_id"],
                        "pending_id": pending_id,
                        "exc_type": "PostFinalizeObservationUnproven",
                    },
                )
            except Exception:  # noqa: BLE001 — event best-effort
                pass
            continue
        shadow_journal.discard_pending(pending_id)
        store.complete_shadow_finalization(row["finalization_id"])
        completed += 1
    return completed


#: Sanitized system-event name for a terminal namespace conflict
#: (round 6 §28). Payload is task_id + auto_decision_id + reason only.
AUTO_EXECUTION_CORRELATION_CONFLICT_EVENT = "AUTO_EXECUTION_CORRELATION_CONFLICT"


class AutoExecutionCorrelation(Enum):
    """Round 6 §27 — classification of the current AUTO lifecycle's
    durable dispatch/run truth. A boolean cannot distinguish "genuinely
    pre-worker" from "the namespace is occupied by a conflicting row" —
    the latter is a fail-closed conflict, never an ordinary cleanup."""

    #: No durable dispatch row occupies the deterministic request id —
    #: the lifecycle is genuinely pre-reservation (crash before
    #: reserve). Ordinary pre-worker semantics apply.
    NO_DISPATCH = "NO_DISPATCH"
    #: The EXACT current SUPERVISED_AUTO reservation is proven and no
    #: exact ``run-{dispatch_id}`` row exists — pre-worker.
    EXACT_PREWORKER = "EXACT_PREWORKER"
    #: The exact reservation is proven AND the exact run row exists —
    #: real AUTO execution; shadow evidence is owed.
    EXACT_REAL_RUN = "EXACT_REAL_RUN"
    #: A durable row occupies the deterministic request id but is NOT
    #: the exact current SUPERVISED_AUTO reservation (foreign authority,
    #: wrong task / target / dispatch id / cycle). Fail closed: never
    #: executed, never adopted, never treated as ordinary pre-worker.
    CONFLICT = "CONFLICT"


def current_supervised_auto_dispatch(
    store: SafetyKernelStore,
    *,
    task_id: str,
    auto_decision_id: str,
    expected_task_state_version: int | None = None,
) -> Any | None:
    """Round 6 §16/§18 — the ONE exact current-AUTO dispatch proof.

    Returns the durable ``OwnerDispatchRecord`` only when it is the
    EXACT reservation this AUTO cycle would create:

    - ``request_id == supervised-auto-dispatch-{auto_decision_id}``
      (the lookup key — the id embeds the planning-cycle version);
    - the frozen routing decision for
      ``supervised-auto-{auto_decision_id}`` exists, belongs to
      ``task_id`` and pins ``execution_target_id``;
    - deterministic ``dispatch_id == owner-dispatch-{request_id}``;
    - ``task_id`` exact;
    - ``authority == SUPERVISED_AUTO`` (an OWNER row is NEVER AUTO
      evidence — §19);
    - ``execution_target_id`` equals the frozen target (§30: a foreign
      row can never substitute its own target).

    State-version rule (§17): the reservation stores the AUTO_GRACE
    ``state_version`` at reserve time, which is NECESSARILY different
    from the later terminal version (RUNNING → WORKER_FINISHED →
    VERIFYING → terminal advanced it). Callers that know the live
    reservation-cycle version (dispatch admission, task still in
    AUTO_GRACE) pass ``expected_task_state_version`` for an exact
    equality check. Terminal/recovery callers pass ``None`` — the
    current cycle is then proven by the five durable dimensions above
    plus the ordering invariant ``row.task_state_version`` strictly
    below the task's current version (a reservation can only precede
    the terminal transitions). No caller may compare the reservation
    version against the terminal task version for equality.
    """

    routing_row = store.routing_decision_by_request_id(
        supervised_auto_routing_request_id(auto_decision_id)
    )
    if routing_row is None or routing_row["task_id"] != task_id:
        return None
    expected_target = _routing_decision_target(routing_row)
    if expected_target is None:
        return None
    request_id = supervised_auto_dispatch_request_id(auto_decision_id)
    try:
        record = store.get_owner_dispatch_by_request_id(request_id)
    except KeyError:
        return None
    version_ok: bool
    if expected_task_state_version is not None:
        version_ok = record.task_state_version == expected_task_state_version
    else:
        task = store.get_task(task_id)
        version_ok = record.task_state_version < task.state_version
    if not version_ok:
        return None
    if not owner_dispatch_matches_expected(
        record,
        dispatch_id=f"owner-dispatch-{request_id}",
        request_id=request_id,
        task_id=task_id,
        task_state_version=record.task_state_version,
        execution_target_id=expected_target,
        authority=AUTHORITY_SUPERVISED_AUTO_EXECUTION,
    ):
        return None
    return record


def _routing_decision_target(routing_row: Any) -> str | None:
    """Extract ``selected_execution_target_id`` from the durable row."""

    try:
        payload = json.loads(routing_row["payload_json"])
    except (TypeError, ValueError):
        return None
    target = payload.get("selected_execution_target_id")
    return target if isinstance(target, str) and target else None


def correlate_supervised_auto_execution(
    store: SafetyKernelStore,
    *,
    task_id: str,
    auto_decision_id: str,
    expected_task_state_version: int | None = None,
) -> AutoExecutionCorrelation:
    """Round 6 §21/§27 — classify the current AUTO lifecycle's run truth.

    Exact dispatch proof FIRST; only then the exact ``run-{dispatch_id}``
    lookup. A namespace occupied by a non-exact row is CONFLICT — it can
    never collapse into "no run / pre-worker".
    """

    dispatch = current_supervised_auto_dispatch(
        store,
        task_id=task_id,
        auto_decision_id=auto_decision_id,
        expected_task_state_version=expected_task_state_version,
    )
    if dispatch is None:
        try:
            store.get_owner_dispatch_by_request_id(
                supervised_auto_dispatch_request_id(auto_decision_id)
            )
        except KeyError:
            return AutoExecutionCorrelation.NO_DISPATCH
        return AutoExecutionCorrelation.CONFLICT
    run_row = store.connection.execute(
        "SELECT 1 FROM runs WHERE run_id=? LIMIT 1",
        (f"run-{dispatch.dispatch_id}",),
    ).fetchone()
    if run_row is not None:
        return AutoExecutionCorrelation.EXACT_REAL_RUN
    return AutoExecutionCorrelation.EXACT_PREWORKER


def real_execution_recovery_proof(
    store: SafetyKernelStore,
    shadow_journal: ShadowEvidenceJournal | None,
    *,
    task_id: str,
    pending_id: str,
) -> bool:
    """Round 4 §8/§9 — the hard metadata-clear recovery guard.

    A SUPERVISED_AUTO lifecycle whose current dispatch truly launched a
    worker may clear its auto metadata ONLY when the observation truth is
    durably recoverable:

    A. a durable shadow-finalization intent exists for the pending (the
       outbox is self-sufficient — §25 priority), OR
    B. a finalized observation for the exact lifecycle identity is
       present: task + routing request id + the REAL durable
       ``RoutingDecision.decision_id`` loaded from ``routing_decisions``
       (round 5 §23 — the observation's ``decision_id`` is ``route-*``,
       never the ``auto-*`` pending id), exactly ONE such observation,
       and its terminal verdict consistent with the task's terminal
       state (VERIFIED ⇔ verified=True, other terminal states ⇔
       verified=False; a non-terminal state never proves recovery).

    Round 6 §26: the real-run classification itself comes from
    :func:`correlate_supervised_auto_execution` — the EXACT current
    SUPERVISED_AUTO reservation is proven first (a foreign OWNER row
    with its own run can never be AUTO evidence), and a namespace
    CONFLICT fails closed (``False``): it is NOT ordinary pre-worker.

    Lifecycles WITHOUT a real exact run (genuinely pre-worker:
    NO_DISPATCH or EXACT_PREWORKER) return ``True``: the ordinary
    abort/cleanup path owns them, and the two path classes must never be
    mixed. Real-run lifecycles without proof return ``False`` — callers
    must keep the metadata, refuse any discard and record a sanitized
    recovery failure.
    """

    correlation = correlate_supervised_auto_execution(
        store, task_id=task_id, auto_decision_id=pending_id
    )
    if correlation is AutoExecutionCorrelation.CONFLICT:
        return False
    if correlation is not AutoExecutionCorrelation.EXACT_REAL_RUN:
        return True  # genuinely pre-worker (NO_DISPATCH / EXACT_PREWORKER)
    if store.shadow_finalize_intent_exists(pending_id):
        return True
    if shadow_journal is not None:
        routing_request_id = supervised_auto_routing_request_id(pending_id)
        routing_row = store.routing_decision_by_request_id(routing_request_id)
        if routing_row is None or routing_row["task_id"] != task_id:
            return False
        expected_routing_decision_id = routing_row["decision_id"]
        try:
            matches = [
                observation
                for observation in shadow_journal.load_all()
                if observation.task_id == task_id
                and observation.request_id == routing_request_id
                and observation.decision_id == expected_routing_decision_id
            ]
        except OSError:
            matches = []
        if len(matches) != 1:
            return False
        try:
            task = store.get_task(task_id)
        except KeyError:
            return False
        if task.state not in _AUTO_TERMINAL_STATES:
            return False  # not a legal terminal recovery state
        if task.state is TaskState.VERIFIED:
            return matches[0].verified is True
        return matches[0].verified is False
    return False


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
        #: Round 7 §11 — per-tick dedupe of correlation-conflict events
        #: so reconciliation and the terminal sweep never both emit for
        #: the same task within one tick. Reset at every tick entry.
        self._tick_conflict_events: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # entrypoint
    # ------------------------------------------------------------------

    def tick(self, now: datetime) -> None:
        """One bounded sweep. Never raises for per-task gate failures.

        Ordering contract (crash-consistency closeout): old-lifecycle
        cleanup always precedes new planning — the outbox drains and the
        stale-READY-metadata recovery run first so no new cycle can
        overlap an uncleaned predecessor. The FINALIZE drain runs before
        the cleanup drain (round 3: real-execution evidence outranks
        discard promises), again at tick end.
        """

        self._tick_conflict_events = set()
        try:
            self._drain_shadow_finalize_outbox()
            self._drain_shadow_cleanup_outbox()
            self._recover_stale_ready_auto_metadata()
            self._abort_tasks_with_revoked_mode(now)
            self._abort_tasks_with_revoked_project(now)
            if self._mode() == "SUPERVISED_AUTO":
                for task in self._tasks_in_state(TaskState.READY):
                    self._try_plan(task, now)
                for task in self._tasks_in_state(TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE):
                    self._advance(task, now)
            self._reconcile_supervised_auto_dispatches(now)
            self._close_terminal_auto_lifecycles()
            self._drain_shadow_finalize_outbox()
            self._drain_shadow_cleanup_outbox()
        except Exception as error:  # noqa: BLE001 — bounded tick, audit + continue
            self._safe_system_event(
                "SUPERVISED_AUTO_TICK_FAILED",
                {"exc_type": type(error).__name__, "message": str(error)[:256]},
            )

    # ------------------------------------------------------------------
    # crash-consistency recovery (outbox drain + stale READY metadata)
    # ------------------------------------------------------------------

    def _drain_shadow_cleanup_outbox(self) -> None:
        drain_auto_shadow_cleanup_outbox(self._store, self._shadow_journal)

    def _drain_shadow_finalize_outbox(self) -> None:
        drain_auto_shadow_finalize_outbox(self._store, self._shadow_journal)

    def _recover_stale_ready_auto_metadata(self) -> None:
        """READY rows with residual lifecycle metadata are stale, never active.

        The predicate keys on the three lifecycle columns
        (``auto_decision_id`` / ``auto_grace_deadline_at`` /
        ``auto_acked_at``) — a real lifecycle always carries them, while
        ``auto_reason`` alone on a READY row is the tick's deliberate
        deduped skip hint, never active control state, and must survive
        this sweep. ``READY`` with a NULL ``auto_decision_id`` but an
        existing routing-decision row is the legal frozen-decision crash
        boundary (decision persisted, task not yet AUTO_PLANNED) — also
        explicitly NOT inconsistent and untouched here.
        """

        rows = self._store.connection.execute(
            """
            SELECT task_id FROM tasks
            WHERE state = ?
              AND (auto_decision_id IS NOT NULL OR auto_grace_deadline_at IS NOT NULL
                   OR auto_acked_at IS NOT NULL)
            ORDER BY task_id
            """,
            (TaskState.READY.value,),
        ).fetchall()
        for row in rows:
            try:
                self._store.recover_ready_auto_metadata(row["task_id"])
            except (KeyError, RuntimeError):
                continue  # concurrent writer owns the row

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
                    runtime_available=self._runtime_available_provider(top.execution_target_id),
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

    def _dispatch_auto_task(self, task: TaskRecord, now: datetime, *, trigger: str) -> None:
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
        #
        # Round 5 §6 / round 6 §20: the deterministic AUTO request-id
        # namespace can be occupied by a legacy / foreign row (e.g. an
        # OWNER_INITIATED dispatch reserved before the namespace became
        # reserved). An existing row is valid AUTO recovery ONLY when
        # the ONE canonical exact proof
        # (``current_supervised_auto_dispatch`` — the same single
        # comparison ``reserve_owner_dispatch`` uses) accepts it. Anything
        # else is a namespace conflict: never executed, never adopted,
        # never mutated — the current lifecycle fails closed instead.
        correlation = correlate_supervised_auto_execution(
            self._store,
            task_id=fresh.task_id,
            auto_decision_id=fresh.auto_decision_id,
            expected_task_state_version=fresh.state_version,
        )
        if correlation is AutoExecutionCorrelation.CONFLICT:
            # §8/§30: the foreign row remains untouched as historical
            # truth; the CURRENT lifecycle closes fail-closed (atomic
            # READY + metadata clear + cleanup outbox). A new planning
            # cycle derives a fresh state_version → fresh
            # auto_decision_id → fresh request id.
            self._abort_auto_task(
                fresh,
                now,
                reason="dispatch_namespace_conflict",
            )
            return
        if correlation is AutoExecutionCorrelation.EXACT_PREWORKER:
            existing_dispatch = current_supervised_auto_dispatch(
                self._store,
                task_id=fresh.task_id,
                auto_decision_id=fresh.auto_decision_id,
                expected_task_state_version=fresh.state_version,
            )
            if (
                existing_dispatch is not None
                and existing_dispatch.status == "RESERVED"
                and self._executor is not None
                and not self._has_active_run(fresh.task_id)
            ):
                live_registry = getattr(self._executor, "execution_supervisor", None)
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
        if correlation is AutoExecutionCorrelation.EXACT_REAL_RUN:
            # The exact dispatch already launched its worker — the
            # executor / reconciliation owns the outcome; never reserve
            # or re-enter here.
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
        """Reconcile the CURRENT cycle's dispatch, pre-worker only.

        Round 7 §4-§6: reconciliation is TASK-driven — the current task
        row is the authoritative lifecycle truth; a raw owner_dispatches
        row is only ever a candidate hint, never proof. Every mutation
        is gated on the ONE canonical Round-6 classifier
        (:func:`correlate_supervised_auto_execution`):

        - CONFLICT — the deterministic namespace is occupied by a row
          that is not the exact current SUPERVISED_AUTO reservation
          (wrong authority / task / frozen target / dispatch id /
          reservation version). Fail closed: preserve the task's auto
          metadata and pending shadow, no abort, no discard, no
          finalize intent, sanitized deduped
          ``AUTO_EXECUTION_CORRELATION_CONFLICT`` event.
        - EXACT_REAL_RUN — the exact dispatch truly launched its
          worker: the executor / terminal recovery owns the outcome;
          never a pre-worker abort.
        - NO_DISPATCH — no durable dispatch exists for THIS cycle;
          unrelated historical SUPERVISED_AUTO rows for the same task
          prove nothing and can never drive a mutation.
        - EXACT_PREWORKER — the exact current reservation exists with
          no exact run; a BLOCKED/CANCELLED reservation is then a
          legitimate pre-worker admission failure and the existing
          crash-atomic abort path closes the lifecycle.

        Version semantics (§7/§8): while the task is still AUTO_GRACE
        the reservation version must exactly equal the live
        ``state_version``; a terminal BLOCKED task uses the recovery
        ordering contract (reservation version strictly below the
        terminal version) — the two are never compared for equality.
        """

        rows = self._store.connection.execute(
            "SELECT task_id FROM tasks WHERE auto_decision_id IS NOT NULL AND state IN (?,?)",
            (TaskState.AUTO_GRACE.value, TaskState.BLOCKED.value),
        ).fetchall()
        for row in rows:
            try:
                task = self._store.get_task(row["task_id"])
            except KeyError:
                continue
            # Current-cycle gate BEFORE any mutation: without a live
            # auto decision id there is no current lifecycle to close.
            if task.auto_decision_id is None:  # pragma: no cover — raced clear
                continue
            expected_version = task.state_version if task.state is TaskState.AUTO_GRACE else None
            correlation = correlate_supervised_auto_execution(
                self._store,
                task_id=task.task_id,
                auto_decision_id=task.auto_decision_id,
                expected_task_state_version=expected_version,
            )
            if correlation is AutoExecutionCorrelation.CONFLICT:
                # §5: never treat a conflicting reservation as a
                # legitimate pre-worker failure — preserve everything.
                self._record_correlation_conflict(
                    task.task_id,
                    task.auto_decision_id,
                    "dispatch namespace occupied by a non-exact reservation",
                )
                continue
            if correlation is not AutoExecutionCorrelation.EXACT_PREWORKER:
                # EXACT_REAL_RUN → real-execution recovery owns it;
                # NO_DISPATCH → no current-cycle dispatch to reconcile
                # (§18: historical rows must not mutate this cycle).
                continue
            # EXACT_PREWORKER: only a BLOCKED/CANCELLED exact current
            # reservation is a terminal pre-worker admission failure.
            # (RESERVED is crash-boundary-C recovery's concern; STARTED
            # implies the atomic run row exists.)
            dispatch = current_supervised_auto_dispatch(
                self._store,
                task_id=task.task_id,
                auto_decision_id=task.auto_decision_id,
                expected_task_state_version=expected_version,
            )
            if dispatch is None:  # pragma: no cover — raced mutation
                continue
            if dispatch.status not in {
                OwnerDispatchStatus.BLOCKED,
                OwnerDispatchStatus.CANCELLED,
            }:
                continue
            reason = f"admission_failed:{dispatch.failure_code or dispatch.status.value}"
            if task.state is TaskState.AUTO_GRACE:
                # The reservation was rejected before the executor ever
                # moved the task (e.g. initiate_owner_dispatch validation).
                self._abort_auto_task(task, now, reason=reason)
                continue
            # Pre-worker supervised-auto BLOCKED for the CURRENT cycle:
            # the exact-reservation proof above is the durable evidence
            # this BLOCKED task belongs to this supervised-auto
            # dispatch, so allow_blocked cannot be reached by a plain
            # owner BLOCKED task.
            self._abort_auto_task(task, now, reason=reason, allow_blocked=True)

    # ------------------------------------------------------------------
    # terminal lifecycle close-out (§32)
    # ------------------------------------------------------------------

    def _close_terminal_auto_lifecycles(self) -> None:
        """Post-RUNNING terminal tasks must not carry active-auto metadata.

        Round 3 classification (crash boundary D): a terminal task whose
        CURRENT dispatch truly launched a worker (exact ``run-{dispatch_id}``
        row) is a REAL EXECUTION — its pending must become a truthful
        finalized observation, never a silent discard:

        - open finalize intent → leave it to the finalize drain; the
          metadata clear (guarded) enqueues no cleanup intent;
        - no intent (old-build crash residue) → reconstruct one from
          durable truth (task terminal state + run row; or an existing
          observation replayed byte-identically), THEN clear metadata;
        - reconstruction impossible (pending file gone, no observation
          provable) → fail closed: keep the metadata, sanitize + record
          a system event, never discard.

        A terminal lifecycle WITHOUT an exact run row is pre-worker /
        aborted: discard + clear (cleanup outbox) as before.
        """

        rows = self._store.connection.execute(
            "SELECT task_id FROM tasks WHERE auto_decision_id IS NOT NULL AND state IN (?,?,?,?,?)",
            tuple(state.value for state in _AUTO_TERMINAL_STATES),
        ).fetchall()
        for row in rows:
            try:
                task = self._store.get_task(row["task_id"])
            except KeyError:
                continue
            if task.auto_decision_id is None:  # pragma: no cover — raced clear
                continue
            pending_id = task.auto_decision_id
            correlation = correlate_supervised_auto_execution(
                self._store, task_id=task.task_id, auto_decision_id=pending_id
            )
            if correlation is AutoExecutionCorrelation.CONFLICT:
                # Round 6 §28: the deterministic namespace is occupied by
                # a row that is NOT the exact current SUPERVISED_AUTO
                # reservation. This is NOT ordinary pre-worker — never
                # discard the pending, never reconstruct finalize truth
                # from a foreign run, never clear the metadata. Fail
                # closed with a sanitized (per-tick deduped) event; every
                # trace is kept.
                self._record_correlation_conflict(
                    task.task_id,
                    pending_id,
                    "dispatch namespace occupied by a non-exact reservation",
                )
                continue
            if correlation is AutoExecutionCorrelation.EXACT_REAL_RUN:
                self._close_real_execution_lifecycle(task, pending_id)
                continue
            # NO_DISPATCH / EXACT_PREWORKER (§29): genuinely pre-worker —
            # discard + clear (cleanup outbox) as before.
            self._discard_pending(pending_id)
            # The clear transaction also enqueues the durable cleanup
            # intent, so a crash between these two steps still drains on
            # the next tick — the metadata clear is the atomic authority.
            self._store.clear_auto_state_metadata(
                task.task_id,
                expected_version=task.state_version,
                reason="terminal state sweep closed the auto lifecycle",
            )

    def _close_real_execution_lifecycle(self, task: TaskRecord, pending_id: str) -> None:
        """Real-execution terminal close: finalize truthfully, never discard."""

        if not self._store.shadow_finalize_intent_exists(pending_id):
            if not self._enqueue_reconstructed_finalize_intent(task, pending_id):
                # Fail closed (§14 case C): not enough durable truth to
                # promise the observation — preserve every trace.
                self._safe_system_event(
                    "AUTO_SHADOW_FINALIZE_RECONSTRUCTION_FAILED",
                    {
                        "task_id": task.task_id,
                        "pending_id": pending_id,
                        "reason": "pending missing and observation unproven",
                    },
                )
                return
        # §24: the finalize intent is durable — the metadata may clear;
        # the guarded clear enqueues NO cleanup intent for this pending.
        self._store.clear_auto_state_metadata(
            task.task_id,
            expected_version=task.state_version,
            reason="terminal state sweep closed the auto lifecycle",
        )
        self._drain_shadow_finalize_outbox()

    def _enqueue_reconstructed_finalize_intent(self, task: TaskRecord, pending_id: str) -> bool:
        """Rebuild a finalize intent from durable truth (§14 case C).

        Returns False when reconstruction is impossible (fail closed).
        Prefer replaying an EXISTING observation byte-identically (its
        verdict fields + observed_at) so the idempotent drain cannot
        diverge from what is already on disk.
        """

        if self._shadow_journal is None:
            return False
        try:
            pending = self._shadow_journal.load_pending(pending_id)
        except (OSError, ValueError):
            return False
        existing = [
            observation
            for observation in self._shadow_journal.load_all()
            if observation.task_id == task.task_id
            and observation.request_id == pending.request_id
            and observation.decision_id == pending.decision_id
        ]
        # Round 6 §20: the dispatch id comes from the canonical exact
        # proof — never a raw namespace lookup that a foreign OWNER row
        # could satisfy. (This reconstruction only runs after an
        # EXACT_REAL_RUN classification, so the proof is expected to
        # hold; an empty id is the fail-closed fallback.)
        dispatch = current_supervised_auto_dispatch(
            self._store, task_id=task.task_id, auto_decision_id=pending_id
        )
        dispatch_id = dispatch.dispatch_id if dispatch is not None else ""
        if existing:
            observed: ShadowObservation = existing[0]
            intent = ShadowFinalizationIntent(
                pending_id=pending_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                request_id=pending.request_id,
                decision_id=pending.decision_id,
                verified=observed.verified,
                execution_success=observed.execution_success,
                verification_success=observed.verification_success,
                quality_outcome=(
                    observed.quality_outcome.value if observed.quality_outcome is not None else None
                ),
                failure_class=(
                    observed.failure_class.value if observed.failure_class is not None else None
                ),
                failure_stage=(
                    observed.failure_stage.value if observed.failure_stage is not None else None
                ),
                regression_detected=observed.regression_detected,
                attempts_to_green=observed.attempts_to_green,
                time_to_green_seconds=observed.time_to_green_seconds,
                handoff_count=observed.handoff_count,
                reset_cycle_ids=observed.reset_cycle_ids,
                quota_after_snapshot_ids=observed.quota_after_snapshot_ids,
                observed_burn_fraction=observed.observed_burn_fraction,
                observed_at=observed.observed_at.isoformat(),
                identity_json=shadow_identity_payload(pending),
            )
        else:
            run_status = self._store.connection.execute(
                "SELECT status FROM runs WHERE run_id=? LIMIT 1",
                (f"run-{dispatch_id}",),
            ).fetchone()
            intent = ShadowFinalizationIntent(
                pending_id=pending_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                request_id=pending.request_id,
                decision_id=pending.decision_id,
                verified=task.state is TaskState.VERIFIED,
                execution_success=(run_status is not None and run_status["status"] == "FINISHED"),
                verification_success=None,
                observed_at=datetime.now(UTC).isoformat(),
                identity_json=shadow_identity_payload(pending),
            )
        try:
            self._store.enqueue_shadow_finalization(intent)
        except RuntimeError:
            # A different payload already owns this finalization id —
            # the first durable intent is authoritative; drain replays it.
            return True
        return True

    # ------------------------------------------------------------------
    # shared abort path
    # ------------------------------------------------------------------

    def _abort_auto_task(
        self,
        task: TaskRecord,
        now: datetime,
        *,
        reason: str,
        allow_blocked: bool = False,
    ) -> None:
        """Fail-closed exit: ONE crash-atomic authoritative close.

        ``SafetyKernelStore.abort_auto_lifecycle`` performs the state
        move to READY, the four-column metadata clear, the (optional)
        MANUAL policy lock, the single version bump, the abort audit and
        the durable shadow-cleanup intent in a single SQLite
        transaction — there is no crash window that can leave
        ``READY`` + active-looking auto metadata. The pending discard
        itself happens after COMMIT via the idempotent outbox drain.
        """

        del now  # the durable timestamp comes from the store transaction
        try:
            fresh = self._store.get_task(task.task_id)
        except KeyError:
            return
        try:
            self._store.abort_auto_lifecycle(
                task.task_id,
                expected_version=fresh.state_version,
                reason=reason,
                allow_blocked=allow_blocked,
            )
        except (ValueError, RuntimeError):
            # A concurrent writer (veto, promotion, another abort) owns
            # the outcome — fail closed and let the sweeps re-decide.
            return
        self._drain_shadow_cleanup_outbox()

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
                        if pool.snapshot is not None and pool.snapshot.provider_id == provider_id:
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
                    catalog_snapshot_id=decision.catalog_snapshot_id or self._catalog_snapshot_id,
                    policy_snapshot_id=decision.policy_snapshot_id or "policy-unknown",
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

    def _record_correlation_conflict(
        self, task_id: str, auto_decision_id: str, reason: str
    ) -> None:
        """Round 7 §11 — sanitized + per-tick-deduped conflict event.

        Payload carries task_id / auto_decision_id / reason only — no
        raw row contents. Reconciliation and the terminal sweep share
        this helper, so one tick emits at most one event per
        (task, lifecycle) even when both stages see the conflict.
        """

        key = (task_id, auto_decision_id)
        if key in self._tick_conflict_events:
            return
        self._tick_conflict_events.add(key)
        self._safe_system_event(
            AUTO_EXECUTION_CORRELATION_CONFLICT_EVENT,
            {
                "task_id": task_id,
                "auto_decision_id": auto_decision_id,
                "reason": reason,
            },
        )


__all__ = [
    "AUTO_EXECUTION_CORRELATION_CONFLICT_EVENT",
    "AutoExecutionCorrelation",
    "SUPERVISED_AUTO_DISPATCH_REQUEST_PREFIX",
    "SUPERVISED_AUTO_STEP_NAME",
    "SupervisedAutoStep",
    "UNACKED_TIMEOUT_SECONDS",
    "correlate_supervised_auto_execution",
    "current_supervised_auto_dispatch",
    "drain_auto_shadow_cleanup_outbox",
    "drain_auto_shadow_finalize_outbox",
    "is_reserved_auto_dispatch_request_id",
    "real_execution_recovery_proof",
    "supervised_auto_decision_id",
    "supervised_auto_dispatch_request_id",
    "supervised_auto_routing_request_id",
]
