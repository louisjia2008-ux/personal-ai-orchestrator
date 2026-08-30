"""Reusable real Shadow campaign runner for disposable task observations."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import uuid4

from pydantic import Field

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
    RegistryModel,
)
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.policy_snapshot import PolicySnapshotJournal
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import TaskProfile
from personal_ai_orchestrator.shadow_evidence import (
    ShadowAcceptancePolicy,
    ShadowCampaignState,
    ShadowCampaignStatus,
    ShadowEvidenceJournal,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import DeterministicVerifier, VerifierProfile


class CampaignFile(RegistryModel):
    path: str = Field(min_length=1)
    content: str


class DeclarativeShadowCase(RegistryModel):
    case_id: str = Field(min_length=1)
    task_family: str = Field(min_length=1)
    difficulty_class: str = Field(min_length=1)
    required_capabilities: dict[str, float] = Field(default_factory=dict)
    fixture_files: tuple[CampaignFile, ...] = Field(min_length=1)
    expected_changed_paths: tuple[str, ...] = Field(min_length=1)
    verifier_profile: VerifierProfile
    worker_prompt: str = Field(min_length=1)
    task_intent: str = Field(min_length=1)
    predicted_quota_fraction_p90: float | None = Field(default=0.01, ge=0.0, le=1.0)


class CampaignWorkerProfile(RegistryModel):
    worker_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_display_name: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    model_display_name: str = Field(min_length=1)
    execution_target_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    catalog_snapshot_id: str = Field(min_length=1)
    catalog_source: str = Field(min_length=1)
    quota_snapshot_id: str = Field(min_length=1)
    quota_source_reference: str = Field(min_length=1)
    quota_source_note: str = Field(min_length=1)
    quota_confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN


class WorkerExecutionResult(RegistryModel):
    worker_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    execution_target_id: str = Field(min_length=1)
    pid: int | None = None
    started_at: datetime
    finished_at: datetime
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def reported_state(self) -> str:
        return "FINISHED" if self.exit_code == 0 else "FAILED"


class ShadowCaseResult(RegistryModel):
    case_id: str
    task_family: str
    difficulty_class: str
    disposable_repo: str
    base_sha: str
    disposable_branch: str
    task_id: str
    run_id: str
    routing_request_id: str
    routing_decision_id: str
    pending_id: str
    catalog_snapshot_id: str
    policy_snapshot_id: str
    quota_snapshot_ids: tuple[str, ...]
    would_select_target: str | None
    scheduler_decision_reason: str | None
    actual_retained_target: str
    worker: WorkerExecutionResult
    verifier_evidence_id: str | None = None
    final_task_state: str
    verified: bool
    changed_paths: tuple[str, ...] = ()
    unexpected_paths: tuple[str, ...] = ()
    failure_reason: str | None = None
    observation_id: str | None = None
    quality_observations_after: int
    real_reset_cycles_total: int
    shadow_review_eligible: bool
    time_to_green_seconds: float | None = None


class WorkerHandle(Protocol):
    pid: int | None

    def wait(self) -> WorkerExecutionResult: ...


WorkerLauncher = Callable[[Path, DeclarativeShadowCase, CampaignWorkerProfile], WorkerHandle]


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _safe_child_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved != candidate and root_resolved not in candidate.parents:
        raise ValueError(f"fixture path escapes disposable repository: {relative}")
    return candidate


def create_disposable_repo(
    case: DeclarativeShadowCase,
    *,
    prefix: str = "pao-p39-shadow-",
) -> tuple[Path, str, str]:
    root = Path(tempfile.mkdtemp(prefix=f"{prefix}{case.case_id}-"))
    for item in case.fixture_files:
        target = _safe_child_path(root, item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.content, encoding="utf-8")
    _run(["git", "init"], cwd=root)
    _run(["git", "config", "user.name", "P3.9 Shadow Campaign"], cwd=root)
    _run(["git", "config", "user.email", "p39-shadow@example.invalid"], cwd=root)
    _run(["git", "add", "."], cwd=root)
    _run(["git", "commit", "-m", f"initial fixture for {case.case_id}"], cwd=root)
    base_sha = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    branch = _run(["git", "branch", "--show-current"], cwd=root).stdout.strip()
    return root, base_sha, branch


def worker_registry(profile: CampaignWorkerProfile, *, now: datetime) -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=now,
        reference=profile.quota_source_reference,
        note=profile.quota_source_note,
        confidence=profile.quota_confidence,
    )
    snapshot = QuotaSnapshot(
        id=profile.quota_snapshot_id,
        quota_pool_id=profile.quota_pool_id,
        provider_id=profile.provider_id,
        account_id=profile.account_id,
        plan_id=profile.plan_id,
        observed_at=now,
        state=QuotaState.UNKNOWN,
        confidence=profile.quota_confidence,
        source=source,
    )
    return ModelRegistry(
        providers={
            profile.provider_id: Provider(
                id=profile.provider_id,
                display_name=profile.provider_display_name,
            )
        },
        accounts={
            profile.account_id: Account(
                id=profile.account_id,
                provider_id=profile.provider_id,
                label=profile.plan_name,
            )
        },
        plans={
            profile.plan_id: Plan(
                id=profile.plan_id,
                account_id=profile.account_id,
                name=profile.plan_name,
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        catalog_snapshots={
            profile.catalog_snapshot_id: ModelCatalogSnapshot(
                id=profile.catalog_snapshot_id,
                source=profile.catalog_source,
                as_of=now,
                fetched_at=now,
                content_hash=profile.execution_target_id,
            )
        },
        quota_pools={
            profile.quota_pool_id: QuotaPool(
                id=profile.quota_pool_id,
                plan_id=profile.plan_id,
                name=f"{profile.plan_name} quota unknown",
                snapshot=snapshot,
            )
        },
        models={
            profile.model_sku_id: ModelSKU(
                id=profile.model_sku_id,
                provider_id=profile.provider_id,
                display_name=profile.model_display_name,
                catalog_snapshot_id=profile.catalog_snapshot_id,
                capabilities=CapabilityProfile(scores={"implementation": 0.95, "testing": 0.9}),
            )
        },
        execution_targets={
            profile.execution_target_id: ExecutionTarget(
                id=profile.execution_target_id,
                model_sku_id=profile.model_sku_id,
                account_id=profile.account_id,
                runtime_id=profile.runtime_id,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id=f"binding-{profile.execution_target_id}",
                model_sku_id=profile.model_sku_id,
                execution_target_id=profile.execution_target_id,
                quota_pool_id=profile.quota_pool_id,
                effective_from=now - timedelta(days=1),
                recorded_at=now,
                confidence=profile.quota_confidence,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id=profile.model_sku_id,
                execution_target_id=profile.execution_target_id,
            ),
        ),
    )


def _ensure_campaign_state(
    journal: ShadowEvidenceJournal,
    *,
    campaign_id: str,
    head: str,
    decision_catalog_snapshot_id: str,
    decision_policy_snapshot_id: str,
    provider_id: str,
) -> None:
    state = journal.load_campaign_state()
    if state is None:
        journal.save_campaign_state(
            ShadowCampaignState(
                campaign_id=campaign_id,
                status=ShadowCampaignStatus.BOOTSTRAPPED,
                started_at=datetime.now(UTC),
                head=head,
                catalog_snapshot_id=decision_catalog_snapshot_id,
                policy_snapshot_id=decision_policy_snapshot_id,
                providers_unknown=(provider_id,),
                acceptance_policy=ShadowAcceptancePolicy(),
            )
        )


def _mark_collecting_after_observation(journal: ShadowEvidenceJournal) -> None:
    state = journal.load_campaign_state()
    if state is not None and state.status is ShadowCampaignStatus.BOOTSTRAPPED:
        journal.save_campaign_state(
            state.model_copy(update={"status": ShadowCampaignStatus.COLLECTING})
        )


def run_shadow_case(
    *,
    campaign_root: Path,
    campaign_id: str,
    case: DeclarativeShadowCase,
    worker_profile: CampaignWorkerProfile,
    worker_launcher: WorkerLauncher,
    repo_head: str,
) -> ShadowCaseResult:
    now = datetime.now(UTC)
    campaign_root.mkdir(parents=True, exist_ok=True)
    shadow_journal = ShadowEvidenceJournal(campaign_root / "shadow-runtime")
    store = SafetyKernelStore(campaign_root / "p39-safety.sqlite3")
    verification_journal = VerificationEvidenceJournal(campaign_root)
    policy_journal = PolicySnapshotJournal(campaign_root)

    disposable_repo, base_sha, disposable_branch = create_disposable_repo(case)
    task_id = f"p39-{case.case_id}-{uuid4().hex[:12]}"
    run_id = f"run-{uuid4().hex[:12]}"
    routing_request_id = f"route-{uuid4().hex[:12]}"
    writer_token = f"writer-{uuid4().hex[:12]}"

    service = RoutingService(
        registry=worker_registry(worker_profile, now=now),
        store=store,
        catalog_snapshot_id=worker_profile.catalog_snapshot_id,
        policy_journal=policy_journal,
        runtime_availability={worker_profile.execution_target_id: True},
        shadow_journal=shadow_journal,
        shadow_actual_execution_targets={task_id: worker_profile.execution_target_id},
    )
    service.set_task_profile(
        TaskProfile(
            task_id=task_id,
            pool=PoolKind.WORKER,
            task_family=case.task_family,
            required_capabilities=case.required_capabilities,
            predicted_quota_fraction_p90=case.predicted_quota_fraction_p90,
        )
    )
    store.submit_task(task_id=task_id, request_id=f"submit-{task_id}", intent=case.task_intent)
    store.register_workspace(
        task_id=task_id,
        repo_path=str(disposable_repo),
        worktree_path=str(disposable_repo),
        branch=disposable_branch,
        base_sha=base_sha,
    )
    store.acquire_writer(task_id, writer_token)
    ready = store.transition_task(task_id, TaskState.READY, reason="P3.9 disposable task ready")

    routing_request = RoutingRequest(
        request_id=routing_request_id,
        session_id=f"p39-{task_id}",
        project_id="p39-disposable",
        location=str(disposable_repo),
        agent=worker_profile.worker_id,
        mode=RoutingMode.SHADOW,
        task_id=task_id,
        task_state_version=ready.state_version,
        requested_at=now,
    )
    decision = service.route(routing_request, now=now)
    pending_id = f"pending-{decision.decision_id}"
    _ensure_campaign_state(
        shadow_journal,
        campaign_id=campaign_id,
        head=repo_head,
        decision_catalog_snapshot_id=decision.catalog_snapshot_id,
        decision_policy_snapshot_id=decision.policy_snapshot_id,
        provider_id=worker_profile.provider_id,
    )

    store.transition_task(task_id, TaskState.RUNNING, expected_version=ready.state_version)
    started = monotonic()
    worker_handle = worker_launcher(disposable_repo, case, worker_profile)
    start_worker_run(
        store,
        task_id=task_id,
        run_id=run_id,
        worker_id=worker_profile.worker_id,
        writer_token=writer_token,
        pid=worker_handle.pid,
    )
    worker_result = worker_handle.wait()
    next_state = record_worker_exit(
        store,
        task_id=task_id,
        run_id=run_id,
        exit_code=worker_result.exit_code,
        worker_result={
            "status": worker_result.reported_state,
            "provider": worker_profile.provider_id,
            "execution_target_id": worker_profile.execution_target_id,
            "stdout_tail": worker_result.stdout_tail,
            "stderr_tail": worker_result.stderr_tail,
        },
    )

    verifier_evidence_id = None
    final_state = next_state
    verified = False
    changed_paths: tuple[str, ...] = ()
    unexpected_paths: tuple[str, ...] = ()
    failure_reason: str | None = None
    observation_id: str | None = None
    time_to_green_seconds = None

    if next_state is TaskState.WORKER_FINISHED:
        begin_verification(store, task_id=task_id)
        verifier_result = DeterministicVerifier().verify(
            disposable_repo,
            base_sha=base_sha,
            profile=case.verifier_profile,
        )
        verification_journal.append(verifier_result)
        verifier_evidence_id = verifier_result.evidence_id
        changed_paths = verifier_result.changed_paths
        unexpected_paths = verifier_result.unexpected_paths
        failure_reason = verifier_result.failure_reason
        time_to_green_seconds = monotonic() - started
        final_state = apply_verification_result(
            store,
            task_id=task_id,
            result=verifier_result,
            evidence_journal=verification_journal,
            shadow_journal=shadow_journal,
            shadow_pending_id=pending_id,
            shadow_reset_cycle_ids=(),
            shadow_quota_after_snapshot_ids=(),
            shadow_observed_burn_fraction=None,
            shadow_attempts_to_green=1 if verifier_result.passed else None,
            shadow_time_to_green_seconds=time_to_green_seconds if verifier_result.passed else None,
        )
        verified = final_state is TaskState.VERIFIED
    else:
        failure_reason = f"worker exited with code {worker_result.exit_code}"
        shadow_journal.finalize_pending(
            pending_id,
            verified=False,
            observed_at=datetime.now(UTC),
        )

    observations = shadow_journal.load_all()
    matching = [
        item
        for item in observations
        if item.task_id == task_id and item.decision_id == decision.decision_id
    ]
    if matching:
        observation_id = matching[0].observation_id
    _mark_collecting_after_observation(shadow_journal)
    summary = shadow_journal.summarize_campaign()

    return ShadowCaseResult(
        case_id=case.case_id,
        task_family=case.task_family,
        difficulty_class=case.difficulty_class,
        disposable_repo=str(disposable_repo),
        base_sha=base_sha,
        disposable_branch=disposable_branch,
        task_id=task_id,
        run_id=run_id,
        routing_request_id=routing_request_id,
        routing_decision_id=decision.decision_id,
        pending_id=pending_id,
        catalog_snapshot_id=decision.catalog_snapshot_id,
        policy_snapshot_id=decision.policy_snapshot_id,
        quota_snapshot_ids=decision.quota_snapshot_ids,
        would_select_target=decision.selected_execution_target_id,
        scheduler_decision_reason=decision.fallback_reason,
        actual_retained_target=worker_profile.execution_target_id,
        worker=worker_result,
        verifier_evidence_id=verifier_evidence_id,
        final_task_state=final_state.value,
        verified=verified,
        changed_paths=changed_paths,
        unexpected_paths=unexpected_paths,
        failure_reason=failure_reason,
        observation_id=observation_id,
        quality_observations_after=summary.quality_observations,
        real_reset_cycles_total=summary.real_reset_cycles_observed,
        shadow_review_eligible=summary.review_eligible,
        time_to_green_seconds=time_to_green_seconds if verified else None,
    )


def write_campaign_report(
    *,
    campaign_root: Path,
    report_name: str,
    results: Sequence[ShadowCaseResult],
    provider_probe_results: dict[str, object],
) -> Path:
    journal = ShadowEvidenceJournal(campaign_root / "shadow-runtime")
    summary = journal.summarize_campaign()
    observations = journal.load_all()
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign_root": str(campaign_root),
        "provider_probe_results": provider_probe_results,
        "cases": [result.model_dump(mode="json") for result in results],
        "readiness": {
            "total_real_quality_observations": summary.quality_observations,
            "total_real_providers": len(
                {item.provider_id for item in observations if item.provider_id}
            ),
            "total_real_execution_targets": len(
                {item.manual_execution_target_id for item in observations}
            ),
            "total_task_families": len({item.task_family for item in observations}),
            "verified_count": summary.quality_observations
            - sum(not item.verified for item in observations),
            "failed_or_blocked_count": sum(not item.verified for item in observations),
            "real_reset_cycles": summary.real_reset_cycles_observed,
            "shadow_review_eligible": summary.review_eligible,
            "production_active": "DISABLED_BY_DESIGN",
            "owner_approval": "ABSENT",
        },
        "cohorts": [group.model_dump(mode="json") for group in summary.groups],
        "blocking_reasons": summary.blocking_reasons,
    }
    report_path = campaign_root / report_name
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


__all__ = [
    "CampaignFile",
    "CampaignWorkerProfile",
    "DeclarativeShadowCase",
    "ShadowCaseResult",
    "WorkerExecutionResult",
    "create_disposable_repo",
    "run_shadow_case",
    "worker_registry",
    "write_campaign_report",
]
