"""Post-worker shadow finalization crash recovery (review round 3).

A REAL worker executed and the authoritative verdict is durable in
SQLite — the pending shadow MUST become a truthful observation, never a
silent discard. All crash windows are simulated deterministically
(store-level commits without any drain, pending-file removals, direct
journal writes); no sleeps, no wall-clock races.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    ShadowFinalizationIntent,
    TaskState,
    shadow_identity_payload,
)
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal
from personal_ai_orchestrator.supervised_auto_step import (
    drain_auto_shadow_cleanup_outbox,
    drain_auto_shadow_finalize_outbox,
    supervised_auto_dispatch_request_id,
)
from tests.test_auto_crash_recovery import _restart_service
from tests.test_supervised_auto_step import NOW, _env, _plan

_TICK_LATER = NOW + timedelta(seconds=301)


def _drive_to_verifying(env) -> tuple[str, str]:
    """Legally drive the planned task into VERIFYING with a real run row."""

    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    # Round 6: the durable contract pins dispatch_id to
    # owner-dispatch-{request_id} (initiate_owner_dispatch); the
    # executor's run row is always run-{dispatch_id}.
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
    return decision, verifying.state_version


def _intent_for(env, decision: str, *, verified: bool, **overrides) -> ShadowFinalizationIntent:
    pending = env.shadow.load_pending(decision)
    fields = {
        "pending_id": decision,
        "task_id": "task-1",
        "dispatch_id": f"owner-dispatch-{decision}",
        "request_id": pending.request_id,
        "decision_id": pending.decision_id,
        "verified": verified,
        "execution_success": True,
        "verification_success": None,
        "observed_at": datetime.now(UTC).isoformat(),
        # Round 4 §17: the intent must be self-sufficient — freeze the
        # pending's immutable identity so restarts can prove replays
        # even after the pending file is gone.
        "identity_json": shadow_identity_payload(pending),
    }
    fields.update(overrides)
    return ShadowFinalizationIntent(**fields)


def _finalize_rows(store: SafetyKernelStore) -> list:
    return store.connection.execute(
        "SELECT finalization_id, pending_id, verified, execution_success, completed_at,"
        " payload_json FROM auto_shadow_finalize_outbox ORDER BY created_at"
    ).fetchall()


def _system_events(store: SafetyKernelStore, event_type: str) -> list:
    return store.connection.execute(
        "SELECT payload_json FROM audit_events WHERE event_type=? AND task_id IS NULL",
        (event_type,),
    ).fetchall()


def _cleanup_rows(store: SafetyKernelStore, pending_id: str) -> list:
    return store.connection.execute(
        "SELECT completed_at FROM auto_shadow_cleanup_outbox WHERE pending_id=?",
        (pending_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# §18 test A: crash after the terminal COMMIT, before the shadow finalize
# ---------------------------------------------------------------------------


def test_crash_after_verified_commit_recovers_observation(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, version = _drive_to_verifying(env)
    # The authoritative verdict + finalization intent commit atomically
    # (production path); the process dies before ANY filesystem work.
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=version,
        target=TaskState.VERIFIED,
        reason="deterministic verification passed: test",
        finalize=_intent_for(env, decision, verified=True),
    )
    assert env.store.get_task("task-1").state is TaskState.VERIFIED
    assert env.shadow.load_all() == ()  # crash: no observation yet
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=shadow2)
    service2.supervised_auto_tick(_TICK_LATER)

    task = store2.get_task("task-1")
    assert task.state is TaskState.VERIFIED
    assert task.auto_decision_id is None  # metadata eventually cleared
    observations = shadow2.load_all()
    assert len(observations) == 1
    assert observations[0].verified is True
    assert observations[0].execution_success is True
    # Pending removed ONLY through the finalize path.
    assert shadow2.load_pending_all() == ()
    rows = _finalize_rows(store2)
    assert len(rows) == 1 and rows[0]["completed_at"] is not None
    assert bool(rows[0]["verified"]) is True
    # No discard intent was ever enqueued for this pending (§16).
    assert _cleanup_rows(store2, decision) == []
    assert executor.unique_calls == []
    store2.close()


def test_crash_after_metadata_clear_still_recovers_observation(tmp_path: Path) -> None:
    """§24: once the intent is durable the metadata may clear — the
    outbox alone recovers the observation on restart."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, version = _drive_to_verifying(env)
    task = env.store.get_task("task-1")
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=version,
        target=TaskState.VERIFIED,
        reason="deterministic verification passed: test",
        finalize=_intent_for(env, decision, verified=True),
    )
    # The executor clears the metadata, then dies before the drain. The
    # guarded clear must NOT have promised the pending to the discard
    # path (a finalize intent is open).
    cleared = env.store.clear_auto_state_metadata(
        "task-1", expected_version=task.state_version + 1, reason="executor close"
    )
    assert cleared.auto_decision_id is None
    assert _cleanup_rows(env.store, decision) == []
    env.store.close()

    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2 = SafetyKernelStore(tmp_path / "safety.db")
    assert drain_auto_shadow_finalize_outbox(store2, shadow2) == 1
    observations = shadow2.load_all()
    assert len(observations) == 1 and observations[0].verified is True
    assert shadow2.load_pending_all() == ()
    assert _finalize_rows(store2)[0]["completed_at"] is not None
    store2.close()


# ---------------------------------------------------------------------------
# §19 test B: crash after the observation write, before discard/complete
# ---------------------------------------------------------------------------


def test_crash_after_observation_write_replays_identically(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, version = _drive_to_verifying(env)
    intent = _intent_for(env, decision, verified=True)
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=version,
        target=TaskState.VERIFIED,
        reason="deterministic verification passed: test",
        finalize=intent,
    )
    # The finalize wrote the observation, then the process died BEFORE
    # the pending discard and the completed marker.
    env.shadow.finalize_pending(
        decision,
        verified=True,
        execution_success=True,
        observed_at=datetime.fromisoformat(intent.observed_at),
    )
    before = env.shadow.load_all()[0]
    assert env.shadow.load_pending(decision) is not None
    env.store.close()

    store2 = SafetyKernelStore(tmp_path / "safety.db")
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    assert drain_auto_shadow_finalize_outbox(store2, shadow2) == 1
    observations = shadow2.load_all()
    assert len(observations) == 1
    after = observations[0]
    assert after == before  # byte-identical replay, no divergence
    assert shadow2.load_pending_all() == ()
    assert _finalize_rows(store2)[0]["completed_at"] is not None
    # A second drain is a pure no-op (already completed).
    assert drain_auto_shadow_finalize_outbox(store2, shadow2) == 0
    store2.close()


# ---------------------------------------------------------------------------
# §20 test C: verifier FAIL → BLOCKED terminal, crash before finalize
# ---------------------------------------------------------------------------


def test_crash_after_blocked_commit_finalizes_failed_verdict(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, version = _drive_to_verifying(env)
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=version,
        target=TaskState.BLOCKED,
        reason="deterministic verification failed",
        finalize=_intent_for(env, decision, verified=False, verification_success=False),
    )
    assert env.store.get_task("task-1").state is TaskState.BLOCKED
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=shadow2)
    service2.supervised_auto_tick(_TICK_LATER)

    task = store2.get_task("task-1")
    assert task.state is TaskState.BLOCKED  # terminal truth untouched
    assert task.auto_decision_id is None
    observations = shadow2.load_all()
    assert len(observations) == 1
    assert observations[0].verified is False
    assert observations[0].verification_success is False
    assert observations[0].execution_success is True  # worker ran fine
    assert shadow2.load_pending_all() == ()
    assert _cleanup_rows(store2, decision) == []  # never the discard path
    assert executor.unique_calls == []
    store2.close()


# ---------------------------------------------------------------------------
# §21 test D: worker non-zero exit (run FAILED) — old-build crash residue
# ---------------------------------------------------------------------------


def test_worker_failure_residue_is_finalized_not_discarded(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    # Old-build residue: worker FAILED + task BLOCKED, executor died
    # before ANY finalization intent existed.
    env.store.connection.execute(
        "UPDATE runs SET status='FAILED' WHERE run_id=?",
        (f"run-owner-dispatch-{supervised_auto_dispatch_request_id(decision)}",),
    )
    env.store.connection.commit()
    task = env.store.get_task("task-1")
    env.store.transition_task("task-1", TaskState.BLOCKED, expected_version=task.state_version)
    assert env.shadow.load_pending(decision) is not None
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=shadow2)
    service2.supervised_auto_tick(_TICK_LATER)

    task = store2.get_task("task-1")
    assert task.state is TaskState.BLOCKED
    assert task.auto_decision_id is None
    observations = shadow2.load_all()
    assert len(observations) == 1
    assert observations[0].verified is False
    assert observations[0].execution_success is False  # truthful failure
    assert shadow2.load_pending_all() == ()
    rows = _finalize_rows(store2)
    assert len(rows) == 1
    assert bool(rows[0]["execution_success"]) is False
    assert rows[0]["completed_at"] is not None
    assert _cleanup_rows(store2, decision) == []
    store2.close()


def test_existing_observation_residue_replays_not_diverges(tmp_path: Path) -> None:
    """Residue whose observation already exists (crash between the
    observation write and everything else): the reconstruction replays
    it byte-identically instead of writing a second one."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    observed_at = datetime.now(UTC)
    env.shadow.finalize_pending(
        decision, verified=True, execution_success=True, observed_at=observed_at
    )
    before = env.shadow.load_all()[0]
    task = env.store.get_task("task-1")
    env.store.transition_task("task-1", TaskState.VERIFIED, expected_version=task.state_version)
    env.store.close()

    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=None, shadow=shadow2)
    service2.supervised_auto_tick(_TICK_LATER)

    observations = shadow2.load_all()
    assert len(observations) == 1
    assert observations[0] == before
    assert shadow2.load_pending_all() == ()
    assert _finalize_rows(store2)[0]["completed_at"] is not None
    store2.close()


# ---------------------------------------------------------------------------
# §22: pre-worker failure must still DISCARD, never finalize
# ---------------------------------------------------------------------------


def test_preworker_failure_discards_without_observation(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    request_id = supervised_auto_dispatch_request_id(decision)
    env.store.reserve_owner_dispatch(
        dispatch_id=f"owner-dispatch-{request_id}",
        request_id=request_id,
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )
    env.store.mark_owner_dispatch_blocked(
        request_id,
        failure_code="QUOTA_EXHAUSTED",
        failure_reason="test admission failure",
    )
    env.tick(_TICK_LATER)

    assert env.store.get_task("task-1").state is TaskState.READY
    assert env.shadow.load_pending_all() == ()
    assert env.shadow.load_all() == ()  # NO finalized observation
    assert _finalize_rows(env.store) == []
    env.store.close()


# ---------------------------------------------------------------------------
# §25: intent immutability + observation idempotency
# ---------------------------------------------------------------------------


def test_intent_payload_conflict_fails_closed(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    intent = _intent_for(env, decision, verified=True)
    env.store.enqueue_shadow_finalization(intent)
    # Same payload → idempotent.
    env.store.enqueue_shadow_finalization(intent)
    # Different payload, same finalization id → fail closed.
    import pytest

    with pytest.raises(RuntimeError):
        env.store.enqueue_shadow_finalization(_intent_for(env, decision, verified=False))
    assert len(_finalize_rows(env.store)) == 1
    env.store.close()


def test_double_drain_yields_exactly_one_observation(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    env.store.enqueue_shadow_finalization(_intent_for(env, decision, verified=True))

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 1
    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0
    assert len(env.shadow.load_all()) == 1
    assert env.shadow.load_pending_all() == ()
    env.store.close()


# ---------------------------------------------------------------------------
# §12: pending missing during retry — prove the observation or fail closed
# ---------------------------------------------------------------------------


def test_missing_pending_with_proven_observation_completes(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    intent = _intent_for(env, decision, verified=True)
    env.store.enqueue_shadow_finalization(intent)
    env.shadow.finalize_pending(
        decision,
        verified=True,
        execution_success=True,
        observed_at=datetime.fromisoformat(intent.observed_at),
    )
    # Pending file lost AFTER the observation was durably written.
    env.shadow.pending_path_for(decision).unlink()

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 1
    assert _finalize_rows(env.store)[0]["completed_at"] is not None
    assert len(env.shadow.load_all()) == 1
    env.store.close()


def test_missing_pending_without_observation_fails_closed(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    env.store.enqueue_shadow_finalization(_intent_for(env, decision, verified=True))
    # Pending file lost BEFORE any observation existed (corruption /
    # wrong deletion): evidence loss must never be marked success.
    env.shadow.pending_path_for(decision).unlink()

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0
    row = _finalize_rows(env.store)[0]
    assert row["completed_at"] is None
    events = _system_events(env.store, "AUTO_SHADOW_FINALIZE_RETRY_FAILED")
    assert any(json.loads(e["payload_json"])["pending_id"] == decision for e in events)
    env.store.close()


def test_missing_pending_with_divergent_verdict_fails_closed(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    env.store.enqueue_shadow_finalization(
        _intent_for(env, decision, verified=False, execution_success=False)
    )
    # An observation exists but its verdict CONTRADICTS the intent.
    env.shadow.finalize_pending(
        decision, verified=True, execution_success=True, observed_at=datetime.now(UTC)
    )
    env.shadow.pending_path_for(decision).unlink()

    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 0
    assert _finalize_rows(env.store)[0]["completed_at"] is None
    env.store.close()


# ---------------------------------------------------------------------------
# §16 + §17: finalize beats discard; journal=None keeps intents OPEN
# ---------------------------------------------------------------------------


def test_finalize_intent_blocks_cleanup_discard(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    env.store.enqueue_shadow_finalization(_intent_for(env, decision, verified=True))
    # A stray open cleanup intent for the same pending (legacy residue).
    env.store.connection.execute(
        "INSERT OR IGNORE INTO auto_shadow_cleanup_outbox"
        " (pending_id, task_id, reason, created_at) VALUES (?, 'task-1', 'stray', ?)",
        (decision, datetime.now(UTC).isoformat()),
    )
    env.store.connection.commit()

    # Cleanup drain must NOT discard while the finalize intent is open.
    assert drain_auto_shadow_cleanup_outbox(env.store, env.shadow) == 0
    assert env.shadow.load_pending(decision) is not None

    # Finalize drain wins: observation written, pending discarded.
    assert drain_auto_shadow_finalize_outbox(env.store, env.shadow) == 1
    assert len(env.shadow.load_all()) == 1
    assert env.shadow.load_pending_all() == ()
    # Now the cleanup drain may finish its (already-satisfied) intent.
    assert drain_auto_shadow_cleanup_outbox(env.store, env.shadow) == 1
    assert _cleanup_rows(env.store, decision)[0]["completed_at"] is not None
    env.store.close()


def test_cleanup_intent_with_none_journal_stays_open(tmp_path: Path) -> None:
    """§17/§31: shadow_journal=None must NOT complete a cleanup intent."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision = env.store.get_task("task-1").auto_decision_id
    env.store.abort_auto_lifecycle("task-1", reason="test abort")
    assert _cleanup_rows(env.store, decision) != []

    assert drain_auto_shadow_cleanup_outbox(env.store, None) == 0
    row = _cleanup_rows(env.store, decision)[0]
    assert row["completed_at"] is None
    assert env.shadow.load_pending(decision) is not None

    # A healthy journal finishes the intent afterwards.
    assert drain_auto_shadow_cleanup_outbox(env.store, env.shadow) == 1
    assert _cleanup_rows(env.store, decision)[0]["completed_at"] is not None
    assert env.shadow.load_pending_all() == ()
    env.store.close()


def test_finalize_drain_with_none_journal_stays_open(tmp_path: Path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    decision, _ = _drive_to_verifying(env)
    env.store.enqueue_shadow_finalization(_intent_for(env, decision, verified=True))

    assert drain_auto_shadow_finalize_outbox(env.store, None) == 0
    row = _finalize_rows(env.store)[0]
    assert row["completed_at"] is None
    assert env.shadow.load_pending(decision) is not None
    env.store.close()
