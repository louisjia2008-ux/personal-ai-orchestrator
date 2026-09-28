from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    execution_target_has_launch_verification,
    begin_verification,
    record_worker_exit,
    reconcile_workspace_truth,
    start_worker_run,
    validate_execution_target_launch,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    EvidenceConfidence,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.shadow_evidence import (
    PendingShadowObservation,
    ResetCycleReference,
    ResetCycleSource,
    ShadowEvidenceJournal,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult


def _running_store(tmp_path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "writer-1")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    start_worker_run(
        store,
        run_id="run-1",
        task_id="t1",
        worker_id="worker",
        writer_token="writer-1",
    )
    return store


def _finish_worker(store: SafetyKernelStore) -> None:
    record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    begin_verification(store, task_id="t1")


def test_worker_run_requires_writer_lock(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="task/t1",
        base_sha="abc",
    )
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    with pytest.raises(RuntimeError, match="writer lock"):
        start_worker_run(
            store,
            run_id="run-1",
            task_id="t1",
            worker_id="worker",
            writer_token="not-owner",
        )


def test_task_cannot_have_two_active_worker_runs(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    with pytest.raises(RuntimeError, match="already has an active worker run"):
        start_worker_run(
            store,
            run_id="run-2",
            task_id="t1",
            worker_id="worker-2",
            writer_token="writer-1",
        )


def test_worker_exit_rejects_run_from_other_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    store.submit_task(task_id="t2", request_id="r2", intent="implement")
    store.register_workspace(
        task_id="t2",
        repo_path=str(tmp_path / "repo2"),
        worktree_path=str(tmp_path / "worktree2"),
        branch="task/t2",
        base_sha="def",
    )
    store.acquire_writer("t2", "writer-2")
    store.transition_task("t2", TaskState.READY)
    store.transition_task("t2", TaskState.RUNNING)
    start_worker_run(
        store,
        run_id="run-2",
        task_id="t2",
        worker_id="worker-2",
        writer_token="writer-2",
    )

    with pytest.raises(ValueError, match="does not belong"):
        record_worker_exit(
            store,
            task_id="t1",
            run_id="run-2",
            exit_code=0,
            worker_result={"status": "finished"},
        )
    assert store.get_task("t1").state is TaskState.RUNNING
    assert store.get_task("t2").state is TaskState.RUNNING


def test_unexpected_worker_exit_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=2,
        worker_result=None,
    )
    assert state is TaskState.BLOCKED


def test_invalid_worker_result_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result="COMPLETE",
    )
    assert state is TaskState.BLOCKED


def test_launch_gate_denies_catalog_only_target_even_when_enabled() -> None:
    registry = ModelRegistry(
        providers={"p": Provider(id="p", display_name="Provider")},
        accounts={"a": Account(id="a", provider_id="p", label="account")},
        models={"m": ModelSKU(id="m", provider_id="p", display_name="Model")},
        execution_targets={
            "target": ExecutionTarget(
                id="target",
                model_sku_id="m",
                account_id="a",
                runtime_id="opencode",
                enabled=True,
                execution_verified=False,
            )
        },
    )

    with pytest.raises(RuntimeError, match="not been runtime-verified"):
        validate_execution_target_launch(
            registry,
            execution_target_id="target",
            runtime_available=True,
        )


def test_launch_gate_denies_legacy_target_missing_execution_verified() -> None:
    registry = ModelRegistry(
        providers={"p": Provider(id="p", display_name="Provider")},
        accounts={"a": Account(id="a", provider_id="p", label="account")},
        models={"m": ModelSKU(id="m", provider_id="p", display_name="Model")},
        execution_targets={
            "target": ExecutionTarget(
                id="target",
                model_sku_id="m",
                account_id="a",
                runtime_id="opencode",
                enabled=True,
            )
        },
    )

    with pytest.raises(RuntimeError, match="not been runtime-verified"):
        validate_execution_target_launch(
            registry,
            execution_target_id="target",
            runtime_available=True,
        )


def _launch_registry(*, static_verified: bool = False) -> ModelRegistry:
    return ModelRegistry(
        providers={"p": Provider(id="p", display_name="Provider")},
        accounts={"a": Account(id="a", provider_id="p", label="account")},
        models={"m": ModelSKU(id="m", provider_id="p", display_name="Model")},
        execution_targets={
            "target": ExecutionTarget(
                id="target",
                model_sku_id="m",
                account_id="a",
                runtime_id="opencode",
                enabled=True,
                execution_verified=static_verified,
            )
        },
    )


def test_launch_verification_accepts_static_authority() -> None:
    registry = _launch_registry(static_verified=True)
    assert execution_target_has_launch_verification(registry, execution_target_id="target")
    validate_execution_target_launch(
        registry,
        execution_target_id="target",
        runtime_available=True,
    )


def test_launch_verification_requires_latest_fresh_verified_evidence(tmp_path: Path) -> None:
    registry = _launch_registry()
    journal = ExecutionEvidenceJournal(tmp_path)
    now = datetime(2030, 1, 31, tzinfo=UTC)
    journal.append(
        build_execution_evidence(
            provider_id="p",
            execution_target_id="target",
            model_sku_id="m",
            observed_at=now - timedelta(days=29),
            result=ExecutionVerificationOutcome.VERIFIED,
            reason_code="TEST_RECENT",
        )
    )
    assert execution_target_has_launch_verification(
        registry,
        execution_target_id="target",
        execution_evidence_journal=journal,
        now=now,
    )

    expired_now = now + timedelta(days=2)
    assert not execution_target_has_launch_verification(
        registry,
        execution_target_id="target",
        execution_evidence_journal=journal,
        now=expired_now,
    )

    journal.append(
        build_execution_evidence(
            provider_id="p",
            execution_target_id="target",
            model_sku_id="m",
            observed_at=expired_now,
            result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
            reason_code="TEST_LATEST_BLOCKED",
        )
    )
    assert not execution_target_has_launch_verification(
        registry,
        execution_target_id="target",
        execution_evidence_journal=journal,
        now=expired_now,
    )


def test_worker_success_requires_matching_persisted_verifier_evidence(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    assert state is TaskState.WORKER_FINISHED
    begin_verification(store, task_id="t1")
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-fixture",
    )
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    journal.append(result)
    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=journal,
        )
        is TaskState.VERIFIED
    )


def test_verified_execution_finalizes_pending_shadow_observation_once(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-shadow",
    )
    evidence = VerificationEvidenceJournal(tmp_path / "runtime-state")
    evidence.append(result)
    shadow = ShadowEvidenceJournal(tmp_path / "shadow")
    shadow.append_pending(
        PendingShadowObservation(
            pending_id="pending-shadow",
            task_id="t1",
            request_id="route-1",
            decision_id="decision-1",
            manual_execution_target_id="m3-sub",
            scheduler_execution_target_id="m3-sub",
            catalog_snapshot_id="catalog-1",
            policy_snapshot_id="policy-1",
            quota_snapshot_ids=("quota-before",),
            provider_id="minimax",
            quota_pool_id="minimax-token-plan-cn",
            task_family="implementation",
            predicted_burn_fraction=0.05,
            started_at=datetime(2026, 8, 30, tzinfo=UTC),
        )
    )
    shadow.append_reset_cycle(
        ResetCycleReference(
            reset_cycle_id="minimax-week-1",
            provider_id="minimax",
            quota_pool_id="minimax-token-plan-cn",
            quota_snapshot_id="quota-before",
            reset_at=datetime(2026, 8, 30, tzinfo=UTC),
            confidence=EvidenceConfidence.EXACT,
            source=ResetCycleSource.PROVIDER_EXACT,
            source_method="fixture-provider-reset-metadata",
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        )
    )

    state = apply_verification_result(
        store,
        task_id="t1",
        result=result,
        evidence_journal=evidence,
        shadow_journal=shadow,
        shadow_pending_id="pending-shadow",
        shadow_reset_cycle_ids=("minimax-week-1",),
        shadow_quota_after_snapshot_ids=("quota-after",),
        shadow_observed_burn_fraction=0.04,
        shadow_attempts_to_green=1,
        shadow_time_to_green_seconds=42.0,
    )

    observations = shadow.load_all()
    assert state is TaskState.VERIFIED
    assert len(observations) == 1
    assert observations[0].verified is True
    assert observations[0].recommendation_followed is True
    assert observations[0].reset_cycle_ids == ("minimax-week-1",)
    assert observations[0].observed_burn_fraction == 0.04


def test_failed_verification_finalizes_truthful_shadow_observation(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=False,
        changed_paths=("src/tiny_math.py",),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-shadow-failed",
        failure_reason="verifier command failed: pytest",
    )
    evidence = VerificationEvidenceJournal(tmp_path / "runtime-state")
    evidence.append(result)
    shadow = ShadowEvidenceJournal(tmp_path / "shadow")
    shadow.append_pending(
        PendingShadowObservation(
            pending_id="pending-shadow-failed",
            task_id="t1",
            request_id="route-1",
            decision_id="decision-1",
            manual_execution_target_id="codex-cli-gpt-5.5",
            scheduler_execution_target_id=None,
            catalog_snapshot_id="catalog-1",
            policy_snapshot_id="policy-1",
            quota_snapshot_ids=("quota-unknown",),
            provider_id="openai",
            quota_pool_id="codex-chatgpt-plan",
            task_family="implementation",
            started_at=datetime(2026, 8, 30, tzinfo=UTC),
        )
    )

    state = apply_verification_result(
        store,
        task_id="t1",
        result=result,
        evidence_journal=evidence,
        shadow_journal=shadow,
        shadow_pending_id="pending-shadow-failed",
    )

    observations = shadow.load_all()
    assert state is TaskState.BLOCKED
    assert len(observations) == 1
    assert observations[0].verified is False
    assert observations[0].reset_cycle_ids == ()


def test_forged_non_null_evidence_id_blocks_claimed_pass(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-forged",
    )
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=journal,
        )
        is TaskState.BLOCKED
    )


def test_mismatched_persisted_evidence_blocks_claimed_pass(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    persisted = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=("src/a.py",),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-same-id",
    )
    claimed = persisted.model_copy(update={"changed_paths": ("src/b.py",)})
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    journal.append(persisted)
    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=claimed,
            evidence_journal=journal,
        )
        is TaskState.BLOCKED
    )


def test_malformed_persisted_evidence_blocks_claimed_pass(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-malformed",
    )
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    target = journal.path_for("verify-malformed")
    target.parent.mkdir(parents=True)
    target.write_text("{not-json\n", encoding="utf-8")

    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=journal,
        )
        is TaskState.BLOCKED
    )


def test_missing_verifier_evidence_blocks_even_claimed_pass(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id=None,
    )
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=journal,
        )
        is TaskState.BLOCKED
    )


def test_failing_verifier_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=False,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-failure",
        failure_reason="injected failing test",
    )
    journal = VerificationEvidenceJournal(tmp_path / "runtime-state")
    journal.append(result)
    assert (
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=journal,
        )
        is TaskState.BLOCKED
    )


def test_missing_worktree_blocks_and_clears_stale_writer_lock(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    missing = tmp_path / "does-not-exist"
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(missing),
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "writer-1")
    assert reconcile_workspace_truth(store) == ("t1",)
    assert store.get_task("t1").state is TaskState.BLOCKED
    assert store.get_workspace("t1").writer_token is None


def test_unexpected_exit_keeps_only_sanitized_transcript_tails(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=2,
        worker_result={
            "exit_code": 2,
            "stdout_sha256": "h",
            "stdout_bytes": 1024,
            "stderr_sha256": "h",
            "stderr_bytes": 512,
            "output_truncated": False,
            "timed_out": False,
            "stdout_tail": "stdout narrative",
            "stderr_tail": "Usage limit reached for 5 hour",
        },
    )
    row = store.connection.execute(
        "SELECT status, result_json FROM runs WHERE run_id='run-1'"
    ).fetchone()
    assert row["status"] == "FAILED"
    import json as _json

    result = _json.loads(row["result_json"])
    # Fail-closed: host-derived metadata is gone; the worker's narration
    # is the only thing that survives so the owner can see WHY.
    assert result == {
        "exit_code": 2,
        "stderr_tail": "Usage limit reached for 5 hour",
        "stdout_tail": "stdout narrative",
    }


def test_failure_result_payload_signal_only_omits_exit_code() -> None:
    from personal_ai_orchestrator.execution_controller import _failure_result_payload

    payload = _failure_result_payload(exit_code=None, signal=9, worker_result=None)
    assert payload == {"signal": 9}


def test_failure_result_payload_preserves_only_tails_from_worker_result() -> None:
    from personal_ai_orchestrator.execution_controller import _failure_result_payload

    payload = _failure_result_payload(
        exit_code=2,
        signal=None,
        worker_result={
            "stdout_sha256": "h",
            "stdout_bytes": 1024,
            "stderr_sha256": "h",
            "stderr_bytes": 512,
            "stdout_tail": "stdout narrative",
            "stderr_tail": "Usage limit reached for 5 hour",
            "extra_unknown_key": "ignored",
        },
    )
    assert payload == {
        "exit_code": 2,
        "stderr_tail": "Usage limit reached for 5 hour",
        "stdout_tail": "stdout narrative",
    }


def test_human_reason_for_failure_names_exit_code_and_signal() -> None:
    from personal_ai_orchestrator.execution_controller import (
        _human_reason_for_failure,
    )

    assert _human_reason_for_failure(exit_code=2, signal=None) == (
        "worker exited unexpectedly with code 2"
    )
    assert _human_reason_for_failure(exit_code=None, signal=9) == (
        "worker exited unexpectedly (signal 9)"
    )
    assert _human_reason_for_failure(exit_code=None, signal=None) == ("worker exited unexpectedly")
