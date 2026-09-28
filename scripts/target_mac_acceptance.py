#!/usr/bin/env python3
"""Target-Mac acceptance runner for local safety and routing gates.

The runner uses only disposable Git repositories and file-backed runtime state.
It emits sanitized JSON evidence and does not read provider credential stores.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    cancel_worker_run,
    reconcile_workspace_truth,
    record_worker_exit,
    start_worker_run,
)
from personal_ai_orchestrator.local_api import MAX_REQUEST_BYTES, handler_for, serve
from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    ConsumptionRule,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.opencode_contract import (
    AdapterAction,
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
    resolve_adapter_outcome,
)
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import TargetTelemetry, TaskProfile
from personal_ai_orchestrator.verification_evidence import (
    RetryPolicy,
    VerificationEvidenceJournal,
    VerificationFailureClass,
    classify_failure,
)
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerificationResult,
    VerificationStage,
    VerifierCommand,
    VerifierProfile,
)
from personal_ai_orchestrator.worktree_manager import WorktreeManager

NOW = datetime.now(UTC)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _status(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", **details}


def _init_repo(root: Path, name: str) -> tuple[Path, str]:
    repo = root / name
    repo.mkdir(parents=True)
    _run(["git", "init", str(repo)])
    _git(repo, "config", "user.email", "acceptance@example.invalid")
    _git(repo, "config", "user.name", "Target Mac Acceptance")
    (repo / "src").mkdir()
    (repo / "src" / "value.txt").write_text("one\n", encoding="utf-8")
    (repo / "src" / "test_value.py").write_text(
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    assert Path('src/value.txt').read_text() == 'two\\n'\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _prepare_task(
    store: SafetyKernelStore,
    *,
    task_id: str,
    request_id: str,
    repo: Path,
    worktree: Path,
    branch: str,
    base_sha: str,
    writer_token: str,
) -> None:
    store.submit_task(task_id=task_id, request_id=request_id, intent="acceptance")
    store.register_workspace(
        task_id=task_id,
        repo_path=str(repo),
        worktree_path=str(worktree),
        branch=branch,
        base_sha=base_sha,
    )
    store.acquire_writer(task_id, writer_token)
    store.transition_task(task_id, TaskState.READY)
    store.transition_task(task_id, TaskState.RUNNING)


async def _p0_acceptance(root: Path) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, Any]] = []
    repo, base_sha = _init_repo(root, "p0-source")
    managed_root = root / "p0-worktrees"
    manager = WorktreeManager(managed_root)
    store = SafetyKernelStore(root / "p0.sqlite3")
    journal_mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0].upper()

    first = store.submit_task(task_id="p0-idempotent", request_id="req-idempotent", intent="p0")
    second = store.submit_task(task_id="p0-idempotent", request_id="req-idempotent", intent="p0")
    evidence.append(
        _status(
            "sqlite_wal_and_idempotent_submission",
            journal_mode == "WAL" and first == second,
            journal_mode=journal_mode,
        )
    )

    before = _git(repo, "rev-parse", "HEAD")
    managed = manager.create(repo_path=repo, task_id="p0-owned", base_sha=base_sha)
    after = _git(repo, "rev-parse", "HEAD")
    store.submit_task(task_id="p0-owned", request_id="req-owned", intent="p0")
    store.register_workspace(
        task_id="p0-owned",
        repo_path=str(repo),
        worktree_path=str(managed.worktree_path),
        branch=managed.branch,
        base_sha=managed.base_sha,
    )
    store.acquire_writer("p0-owned", "writer-a")
    second_writer_blocked = False
    try:
        store.acquire_writer("p0-owned", "writer-b")
    except RuntimeError:
        second_writer_blocked = True
    evidence.append(
        _status(
            "host_owned_worktree_and_single_writer",
            managed.worktree_path.exists()
            and before == after == base_sha
            and _git(managed.worktree_path, "rev-parse", "HEAD") == base_sha
            and second_writer_blocked,
            source_head=after[:12],
            worktree_head=_git(managed.worktree_path, "rev-parse", "HEAD")[:12],
        )
    )

    supervisor = ProcessSupervisor()
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=root,
        start_new_session=True,
    )
    cancellation_passed = False
    try:
        supervised = await supervisor.start(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=managed.worktree_path,
        )
        store.transition_task("p0-owned", TaskState.READY)
        store.transition_task("p0-owned", TaskState.RUNNING)
        start_worker_run(
            store,
            task_id="p0-owned",
            run_id="run-cancel",
            worker_id="fixture-worker",
            writer_token="writer-a",
            pid=supervised.pid,
        )
        cancelled = await cancel_worker_run(
            store,
            supervisor,
            supervised,
            task_id="p0-owned",
            run_id="run-cancel",
            writer_token="writer-a",
            grace_seconds=0.2,
        )
        cancellation_passed = (
            cancelled is TaskState.CANCELLED
            and supervised.pid not in supervisor.owned_pids()
            and unrelated.poll() is None
            and store.get_workspace("p0-owned").writer_token is None
        )
    finally:
        if unrelated.poll() is None:
            os.killpg(unrelated.pid, signal.SIGTERM)
            unrelated.wait(timeout=5)
    evidence.append(
        _status(
            "exact_process_cancellation_preserves_unrelated_process",
            cancellation_passed,
        )
    )

    for task_id, request_id, exit_code, worker_result, expected in (
        ("p0-nonzero", "req-nonzero", 7, {"status": "failed"}, TaskState.BLOCKED),
        ("p0-malformed", "req-malformed", 0, "COMPLETE", TaskState.BLOCKED),
    ):
        task_managed = manager.create(repo_path=repo, task_id=task_id, base_sha=base_sha)
        _prepare_task(
            store,
            task_id=task_id,
            request_id=request_id,
            repo=repo,
            worktree=task_managed.worktree_path,
            branch=task_managed.branch,
            base_sha=base_sha,
            writer_token=f"writer-{task_id}",
        )
        start_worker_run(
            store,
            task_id=task_id,
            run_id=f"run-{task_id}",
            worker_id="fixture-worker",
            writer_token=f"writer-{task_id}",
        )
        state = record_worker_exit(
            store,
            task_id=task_id,
            run_id=f"run-{task_id}",
            exit_code=exit_code,
            worker_result=worker_result,
        )
        evidence.append(
            _status(
                f"{task_id}_worker_result_blocks",
                state is expected,
                final_state=state.value,
            )
        )

    missing = manager.create(repo_path=repo, task_id="p0-missing", base_sha=base_sha)
    store.submit_task(task_id="p0-missing", request_id="req-missing", intent="p0")
    store.transition_task("p0-missing", TaskState.READY)
    store.register_workspace(
        task_id="p0-missing",
        repo_path=str(repo),
        worktree_path=str(missing.worktree_path),
        branch=missing.branch,
        base_sha=base_sha,
    )
    store.acquire_writer("p0-missing", "writer-missing")
    manager.remove("p0-missing")
    blocked = reconcile_workspace_truth(store)
    evidence.append(
        _status(
            "missing_worktree_reconciliation_blocks_and_releases_writer",
            blocked == ("p0-missing",)
            and store.get_task("p0-missing").state is TaskState.BLOCKED
            and store.get_workspace("p0-missing").writer_token is None,
        )
    )

    restart_store = SafetyKernelStore(root / "p0-restart.sqlite3")
    restart_store.submit_task(task_id="p0-restart", request_id="req-restart", intent="p0")
    restart_store.transition_task("p0-restart", TaskState.READY)
    restart_store.transition_task("p0-restart", TaskState.RUNNING)
    restart_store.start_run(
        run_id="run-restart",
        task_id="p0-restart",
        worker_id="fixture-worker",
        pid=123456,
    )
    restart_store.close()
    reopened = SafetyKernelStore(root / "p0-restart.sqlite3")
    startup_blocked = reopened.reconcile_startup()
    run_status = reopened.connection.execute(
        "SELECT status FROM runs WHERE run_id='run-restart'"
    ).fetchone()[0]
    evidence.append(
        _status(
            "startup_reconciliation_blocks_uncertain_state",
            startup_blocked == ("p0-restart",)
            and reopened.get_task("p0-restart").state is TaskState.BLOCKED
            and run_status == "INTERRUPTED",
            run_status=run_status,
        )
    )
    reopened.close()
    store.close()
    return evidence


def _verifying_store(
    root: Path,
    task_id: str,
    repo: Path,
    worktree: Path,
    branch: str,
    base_sha: str,
) -> SafetyKernelStore:
    store = SafetyKernelStore(root / f"{task_id}.sqlite3")
    _prepare_task(
        store,
        task_id=task_id,
        request_id=f"req-{task_id}",
        repo=repo,
        worktree=worktree,
        branch=branch,
        base_sha=base_sha,
        writer_token=f"writer-{task_id}",
    )
    start_worker_run(
        store,
        task_id=task_id,
        run_id=f"run-{task_id}",
        worker_id="fixture-worker",
        writer_token=f"writer-{task_id}",
    )
    record_worker_exit(
        store,
        task_id=task_id,
        run_id=f"run-{task_id}",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    begin_verification(store, task_id=task_id)
    return store


def _p1_acceptance(root: Path) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, Any]] = []
    repo, base_sha = _init_repo(root, "p1-source")
    manager = WorktreeManager(root / "p1-worktrees")
    verifier = DeterministicVerifier()

    passing = manager.create(repo_path=repo, task_id="p1-pass", base_sha=base_sha)
    (passing.worktree_path / "src" / "value.txt").write_text("two\n", encoding="utf-8")
    profile = VerifierProfile(
        name="fixture",
        allowed_paths=("src",),
        commands=(
            VerifierCommand(
                name="targeted pytest",
                argv=(sys.executable, "-m", "pytest", "-q", "src/test_value.py"),
                timeout_seconds=30,
            ),
        ),
    )
    result = verifier.verify(passing.worktree_path, base_sha=base_sha, profile=profile)
    journal = VerificationEvidenceJournal(root / "p1-runtime-state")
    journal.append(result)
    store = _verifying_store(root, "p1-pass", repo, passing.worktree_path, passing.branch, base_sha)
    final = apply_verification_result(
        store,
        task_id="p1-pass",
        result=result,
        evidence_journal=journal,
    )
    evidence.append(
        _status(
            "passing_verification_requires_persisted_evidence",
            result.passed and final is TaskState.VERIFIED and result.evidence_id is not None,
            evidence_id=result.evidence_id,
        )
    )
    store.close()

    cases: list[tuple[str, str, VerifierProfile]] = []
    fail_test = manager.create(repo_path=repo, task_id="p1-failing-test", base_sha=base_sha)
    (fail_test.worktree_path / "src" / "value.txt").write_text("wrong\n", encoding="utf-8")
    cases.append(("failing_targeted_test_blocks_verified", "p1-failing-test", profile))

    diff_bad = manager.create(repo_path=repo, task_id="p1-diff-check", base_sha=base_sha)
    (diff_bad.worktree_path / "src" / "value.txt").write_text("two   \n", encoding="utf-8")
    cases.append(("git_diff_check_failure_blocks_verified", "p1-diff-check", profile))

    outside = manager.create(repo_path=repo, task_id="p1-outside", base_sha=base_sha)
    (outside.worktree_path / "outside.txt").write_text("unexpected\n", encoding="utf-8")
    cases.append(("unexpected_path_blocks_verified", "p1-outside", profile))

    command_fail = manager.create(repo_path=repo, task_id="p1-command-fail", base_sha=base_sha)
    (command_fail.worktree_path / "src" / "value.txt").write_text("two\n", encoding="utf-8")
    failing_profile = VerifierProfile(
        name="fixture-failing-command",
        allowed_paths=("src",),
        commands=(
            VerifierCommand(
                name="intentional false",
                argv=(sys.executable, "-c", "raise SystemExit(3)"),
                timeout_seconds=30,
            ),
        ),
    )
    cases.append(("verifier_command_failure_blocks_verified", "p1-command-fail", failing_profile))

    for name, task_id, case_profile in cases:
        managed = manager._owned[task_id]
        case_result = verifier.verify(
            managed.worktree_path,
            base_sha=base_sha,
            profile=case_profile,
        )
        case_store = _verifying_store(
            root,
            task_id,
            repo,
            managed.worktree_path,
            managed.branch,
            base_sha,
        )
        if case_result.evidence_id is not None:
            journal.append(case_result)
        case_final = apply_verification_result(
            case_store,
            task_id=task_id,
            result=case_result,
            evidence_journal=journal,
        )
        evidence.append(
            _status(
                name,
                not case_result.passed and case_final is TaskState.BLOCKED,
                failure_reason=case_result.failure_reason,
                failure_class=classify_failure(case_result).value,
            )
        )
        case_store.close()

    forged_result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(VerificationStage(name="synthetic", argv=("true",), returncode=0),),
        evidence_id="verify-forged-acceptance",
    )
    forged_store = _verifying_store(
        root,
        "p1-missing-evidence",
        repo,
        passing.worktree_path,
        passing.branch,
        base_sha,
    )
    forged_final = apply_verification_result(
        forged_store,
        task_id="p1-missing-evidence",
        result=forged_result,
        evidence_journal=journal,
    )
    evidence.append(_status("missing_evidence_blocks_verified", forged_final is TaskState.BLOCKED))
    forged_store.close()

    malformed_store = _verifying_store(
        root,
        "p1-malformed-evidence",
        repo,
        passing.worktree_path,
        passing.branch,
        base_sha,
    )
    malformed_path = journal.path_for("verify-malformed-acceptance")
    malformed_path.parent.mkdir(parents=True, exist_ok=True)
    malformed_path.write_text("{not-json\n", encoding="utf-8")
    malformed_result = forged_result.model_copy(
        update={"evidence_id": "verify-malformed-acceptance"}
    )
    malformed_final = apply_verification_result(
        malformed_store,
        task_id="p1-malformed-evidence",
        result=malformed_result,
        evidence_journal=journal,
    )
    evidence.append(
        _status(
            "malformed_evidence_blocks_verified",
            malformed_final is TaskState.BLOCKED,
        )
    )
    malformed_store.close()

    duplicate_blocked = False
    mutated = result.model_copy(update={"profile": "mutated"})
    try:
        journal.append(mutated)
    except ValueError:
        duplicate_blocked = True
    retry_policy = RetryPolicy(max_known_flaky_infra_retries=1)
    evidence.append(
        _status(
            "immutable_evidence_and_retry_policy",
            duplicate_blocked
            and not retry_policy.should_retry(
                failure_class=VerificationFailureClass.COMMAND_FAILURE,
                previous_retries=0,
                host_attested_known_flaky_infra=False,
            )
            and retry_policy.should_retry(
                failure_class=VerificationFailureClass.KNOWN_FLAKY_INFRA,
                previous_retries=0,
                host_attested_known_flaky_infra=True,
            ),
        )
    )

    restart_store = _verifying_store(
        root,
        "p1-restart-before-verified",
        repo,
        passing.worktree_path,
        passing.branch,
        base_sha,
    )
    restart_store.close()
    reopened = SafetyKernelStore(root / "p1-restart-before-verified.sqlite3")
    blocked = reopened.reconcile_startup()
    evidence.append(
        _status(
            "restart_between_finished_and_verified_blocks",
            blocked == ("p1-restart-before-verified",)
            and reopened.get_task("p1-restart-before-verified").state is TaskState.BLOCKED,
        )
    )
    reopened.close()
    return evidence


def _registry() -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=NOW,
        reference="target-mac-acceptance-fixture",
        confidence=EvidenceConfidence.EXACT,
    )
    window = QuotaWindowSnapshot(
        window_id="window-five-hour",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=5 * 60 * 60,
        remaining_fraction=0.8,
        used_fraction=0.2,
        window_started_at=NOW - timedelta(hours=1),
        reset_at=NOW + timedelta(hours=4),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-target-mac-fixture",
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax")},
        accounts={"account": Account(id="account", provider_id="minimax", label="fixture")},
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Fixture Subscription",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="fixture-pool",
                snapshot=snapshot,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
            )
        },
        models={
            "m3": ModelSKU(
                id="m3",
                provider_id="minimax",
                display_name="M3",
                capabilities=CapabilityProfile(scores={"debugging": 0.9, "reasoning": 0.85}),
            )
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding",
                model_sku_id="m3",
                execution_target_id="m3-sub",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
        consumption_rules=(
            ConsumptionRule(
                id="rule",
                model_sku_id="m3",
                execution_target_id="m3-sub",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(pool=PoolKind.WORKER, model_sku_id="m3", execution_target_id="m3-sub"),
        ),
    )


def _post_json(
    url: str,
    payload: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers or {"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _routing_acceptance(root: Path) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, Any]] = []
    store = SafetyKernelStore(root / "routing.sqlite3")
    store.submit_task(task_id="route-task", request_id="req-route-task", intent="routing")
    ready = store.transition_task("route-task", TaskState.READY)
    service = RoutingService(
        registry=_registry(),
        store=store,
        catalog_snapshot_id="catalog-target-mac-fixture",
        runtime_availability={"m3-sub": True},
        telemetry={
            "m3-sub": TargetTelemetry(
                success_prior=0.9,
                context_window_tokens=200000,
                supported_tools=("edit", "test"),
            )
        },
    )
    service.set_task_profile(
        TaskProfile(
            task_id="route-task",
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/opencode/route"
    try:
        payload = json.dumps(
            {
                "request_id": "req-shadow",
                "session_id": "session-1",
                "task_id": "route-task",
                "task_state_version": ready.state_version,
                "mode": "SHADOW",
                "requested_at": NOW.isoformat(),
            }
        ).encode("utf-8")
        status, body = _post_json(
            url,
            payload,
            {"content-type": "application/json", "origin": "http://localhost:3000"},
        )
        decision = RoutingDecision.model_validate(body)
        outcome = resolve_adapter_outcome(
            RoutingRequest(
                request_id="req-shadow",
                session_id="session-1",
                task_id="route-task",
                task_state_version=ready.state_version,
                mode=RoutingMode.SHADOW,
                requested_at=NOW,
            ),
            decision,
        )
        persisted = store.connection.execute(
            "SELECT decision_id FROM routing_decisions WHERE request_id='req-shadow'"
        ).fetchone()
        evidence.append(
            _status(
                "shadow_route_persists_decision_without_switch",
                status == 200
                and decision.selected_model == ModelRef(provider_id="minimax", model_id="m3")
                and decision.switch_requested is False
                and outcome.action is AdapterAction.RECORD_ONLY
                and persisted is not None
                and persisted["decision_id"] == decision.decision_id,
                decision_id=decision.decision_id,
                quota_snapshot_ids=decision.quota_snapshot_ids,
            )
        )

        active_payload = json.dumps(
            {
                "request_id": "req-active-denied",
                "session_id": "session-1",
                "task_id": "route-task",
                "task_state_version": ready.state_version,
                "mode": "ACTIVE",
                "requested_at": NOW.isoformat(),
            }
        ).encode("utf-8")
        active_status, active_body = _post_json(url, active_payload)
        active_decision = RoutingDecision.model_validate(active_body)
        evidence.append(
            _status(
                "production_active_remains_disabled",
                active_status == 200
                and active_decision.selected_model is not None
                and active_decision.switch_requested is False
                and "production ACTIVE gate not authorized"
                in (active_decision.fallback_reason or ""),
            )
        )

        non_json_status, non_json_body = _post_json(
            url,
            b"hello",
            {"content-type": "text/plain"},
        )
        spoof_status, spoof_body = _post_json(
            url,
            payload.replace(b"req-shadow", b"req-origin-spoof"),
            {"content-type": "application/json", "origin": "http://localhost.evil.invalid"},
        )
        large_status, large_body = _post_json(
            url,
            b"{" + (b'"x":' + b'"a"' * MAX_REQUEST_BYTES) + b"}",
            {"content-type": "application/json"},
        )
        evidence.append(
            _status(
                "loopback_json_origin_and_size_guards",
                non_json_status == 415
                and non_json_body == {"error": "json_required"}
                and spoof_status == 403
                and spoof_body == {"error": "invalid_origin"}
                and large_status == 413
                and large_body == {"error": "request_too_large_or_empty"},
                non_json_status=non_json_status,
                spoof_status=spoof_status,
                large_status=large_status,
            )
        )

        unavailable_outcome = resolve_adapter_outcome(
            RoutingRequest(
                request_id="req-daemon-down",
                session_id="session-1",
                mode=RoutingMode.SHADOW,
                requested_at=NOW,
            ),
            None,
            daemon_error="TIMEOUT",
        )
        malformed_outcome = resolve_adapter_outcome(
            RoutingRequest(
                request_id="req-current",
                session_id="session-1",
                mode=RoutingMode.ACTIVE,
                task_state_version=ready.state_version,
                requested_at=NOW,
            ),
            active_decision.model_copy(update={"request_id": "different"}),
        )
        bypass_outcome = resolve_adapter_outcome(
            RoutingRequest(
                request_id="req-bypass",
                session_id="session-1",
                mode=RoutingMode.BYPASS,
                requested_at=NOW,
            ),
            None,
        )
        evidence.append(
            _status(
                "adapter_fail_closed_outcomes",
                unavailable_outcome.action is AdapterAction.KEEP_CURRENT
                and malformed_outcome.action is AdapterAction.KEEP_CURRENT
                and bypass_outcome.action is AdapterAction.KEEP_CURRENT,
                unavailable_reason=unavailable_outcome.reason,
                malformed_reason=malformed_outcome.reason,
                bypass_reason=bypass_outcome.reason,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        store.close()

    bind_rejected = False
    try:
        serve(service, host="0.0.0.0", port=0)
    except ValueError:
        bind_rejected = True
    evidence.append(_status("non_loopback_bind_rejected", bind_rejected))

    gates = [
        ActiveRoutingGate(
            p0_safety_kernel_authoritative=True,
            p1_verifier_authoritative=True,
            adapter_fail_closed_validated=True,
            shadow_evidence_accepted=True,
            safe_bypass_validated=True,
            owner_approved=True,
        ).authorized,
        ActiveRoutingGate(
            p0_safety_kernel_authoritative=False,
            p1_verifier_authoritative=True,
            adapter_fail_closed_validated=True,
            shadow_evidence_accepted=True,
            safe_bypass_validated=True,
            owner_approved=True,
        ).authorized,
        ActiveRoutingGate(
            p0_safety_kernel_authoritative=True,
            p1_verifier_authoritative=True,
            adapter_fail_closed_validated=True,
            shadow_evidence_accepted=True,
            safe_bypass_validated=True,
            owner_approved=False,
        ).authorized,
    ]
    evidence.append(
        _status(
            "active_gate_requires_all_prerequisites",
            gates == [True, False, False],
        )
    )
    return evidence


def _versions() -> dict[str, str]:
    values: dict[str, str] = {
        "python": sys.version.split()[0],
        "node": _run(["node", "--version"], check=False).stdout.strip() or "UNKNOWN",
        "opencode": "NOT_FOUND",
    }
    sw_vers = _run(["sw_vers"], check=False)
    values["macos"] = (
        sw_vers.stdout.strip().replace("\n", "; ") if sw_vers.returncode == 0 else "UNKNOWN"
    )
    opencode = _run(["opencode", "--version"], check=False)
    if opencode.returncode == 0:
        values["opencode"] = opencode.stdout.strip()
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run target Mac local acceptance checks")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Directory for disposable repos, state DBs and evidence JSON.",
    )
    args = parser.parse_args(argv)

    runtime_root = args.runtime_root
    if runtime_root is None:
        runtime_root = Path(
            tempfile.mkdtemp(prefix="pao-target-mac-acceptance-", dir="/private/tmp")
        )
    runtime_root.mkdir(parents=True, exist_ok=True)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "runtime_root": str(runtime_root),
        "head": _git(Path.cwd(), "rev-parse", "HEAD"),
        "versions": _versions(),
        "p0_safety_kernel": asyncio.run(_p0_acceptance(runtime_root / "p0")),
        "p1_deterministic_verifier": _p1_acceptance(runtime_root / "p1"),
        "opencode_routing": _routing_acceptance(runtime_root / "routing"),
        "provider_live_quota": {
            "MiniMax": "NOT_EXECUTED_SUPPORTED_AUTH_SURFACE_REQUIRED",
            "Z.AI / GLM": "NOT_EXECUTED_SUPPORTED_AUTH_SURFACE_REQUIRED",
            "OpenAI / Codex": "UNKNOWN_NO_SUPPORTED_MACHINE_READABLE_SUBSCRIPTION_QUOTA",
            "Anthropic / Claude": "UNKNOWN_NO_SUPPORTED_MACHINE_READABLE_SUBSCRIPTION_QUOTA",
            "DeepSeek": "UNKNOWN_PAYG_BALANCE_NOT_SUBSCRIPTION_QUOTA",
            "local": "NOT_EXECUTED_CAPACITY_IS_NOT_PERCENTAGE_QUOTA",
        },
        "production_active": "DISABLED_BY_DESIGN_OWNER_APPROVAL_NOT_CREATED",
    }
    all_checks = [
        item
        for section in ("p0_safety_kernel", "p1_deterministic_verifier", "opencode_routing")
        for item in report[section]
    ]
    report["overall_local_runtime_acceptance"] = (
        "PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED"
        if all(item["status"] == "PASS" for item in all_checks)
        else "FAIL"
    )
    target = runtime_root / "target-mac-acceptance-report.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)
    print(report["overall_local_runtime_acceptance"])
    return 0 if report["overall_local_runtime_acceptance"].startswith("PASS_LOCAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
