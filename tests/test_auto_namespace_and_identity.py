"""Round 5 — cross-authority namespace + pending identity + recovery proof.

P0-1: the deterministic ``supervised-auto-dispatch-*`` namespace belongs
exclusively to the SUPERVISED_AUTO authority. Owner-supplied request ids
in that namespace are rejected BEFORE any durable reservation, and an
existing (legacy/polluted) row occupying the deterministic AUTO request
id is accepted as AUTO crash recovery ONLY on an exact identity match
(authority, task, state version, frozen target, dispatch id) — a foreign
row is never executed, adopted or mutated; the current lifecycle fails
closed instead.

P0-2: a PRESENT pending shadow must equal the immutable identity frozen
in the finalization intent before the drain finalizes it, and success
requires the exact expected observation to be durably proven — a plain
``finalize_pending()`` return is not proof.

P1: observation-only recovery proof correlates on the REAL durable
``RoutingDecision.decision_id`` (``route-*``), never the ``auto-*``
pending id, and requires terminal task/shadow verdict consistency.
"""

import time
from datetime import timedelta

import pytest

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.shadow_evidence import ShadowObservation
from personal_ai_orchestrator.supervised_auto_step import (
    drain_auto_shadow_finalize_outbox,
    real_execution_recovery_proof,
    supervised_auto_dispatch_request_id,
    supervised_auto_routing_request_id,
)
from tests.test_auto_final_outcome import _finalize_rows, _proof_env
from tests.test_supervised_auto_step import NOW, _env, _plan

_TICK_LATER = NOW + timedelta(seconds=301)


def _wait_for_calls(executor, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(executor.unique_calls) >= count:
            return
        time.sleep(0.01)
    assert len(executor.unique_calls) >= count


def _abort_reasons(store, task_id: str = "task-1") -> list:
    return [
        event["payload"].get("reason")
        for event in store.audit_events(task_id)
        if event["event_type"] == "AUTO_ABORTED"
    ]


# ---------------------------------------------------------------------------
# NAMESPACE-1 (§12) — reserved prefix rejected at the owner boundary
# ---------------------------------------------------------------------------


def test_owner_reserved_prefix_rejected_before_reservation(tmp_path) -> None:
    env = _env(tmp_path)
    task = env.store.get_task("task-1")

    with pytest.raises(ControlPlaneError) as error:
        env.service.dispatch_task(
            "task-1",
            {
                "request_id": "supervised-auto-dispatch-auto-task-1-v0",
                "task_state_version": task.state_version,
                "execution_target_id": "m3-sub",
            },
        )

    assert error.value.status == 400
    assert error.value.code == "reserved_dispatch_request_id_namespace"
    # Rejection happened BEFORE the durable reservation: no row, no
    # worker, no task mutation.
    polluted = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches "
        "WHERE request_id LIKE 'supervised-auto-dispatch-%'"
    ).fetchone()
    assert polluted["n"] == 0
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.READY


def test_owner_normal_request_id_still_dispatches(tmp_path) -> None:
    env = _env(tmp_path)
    task = env.store.get_task("task-1")

    view = env.service.dispatch_task(
        "task-1",
        {
            "request_id": "owner-request-123",
            "task_state_version": task.state_version,
            "execution_target_id": "m3-sub",
        },
    )

    assert view.request_id == "owner-request-123"
    rows = env.store.connection.execute(
        "SELECT authority, status FROM owner_dispatches WHERE request_id=?",
        ("owner-request-123",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["authority"] == "OWNER_INITIATED_EXECUTION"
    assert rows[0]["status"] in {"RESERVED", "STARTED"}


# ---------------------------------------------------------------------------
# NAMESPACE-2/3/4 (§9/§10) — polluted deterministic rows never execute
# ---------------------------------------------------------------------------


def _pollute_deterministic_request_id(
    env,
    *,
    authority: str = "OWNER_INITIATED_EXECUTION",
    target: str = "TARGET_B",
    task_id: str | None = None,
    task_state_version: int | None = None,
    dispatch_id: str | None = None,
) -> str:
    task = env.store.get_task("task-1")
    request_id = supervised_auto_dispatch_request_id(task.auto_decision_id)
    if task_id is not None and task_id != task.task_id:
        # The polluted row references a different (existing) task.
        env.store.submit_task(
            task_id=task_id,
            request_id=f"submit-{task_id}",
            project_id="p1",
            intent="foreign task",
            base_sha=env.store.get_project("p1").last_known_head,
        )
        env.store.transition_task(task_id, TaskState.READY)
    env.store.connection.execute(
        "INSERT INTO owner_dispatches(dispatch_id, request_id, task_id,"
        " task_state_version, execution_target_id, authority, status,"
        " created_at, started_at, finished_at, failure_code,"
        " failure_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            dispatch_id or f"owner-dispatch-{request_id}",
            request_id,
            task_id or task.task_id,
            task_state_version if task_state_version is not None else task.state_version,
            target,
            authority,
            "RESERVED",
            NOW.isoformat(),
            None,
            None,
            None,
            None,
        ),
    )
    env.store.connection.commit()
    return request_id


def test_foreign_owner_reserved_row_never_executed(tmp_path) -> None:
    """§9 merge-critical: a legacy OWNER row (TARGET_B, RESERVED)
    occupying the current AUTO request id is NEVER executed; the current
    lifecycle closes fail-closed and the foreign row stays untouched."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    request_id = _pollute_deterministic_request_id(env)

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY  # fail-closed abort
    assert task.auto_decision_id is None
    assert "dispatch_namespace_conflict" in _abort_reasons(env.store)
    # TARGET_B was never launched: no executor entry, no run row.
    assert env.executor is not None and env.executor.unique_calls == []
    runs = env.store.connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    assert runs["n"] == 0
    # The foreign row remains untouched historical truth.
    row = env.store.connection.execute(
        "SELECT authority, status, execution_target_id, dispatch_id"
        " FROM owner_dispatches WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert row["authority"] == "OWNER_INITIATED_EXECUTION"
    assert row["status"] == "RESERVED"
    assert row["execution_target_id"] == "TARGET_B"


def test_foreign_auto_authority_row_with_wrong_target_not_adopted(
    tmp_path,
) -> None:
    """NAMESPACE-3: even a SUPERVISED_AUTO row is foreign when its frozen
    target differs — TARGET_B can never replace the frozen TARGET_A."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    request_id = _pollute_deterministic_request_id(
        env, authority="SUPERVISED_AUTO", target="TARGET_B"
    )

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert "dispatch_namespace_conflict" in _abort_reasons(env.store)
    assert env.executor is not None and env.executor.unique_calls == []
    row = env.store.connection.execute(
        "SELECT execution_target_id FROM owner_dispatches WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert row["execution_target_id"] == "TARGET_B"  # untouched


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"task_id": "task-2"}, id="wrong-task-id"),
        pytest.param({"task_state_version": 99}, id="wrong-state-version"),
        pytest.param({"dispatch_id": "owner-dispatch-foreign"}, id="wrong-dispatch-id"),
    ],
)
def test_wrong_tuple_dimensions_rejected(tmp_path, mutation) -> None:
    """§10: each identity dimension of the existing row must match
    exactly — authority is covered by the OWNER test above."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    request_id = _pollute_deterministic_request_id(env, authority="SUPERVISED_AUTO", **mutation)

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert env.executor is not None and env.executor.unique_calls == []
    rows = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert rows["n"] == 1  # the polluted row itself is preserved


# ---------------------------------------------------------------------------
# NAMESPACE-5 (§11) — exact AUTO reservation still crash-recovers
# ---------------------------------------------------------------------------


def test_exact_auto_reserved_row_recovers_without_second_reservation(
    tmp_path,
) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    request_id = supervised_auto_dispatch_request_id(task.auto_decision_id)
    env.store.reserve_owner_dispatch(
        dispatch_id=f"owner-dispatch-{request_id}",
        request_id=request_id,
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )

    env.tick(_TICK_LATER)

    # Recovery re-entered the executor for the SAME request id, and no
    # second reservation / different target exists.
    assert env.executor is not None
    _wait_for_calls(env.executor, 1)
    assert env.executor.unique_calls == [request_id]
    rows = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM owner_dispatches WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert rows["n"] == 1
    task = env.store.get_task("task-1")
    assert task.state is TaskState.AUTO_GRACE  # recording executor: no move


# ---------------------------------------------------------------------------
# IDENTITY-1..4 (§18-§21) — present pending identity + finalize proof
# ---------------------------------------------------------------------------


def _replace_pending(env, decision: str, **updates) -> None:
    pending = env.shadow.load_pending(decision)
    replaced = pending.model_copy(update=updates)
    env.shadow.pending_path_for(decision).write_text(
        replaced.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def test_present_pending_exact_identity_finalizes(tmp_path) -> None:
    """IDENTITY-1 (§20): unmodified pending + self-sufficient intent →
    exactly one expected observation, pending discarded, intent
    completed, second drain a no-op."""

    env, decision, intent = _proof_env(tmp_path)

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 1

    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is not None
    observations = env.shadow.load_all()
    assert len(observations) == 1
    observation = observations[0]
    assert observation.verified is False
    assert observation.execution_success is True
    assert observation.verification_success is True
    assert observation.observed_at.isoformat() == intent.observed_at
    assert env.shadow.load_pending_all() == ()
    # Idempotent: the duplicate drain is a no-op, still one observation.
    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0
    assert len(env.shadow.load_all()) == 1


def test_present_pending_target_mismatch_stays_open(tmp_path) -> None:
    """IDENTITY-2 (§18): same pending id, different target → no finalize,
    no discard, intent stays OPEN, sanitized retry event."""

    env, decision, _intent = _proof_env(tmp_path)
    _replace_pending(env, decision, manual_execution_target_id="attacker-target")

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0

    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is None
    assert env.shadow.load_all() == ()
    # Pending B was NOT discarded.
    assert env.shadow.load_pending(decision).manual_execution_target_id == ("attacker-target")
    events = env.store.connection.execute(
        "SELECT payload_json FROM audit_events"
        " WHERE event_type='AUTO_SHADOW_FINALIZE_RETRY_FAILED'"
        " AND task_id IS NULL"
    ).fetchall()
    assert len(events) == 1
    assert "PendingIdentityMismatch" in events[0]["payload_json"]


@pytest.mark.parametrize(
    "updates",
    [
        pytest.param({"catalog_snapshot_id": "stale-catalog"}, id="catalog"),
        pytest.param({"quota_snapshot_ids": ("quota-other",)}, id="quota"),
        pytest.param({"provider_id": "other-provider"}, id="provider"),
    ],
)
def test_present_pending_identity_field_mismatch_fails_closed(tmp_path, updates) -> None:
    """IDENTITY-3 (§19): snapshot/quota/provider divergence stays OPEN."""

    env, decision, _intent = _proof_env(tmp_path)
    _replace_pending(env, decision, **updates)

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0

    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is None
    assert env.shadow.load_all() == ()
    assert env.shadow.load_pending_all() != ()


class _SilentFinalizeJournal:
    """§21 fault injection: ``finalize_pending`` returns normally but
    never persists the observation."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def load_pending(self, pending_id):
        return self._inner.load_pending(pending_id)

    def load_pending_all(self):
        return self._inner.load_pending_all()

    def discard_pending(self, pending_id):
        return self._inner.discard_pending(pending_id)

    def load_all(self):
        return self._inner.load_all()

    def finalize_pending(self, *args, **kwargs):
        return None  # lies: success without durable evidence


def test_finalize_return_without_observation_proof_is_not_success(
    tmp_path,
) -> None:
    """IDENTITY-4 (§21): a successful function return is NOT durable
    evidence proof — no discard, no completion."""

    env, decision, _intent = _proof_env(tmp_path)

    drained = drain_auto_shadow_finalize_outbox(env.store, _SilentFinalizeJournal(env.shadow))

    assert drained == 0
    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is None
    assert env.shadow.load_all() == ()
    assert env.shadow.load_pending_all() != ()  # pending NOT discarded
    events = env.store.connection.execute(
        "SELECT payload_json FROM audit_events"
        " WHERE event_type='AUTO_SHADOW_FINALIZE_RETRY_FAILED'"
        " AND task_id IS NULL"
    ).fetchall()
    assert len(events) == 1
    assert "PostFinalizeObservationUnproven" in events[0]["payload_json"]


# ---------------------------------------------------------------------------
# RECOVERY-1..3 (§26-§28) — observation-only recovery proof
# ---------------------------------------------------------------------------


def _recovery_env(tmp_path, *, task_verified: bool):
    """Terminal task + exact real dispatch/run + pending, NO intent."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    dispatch_id = f"owner-dispatch-{request_id}"
    env.store.reserve_owner_dispatch(
        dispatch_id=dispatch_id,
        request_id=request_id,
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )
    running = env.store.transition_task(
        "task-1", TaskState.RUNNING, expected_version=task.state_version
    )
    finished = env.store.transition_task(
        "task-1", TaskState.WORKER_FINISHED, expected_version=running.state_version
    )
    verifying = env.store.transition_task(
        "task-1", TaskState.VERIFYING, expected_version=finished.state_version
    )
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=verifying.state_version,
        target=TaskState.VERIFIED if task_verified else TaskState.BLOCKED,
        reason="test: terminal truth",
        finalize=None,
    )
    env.store.connection.execute(
        "INSERT INTO runs (run_id, task_id, worker_id, pid, status, started_at,"
        " finished_at) VALUES (?, 'task-1', 'm3-sub', 4711, 'FINISHED', ?, ?)",
        (
            f"run-{dispatch_id}",
            NOW.isoformat(),
            (NOW + timedelta(seconds=60)).isoformat(),
        ),
    )
    env.store.connection.commit()
    return env, decision


def test_observation_only_proof_uses_real_routing_decision_id(tmp_path) -> None:
    """RECOVERY-1 (§26): the durable route-* decision id proves; the
    metadata clear is authorized by the exact finalized observation."""

    env, decision = _recovery_env(tmp_path, task_verified=True)
    routing = env.store.routing_decision_by_request_id(supervised_auto_routing_request_id(decision))
    assert routing is not None
    assert routing["decision_id"].startswith("route-")

    env.shadow.finalize_pending(
        decision,
        verified=True,
        execution_success=True,
        verification_success=True,
        observed_at=NOW,
    )
    env.shadow.discard_pending(decision)

    assert (
        real_execution_recovery_proof(env.store, env.shadow, task_id="task-1", pending_id=decision)
        is True
    )


def _observation_with_decision_id(env, decision: str, *, verified: bool) -> None:
    """Append an observation identical to the lifecycle identity except
    its ``decision_id`` is the auto id instead of route-*."""

    pending = env.shadow.load_pending(decision)
    env.shadow.append(
        ShadowObservation.build(
            task_id=pending.task_id,
            request_id=pending.request_id,
            decision_id=decision,  # the WRONG id (auto-*, not route-*)
            reset_cycle_ids=(),
            manual_execution_target_id=pending.manual_execution_target_id,
            scheduler_execution_target_id=pending.scheduler_execution_target_id,
            catalog_snapshot_id=pending.catalog_snapshot_id,
            policy_snapshot_id=pending.policy_snapshot_id,
            quota_snapshot_ids=pending.quota_snapshot_ids,
            provider_id=pending.provider_id,
            quota_pool_id=pending.quota_pool_id,
            task_family=pending.task_family,
            quota_confidence=pending.quota_confidence,
            collector_status=pending.collector_status,
            predicted_burn_fraction=pending.predicted_burn_fraction,
            execution_success=True,
            verification_success=True,
            verified=verified,
            observed_at=NOW,
        )
    )
    env.shadow.discard_pending(decision)


def test_auto_decision_id_as_decision_id_does_not_prove(tmp_path) -> None:
    """RECOVERY-2 (§27): pins the round-5 bug — observation.decision_id
    == auto_decision_id must NOT count as recovery proof."""

    env, decision = _recovery_env(tmp_path, task_verified=True)
    _observation_with_decision_id(env, decision, verified=True)

    assert (
        real_execution_recovery_proof(env.store, env.shadow, task_id="task-1", pending_id=decision)
        is False
    )


@pytest.mark.parametrize(
    "task_verified,observation_verified",
    [
        pytest.param(False, True, id="blocked-task-verified-shadow"),
        pytest.param(True, False, id="verified-task-blocked-shadow"),
    ],
)
def test_terminal_verdict_mismatch_does_not_prove(
    tmp_path, task_verified, observation_verified
) -> None:
    """RECOVERY-3 (§28): task/shadow terminal verdict inconsistency is
    never recovery proof — metadata stays preserved."""

    env, decision = _recovery_env(tmp_path, task_verified=task_verified)
    routing = env.store.routing_decision_by_request_id(supervised_auto_routing_request_id(decision))
    assert routing is not None
    pending = env.shadow.load_pending(decision)
    env.shadow.append(
        ShadowObservation.build(
            task_id=pending.task_id,
            request_id=pending.request_id,
            decision_id=routing["decision_id"],  # the REAL route-* id
            reset_cycle_ids=(),
            manual_execution_target_id=pending.manual_execution_target_id,
            scheduler_execution_target_id=pending.scheduler_execution_target_id,
            catalog_snapshot_id=pending.catalog_snapshot_id,
            policy_snapshot_id=pending.policy_snapshot_id,
            quota_snapshot_ids=pending.quota_snapshot_ids,
            provider_id=pending.provider_id,
            quota_pool_id=pending.quota_pool_id,
            task_family=pending.task_family,
            quota_confidence=pending.quota_confidence,
            collector_status=pending.collector_status,
            predicted_burn_fraction=pending.predicted_burn_fraction,
            execution_success=True,
            verification_success=True,
            verified=observation_verified,
            observed_at=NOW,
        )
    )
    env.shadow.discard_pending(decision)

    assert (
        real_execution_recovery_proof(env.store, env.shadow, task_id="task-1", pending_id=decision)
        is False
    )
