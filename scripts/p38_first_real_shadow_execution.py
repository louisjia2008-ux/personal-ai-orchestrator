#!/usr/bin/env python3
"""Run one credential-safe real worker through the Shadow evidence pipeline."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    record_worker_exit,
    start_worker_run,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelCatalogSnapshot,
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
)
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshotJournal
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import TaskProfile
from personal_ai_orchestrator.shadow_evidence import (
    ShadowCampaignState,
    ShadowCampaignStatus,
    ShadowEvidenceJournal,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)

ACTUAL_EXECUTION_TARGET = "codex-cli-gpt-5.5"
PROVIDER_ID = "openai"
QUOTA_POOL_ID = "codex-chatgpt-plan"
TASK_FAMILY = "worker"


def _run(argv: list[str], *, cwd: Path, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_disposable_repo(root: Path) -> tuple[str, str]:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "tiny_math.py").write_text(
        'def add_one(value: int) -> int:\n    """Return value plus one."""\n    return value\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_tiny_math.py").write_text(
        "import unittest\n\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n\n"
        "from tiny_math import add_one\n\n\n"
        "class TinyMathTests(unittest.TestCase):\n"
        "    def test_add_one(self) -> None:\n"
        "        self.assertEqual(add_one(3), 4)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    _run(["git", "init"], cwd=root)
    _run(["git", "config", "user.name", "P3.8 Acceptance"], cwd=root)
    _run(["git", "config", "user.email", "p38-acceptance@example.invalid"], cwd=root)
    _run(["git", "add", "src/tiny_math.py", "tests/test_tiny_math.py"], cwd=root)
    _run(["git", "commit", "-m", "initial failing fixture"], cwd=root)
    base_sha = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    branch = _run(["git", "branch", "--show-current"], cwd=root).stdout.strip()
    return base_sha, branch


def _catalog_snapshot(now: datetime) -> ModelCatalogSnapshot:
    return ModelCatalogSnapshot(
        id="catalog-p38-real-codex",
        source="p38-real-shadow-acceptance",
        as_of=now,
        fetched_at=now,
        content_hash="codex-cli-gpt-5.5-existing-auth",
    )


def _registry(now: datetime) -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=now,
        reference="codex-cli-existing-auth",
        note="Codex CLI probe identified provider openai and model gpt-5.5 without reading secrets",
        confidence=EvidenceConfidence.UNKNOWN,
    )
    snapshot = QuotaSnapshot(
        id="quota-p38-codex-unknown",
        quota_pool_id=QUOTA_POOL_ID,
        provider_id=PROVIDER_ID,
        account_id="codex-cli-account",
        plan_id="codex-chatgpt-plan",
        observed_at=now,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=source,
    )
    return ModelRegistry(
        providers={PROVIDER_ID: Provider(id=PROVIDER_ID, display_name="OpenAI")},
        accounts={
            "codex-cli-account": Account(
                id="codex-cli-account",
                provider_id=PROVIDER_ID,
                label="Codex CLI existing login",
            )
        },
        plans={
            "codex-chatgpt-plan": Plan(
                id="codex-chatgpt-plan",
                account_id="codex-cli-account",
                name="Codex CLI existing login",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        catalog_snapshots={"catalog-p38-real-codex": _catalog_snapshot(now)},
        quota_pools={
            QUOTA_POOL_ID: QuotaPool(
                id=QUOTA_POOL_ID,
                plan_id="codex-chatgpt-plan",
                name="Codex subscription quota unknown",
                snapshot=snapshot,
            )
        },
        models={
            "gpt-5.5": ModelSKU(
                id="gpt-5.5",
                provider_id=PROVIDER_ID,
                display_name="GPT-5.5",
                catalog_snapshot_id="catalog-p38-real-codex",
                capabilities=CapabilityProfile(scores={"implementation": 0.9}),
            )
        },
        execution_targets={
            ACTUAL_EXECUTION_TARGET: ExecutionTarget(
                id=ACTUAL_EXECUTION_TARGET,
                model_sku_id="gpt-5.5",
                account_id="codex-cli-account",
                runtime_id="codex-cli",
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding-p38-codex",
                model_sku_id="gpt-5.5",
                execution_target_id=ACTUAL_EXECUTION_TARGET,
                quota_pool_id=QUOTA_POOL_ID,
                effective_from=now - timedelta(days=1),
                recorded_at=now,
                confidence=EvidenceConfidence.UNKNOWN,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="gpt-5.5",
                execution_target_id=ACTUAL_EXECUTION_TARGET,
            ),
        ),
    )


def _worker_prompt() -> str:
    return (
        "You are operating only inside this disposable repository. "
        "Fix the failing deterministic unit test by editing only src/tiny_math.py. "
        "The intended behavior is add_one(value) returns value + 1. "
        "Do not edit tests, do not access secrets, do not use network, and keep the change "
        "minimal. After making the change, run python3 -B -m unittest discover -s tests and "
        "summarize the result."
    )


def _start_codex_worker(repo: Path) -> tuple[subprocess.Popen[str], datetime]:
    codex = shutil.which("codex")
    if codex is None:
        raise RuntimeError("codex CLI is not available")
    argv = [
        codex,
        "exec",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "-m",
        "gpt-5.5",
        "-C",
        str(repo),
        "-s",
        "workspace-write",
        "-c",
        "approval_policy='never'",
        _worker_prompt(),
    ]
    started_at = datetime.now(UTC)
    process = subprocess.Popen(
        argv,
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, started_at


def _wait_codex_worker(process: subprocess.Popen[str]) -> tuple[str, str, int, datetime]:
    stdout, stderr = process.communicate(timeout=360)
    finished_at = datetime.now(UTC)
    return stdout[-12000:], stderr[-12000:], process.returncode, finished_at


def _safe_worker_stderr(stderr: str, *, exit_code: int) -> str:
    if exit_code == 0 and len(stderr) > 2000:
        return "OMITTED_SUCCESSFUL_CODEX_CLI_DIAGNOSTICS"
    return stderr[-4000:]


def _ensure_campaign_collecting(journal: ShadowEvidenceJournal) -> tuple[str | None, str | None]:
    state = journal.load_campaign_state()
    before = None if state is None else state.status.value
    if state is not None and state.status is ShadowCampaignStatus.BOOTSTRAPPED:
        journal.save_campaign_state(
            state.model_copy(update={"status": ShadowCampaignStatus.COLLECTING})
        )
        return before, ShadowCampaignStatus.COLLECTING.value
    return before, None if state is None else state.status.value


def main() -> int:
    now = datetime.now(UTC)
    repo_root = Path.cwd()
    report_root = repo_root / ".personal-ai-orchestrator" / "p38-shadow"
    evidence_run_id = f"evidence-{uuid4().hex[:12]}"
    runtime_root = report_root / evidence_run_id / "shadow-runtime"
    report_root.mkdir(parents=True, exist_ok=True)

    shadow_journal = ShadowEvidenceJournal(runtime_root)
    quality_before = 0
    campaign_before = None

    disposable_repo = Path(tempfile.mkdtemp(prefix="pao-p38-real-shadow-"))
    base_sha, disposable_branch = _write_disposable_repo(disposable_repo)
    task_id = f"p38-real-shadow-{uuid4().hex[:12]}"
    run_id = f"run-{uuid4().hex[:12]}"
    routing_request_id = f"route-{uuid4().hex[:12]}"
    writer_token = f"writer-{uuid4().hex[:12]}"

    store = SafetyKernelStore(report_root / "p38-safety.sqlite3")
    verification_journal = VerificationEvidenceJournal(report_root)
    policy_journal = PolicySnapshotJournal(report_root)
    service = RoutingService(
        registry=_registry(now),
        store=store,
        catalog_snapshot_id="catalog-p38-real-codex",
        policy_journal=policy_journal,
        runtime_availability={ACTUAL_EXECUTION_TARGET: True},
        shadow_journal=shadow_journal,
        shadow_actual_execution_targets={task_id: ACTUAL_EXECUTION_TARGET},
    )
    service.set_task_profile(
        TaskProfile(
            task_id=task_id,
            pool=PoolKind.WORKER,
            required_capabilities={"implementation": 0.8},
            predicted_quota_fraction_p90=0.01,
        )
    )
    store.submit_task(task_id=task_id, request_id=f"submit-{task_id}", intent="p38 real shadow")
    store.register_workspace(
        task_id=task_id,
        repo_path=str(disposable_repo),
        worktree_path=str(disposable_repo),
        branch=disposable_branch,
        base_sha=base_sha,
    )
    store.acquire_writer(task_id, writer_token)
    ready = store.transition_task(task_id, TaskState.READY, reason="P3.8 disposable task ready")

    routing_request = RoutingRequest(
        request_id=routing_request_id,
        session_id=f"p38-{task_id}",
        project_id="p38-disposable",
        location=str(disposable_repo),
        agent="codex-cli",
        mode=RoutingMode.SHADOW,
        task_id=task_id,
        task_state_version=ready.state_version,
        requested_at=now,
    )
    decision = service.route(routing_request, now=now)
    shadow_journal.save_campaign_state(
        ShadowCampaignState(
            campaign_id=f"p38-real-shadow-{evidence_run_id}",
            status=ShadowCampaignStatus.BOOTSTRAPPED,
            started_at=now,
            head=_run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip(),
            catalog_snapshot_id=decision.catalog_snapshot_id,
            policy_snapshot_id=decision.policy_snapshot_id,
            providers_unknown=(PROVIDER_ID,),
        )
    )
    quality_before = shadow_journal.summarize_campaign().quality_observations
    campaign_before = shadow_journal.load_campaign_state()
    pending_id = f"pending-{decision.decision_id}"
    store.transition_task(task_id, TaskState.RUNNING, expected_version=ready.state_version)

    worker_start_monotonic = monotonic()
    worker_process, worker_started_at = _start_codex_worker(disposable_repo)
    start_worker_run(
        store,
        task_id=task_id,
        run_id=run_id,
        worker_id="codex-cli",
        writer_token=writer_token,
        pid=worker_process.pid,
    )
    worker_stdout, worker_stderr, worker_exit, worker_finished_at = _wait_codex_worker(
        worker_process
    )
    safe_worker_stderr = _safe_worker_stderr(worker_stderr, exit_code=worker_exit)
    time_to_green_seconds = monotonic() - worker_start_monotonic
    next_state = record_worker_exit(
        store,
        task_id=task_id,
        run_id=run_id,
        exit_code=worker_exit,
        worker_result={
            "status": "FINISHED" if worker_exit == 0 else "FAILED",
            "provider": PROVIDER_ID,
            "execution_target_id": ACTUAL_EXECUTION_TARGET,
            "stdout_tail": worker_stdout,
            "stderr_tail": safe_worker_stderr,
        },
    )

    verifier_result_id = None
    final_task_state = next_state
    observation_id = None
    changed_files: tuple[str, ...] = ()
    if next_state is TaskState.WORKER_FINISHED:
        begin_verification(store, task_id=task_id)
        verifier = DeterministicVerifier()
        verifier_result = verifier.verify(
            disposable_repo,
            base_sha=base_sha,
            profile=VerifierProfile(
                name="p38-disposable-unittest",
                allowed_paths=("src/tiny_math.py",),
                commands=(
                    VerifierCommand(
                        name="unittest",
                        argv=("python3", "-B", "-m", "unittest", "discover", "-s", "tests"),
                        timeout_seconds=60,
                    ),
                ),
            ),
        )
        verification_journal.append(verifier_result)
        verifier_result_id = verifier_result.evidence_id
        changed_files = verifier_result.changed_paths
        final_task_state = apply_verification_result(
            store,
            task_id=task_id,
            result=verifier_result,
            evidence_journal=verification_journal,
            shadow_journal=shadow_journal,
            shadow_pending_id=pending_id,
            shadow_reset_cycle_ids=(),
            shadow_quota_after_snapshot_ids=(),
            shadow_observed_burn_fraction=None,
            shadow_attempts_to_green=1,
            shadow_time_to_green_seconds=time_to_green_seconds,
        )
        observation = next(
            item
            for item in shadow_journal.load_all()
            if item.task_id == task_id and item.decision_id == decision.decision_id
        )
        observation_id = observation.observation_id

    campaign_status_before, campaign_status_after = _ensure_campaign_collecting(shadow_journal)
    summary_after = shadow_journal.summarize_campaign()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "selected_real_worker": "codex-cli",
        "provider": PROVIDER_ID,
        "actual_execution_target": ACTUAL_EXECUTION_TARGET,
        "auth_reused_without_secret_read": True,
        "disposable_repo": str(disposable_repo),
        "shadow_runtime_root": str(runtime_root),
        "base_sha": base_sha,
        "branch": disposable_branch,
        "task_id": task_id,
        "run_id": run_id,
        "task_family": TASK_FAMILY,
        "expected_changed_files": ["src/tiny_math.py"],
        "trusted_verifier_profile": "p38-disposable-unittest",
        "routing_request_id": routing_request_id,
        "routing_decision_id": decision.decision_id,
        "catalog_snapshot_id": decision.catalog_snapshot_id,
        "policy_snapshot_id": decision.policy_snapshot_id,
        "quota_snapshot_ids": list(decision.quota_snapshot_ids),
        "would_select_target": decision.selected_execution_target_id,
        "actual_retained_target": ACTUAL_EXECUTION_TARGET,
        "pending_id": pending_id,
        "real_worker_execution": {
            "worker_id": "codex-cli",
            "pid": worker_process.pid,
            "started_at": worker_started_at.isoformat(),
            "finished_at": worker_finished_at.isoformat(),
            "exit_code": worker_exit,
            "stdout_tail": worker_stdout,
            "stderr_tail": safe_worker_stderr,
        },
        "worker_reported_completion_state": "FINISHED" if worker_exit == 0 else "FAILED",
        "verifier_result": {
            "evidence_id": verifier_result_id,
            "task_state": final_task_state.value,
            "changed_files": list(changed_files),
        },
        "first_real_shadow_observation": observation_id,
        "quality_observations_before": quality_before,
        "quality_observations_after": summary_after.quality_observations,
        "campaign_status_before": (
            None if campaign_before is None else campaign_before.status.value
        ),
        "campaign_status_after": campaign_status_after,
        "campaign_status_transition_before": campaign_status_before,
        "quota_confidence": "UNKNOWN",
        "reset_metadata": "UNKNOWN_OR_ABSENT",
        "real_reset_cycles_contributed": 0,
        "real_reset_cycles_total": summary_after.real_reset_cycles_observed,
        "shadow_review_eligible": summary_after.review_eligible,
        "production_active": "DISABLED_BY_DESIGN",
        "owner_approval": "ABSENT",
    }
    report_path = report_root / "p38-first-real-shadow-observation-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report_path)
    print(report["first_real_shadow_observation"])
    print(report["quality_observations_before"])
    print(report["quality_observations_after"])
    print(report["real_reset_cycles_total"])
    print(report["shadow_review_eligible"])
    return 0 if observation_id is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
