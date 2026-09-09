"""M1 WP5a-2 §26 — the SUPERVISED_AUTO pending-shadow lifecycle.

The invariant: a pending shadow is created exactly once at planning and
reaches exactly one terminal disposition — finalized into a real
observation when a worker + verifier verdict exists, discarded on every
abort exit. No path may leave a pending row hanging.

The successful-dispatch cases run the REAL ``OwnerDispatchExecutor``
with the scripted worker + deterministic verifier from the executor
test harness, so the finalize truth comes from the actual production
close-out path (worker exit → verifier → ``apply_verification_result``
→ pending discard → metadata clear).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from personal_ai_orchestrator.dispatch_executor import (
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
)
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.shadow_evidence import (
    PendingShadowObservation,
    ShadowEvidenceJournal,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from tests.test_dispatch_executor import (
    HELLO_CONTENT,
    ExecutorHarness,
    _git,
    make_profile,
)
from tests.test_supervised_auto_step import NOW, _env, _plan

HELLO_REQUEST_ID = "supervised-auto-dispatch-auto-task-1-v0"


def _pending(journal: ShadowEvidenceJournal, pending_id: str) -> PendingShadowObservation:
    return journal.load_pending(pending_id)


# ---------------------------------------------------------------------------
# planning creates / repeats do not duplicate / aborts discard
# ---------------------------------------------------------------------------


def test_planning_creates_exactly_one_pending(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    pending = _pending(env.shadow, task.auto_decision_id)
    assert pending.task_id == "task-1"
    assert pending.scheduler_execution_target_id == "m3-sub"
    assert pending.manual_execution_target_id == "m3-sub"
    assert pending.decision_id
    # Repeated ticks inside the grace window do not duplicate.
    env.tick(NOW + timedelta(seconds=5))
    env.tick(NOW + timedelta(seconds=10))
    assert len(env.shadow.load_pending_all()) == 1


def test_mode_abort_discards_pending(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    assert len(env.shadow.load_pending_all()) == 1
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    assert env.shadow.load_pending_all() == ()


def test_unacked_timeout_discards_pending(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    assert len(env.shadow.load_pending_all()) == 1
    env.tick(NOW + timedelta(hours=25))
    assert env.shadow.load_pending_all() == ()


def test_admission_failure_discards_pending(tmp_path) -> None:
    """§3.4 step 4 fail branch: a pre-worker BLOCKED dispatch discards."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    env.store.reserve_owner_dispatch(
        dispatch_id=f"owner-dispatch-{HELLO_REQUEST_ID}",
        request_id=HELLO_REQUEST_ID,
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )
    env.store.mark_owner_dispatch_blocked(
        HELLO_REQUEST_ID,
        failure_code="QUOTA_EXHAUSTED",
        failure_reason="test admission failure",
    )
    env.tick(NOW + timedelta(seconds=301))
    assert env.store.get_task("task-1").state is TaskState.READY
    assert env.shadow.load_pending_all() == ()


# ---------------------------------------------------------------------------
# real executor: verified finalize + failed finalize + duplicate guard
# ---------------------------------------------------------------------------


def _auto_harness(tmp_path: Path) -> tuple[ExecutorHarness, ShadowEvidenceJournal]:
    harness = ExecutorHarness(tmp_path)
    # Rebuild the executor with the shadow journal attached (the base
    # harness predates WP5a-2 and omits it).
    harness.executor = OwnerDispatchExecutor(
        state_db=harness.state_db,
        config=DispatchExecutorConfig(
            repo_path=harness.main_repo,
            worktree_root=harness.worktree_root,
            opencode_bin=str(harness.worker_bin),
            worker_timeout_seconds=30.0,
            verifier_profile=make_profile(),
        ),
        registry_provider=lambda: harness.registry,
        verification_journal=VerificationEvidenceJournal(harness.runtime_root),
        execution_evidence_journal=ExecutionEvidenceJournal(harness.runtime_root),
        quota_availability_journal=QuotaAvailabilityJournal(harness.runtime_root),
        quota_collectors={},
        shadow_journal=ShadowEvidenceJournal(harness.runtime_root),
    )
    journal = ShadowEvidenceJournal(harness.runtime_root)
    return harness, journal


def _reserve_auto_task(harness: ExecutorHarness, journal: ShadowEvidenceJournal) -> str:
    """Drive one task into AUTO_GRACE with a pending shadow + reservation."""

    store = SafetyKernelStore(harness.state_db)
    try:
        base_sha = _git(harness.main_repo, "rev-parse", "HEAD")
        store.register_project(
            project_id="project-main",
            display_name="Main Repo",
            canonical_repo_root=str(harness.main_repo),
            git_root=str(harness.main_repo),
            default_branch="main",
            last_known_head=base_sha,
        )
        store.submit_task(
            task_id="task-1",
            request_id="submit-task-1",
            project_id="project-main",
            base_sha=base_sha,
            intent=f"Create hello.txt with exactly: {HELLO_CONTENT}",
        )
        store.transition_task("task-1", TaskState.READY, expected_version=0)
        planned = store.transition_task(
            "task-1",
            TaskState.AUTO_PLANNED,
            expected_version=1,
            auto_decision_id="auto-task-1-v0",
        )
        store.transition_task(
            "task-1",
            TaskState.AUTO_GRACE,
            expected_version=planned.state_version,
            auto_grace_deadline_at="1970-01-01T00:00:00+00:00",
        )
        store.reserve_owner_dispatch(
            dispatch_id=f"owner-dispatch-{HELLO_REQUEST_ID}",
            request_id=HELLO_REQUEST_ID,
            task_id="task-1",
            task_state_version=planned.state_version + 1,
            execution_target_id="zai-coding-plan-glm-5.3",
            authority="SUPERVISED_AUTO",
        )
        journal.append_pending(
            PendingShadowObservation(
                pending_id="auto-task-1-v0",
                task_id="task-1",
                request_id=HELLO_REQUEST_ID,
                decision_id="frozen-decision-task-1",
                manual_execution_target_id="zai-coding-plan-glm-5.3",
                scheduler_execution_target_id="zai-coding-plan-glm-5.3",
                catalog_snapshot_id="catalog-test",
                policy_snapshot_id="policy-test",
                quota_snapshot_ids=("quota-harness",),
                started_at=NOW,
            )
        )
    finally:
        store.close()
    return HELLO_REQUEST_ID


def test_verified_dispatch_finalizes_pending_exactly_once(tmp_path) -> None:
    harness, journal = _auto_harness(tmp_path)
    request_id = _reserve_auto_task(harness, journal)

    harness.executor.execute(request_id)
    snap = harness.snapshot()
    try:
        assert snap["task"].state is TaskState.VERIFIED
        # The pending row is gone; exactly one finalized observation
        # exists with the truthful verified outcome.
        assert journal.load_pending_all() == ()
        observations = journal.load_all()
        assert len(observations) == 1
        assert observations[0].task_id == "task-1"
        assert observations[0].verified is True
        assert observations[0].execution_success is True
        assert (
            observations[0].scheduler_execution_target_id
            == "zai-coding-plan-glm-5.3"
        )
        # Metadata cleared off the active row (§32) and the dispatch is
        # FINISHED with the SUPERVISED_AUTO authority.
        assert snap["task"].auto_decision_id is None
        assert snap["dispatch_status"] == "FINISHED"
        # Re-running the executor is a no-op (dispatch terminal) — no
        # duplicate observation, no state resurrection.
        harness.executor.execute(request_id)
        assert len(journal.load_all()) == 1
    finally:
        snap["store"].close()
    assert harness.main_unchanged()


def test_failed_worker_finalizes_pending_with_failed_outcome(tmp_path) -> None:
    harness, journal = _auto_harness(tmp_path)
    request_id = _reserve_auto_task(harness, journal)
    # Break the worker so the run fails: replace the binary with one
    # that exits non-zero without creating the file.
    failing = harness.tmp_path / "bin-failing"
    failing.write_text(
        "#!/bin/sh\nexit 3\n",
        encoding="utf-8",
    )
    failing.chmod(0o755)
    harness.executor.config = DispatchExecutorConfig(
        repo_path=harness.main_repo,
        worktree_root=harness.worktree_root,
        opencode_bin=str(failing),
        worker_timeout_seconds=30.0,
        verifier_profile=make_profile(),
    )

    harness.executor.execute(request_id)
    snap = harness.snapshot()
    try:
        assert snap["task"].state is TaskState.BLOCKED
        assert journal.load_pending_all() == ()
        observations = journal.load_all()
        assert len(observations) == 1
        assert observations[0].verified is False
        assert observations[0].execution_success is False
        assert snap["task"].auto_decision_id is None
    finally:
        snap["store"].close()
    assert harness.main_unchanged()
