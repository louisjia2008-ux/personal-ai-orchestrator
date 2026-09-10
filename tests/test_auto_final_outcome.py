"""Final host-outcome ordering + recovery guard (review round 4).

P0-1: the immutable shadow finalize intent must freeze the FINAL
host-authoritative outcome — verifier authority AND main-repo
immutability composed BEFORE the single terminal commit. A passing
verifier whose run mutated the main repo finalizes exactly one
``verified=False`` / ``verification_success=True`` observation; there is
no normal VERIFIED → BLOCKED downgrade after finalization.

P0-2: a real-run lifecycle whose pending file was never written (or was
lost) may never clear ``auto_decision_id`` — the last recovery
correlation — without a durable finalize intent or a proven finalized
observation.

P1: an existing observation only proves an intent when it equals the
EXACT expected semantic observation rebuilt from the durable intent.
"""

from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.dispatch_executor import DispatchExecutorConfig
from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    ShadowFinalizationIntent,
    TaskState,
    shadow_identity_payload,
)
from personal_ai_orchestrator.shadow_evidence import ShadowFailureClass
from personal_ai_orchestrator.supervised_auto_step import (
    drain_auto_shadow_finalize_outbox,
    supervised_auto_dispatch_request_id,
)
from tests.test_dispatch_executor import HELLO_CONTENT, make_profile
from tests.test_pending_shadow_lifecycle import _auto_harness, _reserve_auto_task
from tests.test_supervised_auto_step import NOW, _env, _plan

_TICK_LATER = NOW + timedelta(seconds=301)


def _finalize_rows(store: SafetyKernelStore) -> list:
    return store.connection.execute(
        "SELECT finalization_id, pending_id, verified, execution_success,"
        " verification_success, failure_class, completed_at"
        " FROM auto_shadow_finalize_outbox ORDER BY created_at"
    ).fetchall()


def _system_events(store: SafetyKernelStore, event_type: str) -> list:
    return store.connection.execute(
        "SELECT payload_json FROM audit_events WHERE event_type=? AND task_id IS NULL",
        (event_type,),
    ).fetchall()


def _cleanup_rows(store: SafetyKernelStore, pending_id: str) -> list:
    return store.connection.execute(
        "SELECT pending_id, completed_at FROM auto_shadow_cleanup_outbox"
        " WHERE pending_id=?",
        (pending_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# §19/§20 — final outcome ordering through the REAL executor
# ---------------------------------------------------------------------------


def test_verifier_pass_but_main_repo_mutated_finalizes_blocked(tmp_path) -> None:
    """§19: verifier PASS + main repo mutation → ONE blocked truth."""

    harness, journal = _auto_harness(tmp_path)
    request_id = _reserve_auto_task(harness, journal)
    # The scripted worker succeeds in the worktree AND escapes to write
    # an untracked file into the MAIN repository — the fingerprint
    # (HEAD + porcelain digest) changes while the verifier still passes.
    mutating = harness.tmp_path / "bin-mutate"
    mutating.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{HELLO_CONTENT}' > hello.txt\n"
        f"printf 'rogue\\n' >> '{harness.main_repo}/rogue.txt'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    mutating.chmod(0o755)
    harness.executor.config = DispatchExecutorConfig(
        repo_path=harness.main_repo,
        worktree_root=harness.worktree_root,
        opencode_bin=str(mutating),
        worker_timeout_seconds=30.0,
        verifier_profile=make_profile(),
    )

    harness.executor.execute(request_id)
    snap = harness.snapshot()
    try:
        assert snap["task"].state is TaskState.BLOCKED
        assert (harness.main_repo / "rogue.txt").exists()
        # Exactly ONE observation, freezing the FINAL host outcome: the
        # worker and the verifier both succeeded, the host safety
        # composition failed — never an earlier verified=True shadow.
        observations = journal.load_all()
        assert len(observations) == 1
        assert observations[0].verified is False
        assert observations[0].execution_success is True
        assert observations[0].verification_success is True
        assert observations[0].failure_class is ShadowFailureClass.INFRA_FAILURE
        # The finalize intent is durable, immutable and completed with
        # the same composed verdict.
        rows = _finalize_rows(snap["store"])
        assert len(rows) == 1
        assert bool(rows[0]["verified"]) is False
        assert bool(rows[0]["execution_success"]) is True
        assert bool(rows[0]["verification_success"]) is True
        assert rows[0]["failure_class"] == "INFRA_FAILURE"
        assert rows[0]["completed_at"] is not None
        # The pending was discarded via the finalize drain and the
        # recovery-proven metadata cleared.
        assert journal.load_pending_all() == ()
        assert snap["task"].auto_decision_id is None
    finally:
        snap["store"].close()
    assert not harness.main_unchanged()


def test_verifier_pass_main_unchanged_finalizes_verified(tmp_path) -> None:
    """§20: the happy path still lands VERIFIED with one true shadow."""

    harness, journal = _auto_harness(tmp_path)
    request_id = _reserve_auto_task(harness, journal)

    harness.executor.execute(request_id)
    snap = harness.snapshot()
    try:
        assert snap["task"].state is TaskState.VERIFIED
        observations = journal.load_all()
        assert len(observations) == 1
        assert observations[0].verified is True
        assert observations[0].execution_success is True
        assert observations[0].verification_success is True
        rows = _finalize_rows(snap["store"])
        assert len(rows) == 1 and bool(rows[0]["verified"]) is True
        assert rows[0]["completed_at"] is not None
        assert snap["task"].auto_decision_id is None
    finally:
        snap["store"].close()
    assert harness.main_unchanged()


# ---------------------------------------------------------------------------
# §21/§22/§23 — missing pending recovery (P0-2)
# ---------------------------------------------------------------------------


def test_missing_pending_before_verification_preserves_recovery(tmp_path) -> None:
    """§21: a real worker ran but the pending file never existed.

    The pending identity cannot be proven from durable truth (the
    full immutable identity — snapshots, targets, provider — lives in
    the pending file), so the lifecycle fails closed: task BLOCKED,
    ``auto_decision_id`` PRESERVED, no discard, no fake observation, a
    sanitized system event.
    """

    harness, journal = _auto_harness(tmp_path)
    request_id = _reserve_auto_task(harness, journal)
    # Simulate the pending write having failed at planning time.
    for pending in journal.load_pending_all():
        journal.discard_pending(pending.pending_id)

    harness.executor.execute(request_id)
    snap = harness.snapshot()
    try:
        assert snap["task"].state is TaskState.BLOCKED
        # The last recovery correlation is still on the task row.
        assert snap["task"].auto_decision_id == "auto-task-1-v0"
        # No observation was invented and nothing was promised to the
        # discard path.
        assert journal.load_all() == ()
        assert _finalize_rows(snap["store"]) == []
        assert _cleanup_rows(snap["store"], "auto-task-1-v0") == []
        assert len(_system_events(snap["store"], "AUTO_SHADOW_RECOVERY_GUARD_HELD")) == 1
    finally:
        snap["store"].close()


def test_terminal_sweep_missing_pending_preserves_metadata(tmp_path) -> None:
    """§22: old-build residue — terminal BLOCKED + real exact run +
    pending missing + no intent + no observation → fail closed."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    dispatch_id = f"owner-dispatch-{decision}"
    request_id = supervised_auto_dispatch_request_id(decision)
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
        target=TaskState.BLOCKED,
        reason="test residue: verifier failed",
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
    # The pending file was lost before any finalize intent existed.
    env.shadow.discard_pending(decision)

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.BLOCKED
    assert task.auto_decision_id == decision  # PRESERVED
    assert env.shadow.load_all() == ()  # no fake observation
    assert _cleanup_rows(env.store, decision) == []  # no discard promise
    rows = _finalize_rows(env.store)
    assert rows == []  # nothing completed, nothing fabricated
    assert len(
        _system_events(env.store, "AUTO_SHADOW_FINALIZE_RECONSTRUCTION_FAILED")
    ) == 1


def test_durable_intent_allows_clear_and_outbox_alone_recovers(tmp_path) -> None:
    """§23: a durable finalize intent (self-sufficient payload) is the
    clear authority — restart recovery proves the observation without
    the pending file and without the task's auto metadata."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    pending = env.shadow.load_pending(decision)
    dispatch_id = f"owner-dispatch-{decision}"
    request_id = supervised_auto_dispatch_request_id(decision)
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
    pinned = datetime.now(UTC).isoformat()
    intent = ShadowFinalizationIntent(
        pending_id=decision,
        task_id="task-1",
        dispatch_id=dispatch_id,
        request_id=pending.request_id,
        decision_id=pending.decision_id,
        verified=False,
        execution_success=True,
        verification_success=True,
        observed_at=pinned,
        identity_json=shadow_identity_payload(pending),
    )
    env.store.apply_verification_outcome(
        "task-1",
        expected_version=verifying.state_version,
        target=TaskState.BLOCKED,
        reason="test: blocked verdict with durable intent",
        finalize=intent,
    )
    # Crash window: the finalize + discard happened on the filesystem
    # but the completed marker never committed.
    env.shadow.finalize_pending(
        decision,
        verified=False,
        execution_success=True,
        verification_success=True,
        observed_at=datetime.fromisoformat(pinned),
    )
    env.shadow.discard_pending(decision)

    env.tick(_TICK_LATER)

    task = env.store.get_task("task-1")
    assert task.state is TaskState.BLOCKED
    # §8A: the durable intent authorized the metadata clear...
    assert task.auto_decision_id is None
    # ...and the restart drain proved + completed the observation from
    # the outbox payload ALONE (pending gone, metadata gone).
    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is not None
    assert len(env.shadow.load_all()) == 1
    assert _cleanup_rows(env.store, decision) == []


# ---------------------------------------------------------------------------
# §24-§27 — full semantic observation proof (P1)
# ---------------------------------------------------------------------------


def _proof_env(tmp_path, **intent_overrides):
    """Store + journal + pending + durable intent (no task states)."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    decision = task.auto_decision_id
    pending = env.shadow.load_pending(decision)
    pinned = datetime.now(UTC).isoformat()
    fields = {
        "pending_id": decision,
        "task_id": "task-1",
        "request_id": pending.request_id,
        "decision_id": pending.decision_id,
        "verified": False,
        "execution_success": True,
        "verification_success": True,
        "observed_at": pinned,
        "identity_json": shadow_identity_payload(pending),
    }
    fields.update(intent_overrides)
    intent = ShadowFinalizationIntent(**fields)
    env.store.enqueue_shadow_finalization(intent)
    return env, decision, intent


def _drain_once(env) -> int:
    return drain_auto_shadow_finalize_outbox(env.store, env.shadow)


def test_divergent_failure_class_is_not_proof(tmp_path) -> None:
    """§24: same identity/verified/execution_success, different
    failure_class → the intent stays OPEN with a retry event."""

    env, decision, intent = _proof_env(tmp_path)
    env.shadow.finalize_pending(
        decision,
        verified=False,
        execution_success=True,
        verification_success=True,
        failure_class=ShadowFailureClass.MODEL_TASK_FAILURE,
        observed_at=datetime.fromisoformat(intent.observed_at),
    )
    env.shadow.discard_pending(decision)

    assert _drain_once(env) == 0
    rows = _finalize_rows(env.store)
    assert len(rows) == 1 and rows[0]["completed_at"] is None
    assert len(_system_events(env.store, "AUTO_SHADOW_FINALIZE_RETRY_FAILED")) == 1


def test_exact_semantic_match_completes_without_duplicate(tmp_path) -> None:
    """§25: an exactly-equal observation completes the intent once."""

    env, decision, intent = _proof_env(tmp_path)
    env.shadow.finalize_pending(
        decision,
        verified=False,
        execution_success=True,
        verification_success=True,
        observed_at=datetime.fromisoformat(intent.observed_at),
    )
    env.shadow.discard_pending(decision)

    assert _drain_once(env) == 1
    rows = _finalize_rows(env.store)
    assert rows[0]["completed_at"] is not None
    assert len(env.shadow.load_all()) == 1
    # Duplicate drains are no-ops — exactly one observation.
    assert _drain_once(env) == 0
    assert len(env.shadow.load_all()) == 1


def test_divergent_observed_at_is_not_proof(tmp_path) -> None:
    """§26: the pinned ``observed_at`` is part of the frozen payload —
    the same digest with a different timestamp diverges."""

    env, decision, intent = _proof_env(tmp_path)
    env.shadow.finalize_pending(
        decision,
        verified=False,
        execution_success=True,
        verification_success=True,
        observed_at=datetime.fromisoformat(intent.observed_at)
        + timedelta(seconds=1),
    )
    env.shadow.discard_pending(decision)

    assert _drain_once(env) == 0
    rows = _finalize_rows(env.store)
    assert rows[0]["completed_at"] is None
    assert len(_system_events(env.store, "AUTO_SHADOW_FINALIZE_RETRY_FAILED")) == 1


def test_divergent_quota_or_burn_data_is_not_proof(tmp_path) -> None:
    """§27: divergent ``quota_after_snapshot_ids`` / burn fraction are
    not exact intent fulfillment."""

    env, decision, intent = _proof_env(tmp_path)
    env.shadow.finalize_pending(
        decision,
        verified=False,
        execution_success=True,
        verification_success=True,
        quota_after_snapshot_ids=("quota-after-divergent",),
        observed_at=datetime.fromisoformat(intent.observed_at),
    )
    env.shadow.discard_pending(decision)
    assert _drain_once(env) == 0

    second = tmp_path / "second"
    second.mkdir()
    env2, decision2, intent2 = _proof_env(second, observed_burn_fraction=0.5)
    env2.shadow.finalize_pending(
        decision2,
        verified=False,
        execution_success=True,
        verification_success=True,
        observed_burn_fraction=0.25,
        observed_at=datetime.fromisoformat(intent2.observed_at),
    )
    env2.shadow.discard_pending(decision2)
    assert drain_auto_shadow_finalize_outbox(env2.store, env2.shadow) == 0
    rows2 = _finalize_rows(env2.store)
    assert rows2[0]["completed_at"] is None
