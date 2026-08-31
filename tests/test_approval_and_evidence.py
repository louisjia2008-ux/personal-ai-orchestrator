from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation_authority import build_active_gate
from personal_ai_orchestrator.approval import ApprovalAuthority, ApprovalKind, ApprovalStatus
from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.shadow_evidence import (
    ResetCycleReference,
    ResetCycleSource,
    ShadowEvidenceJournal,
    ShadowObservation,
)
from personal_ai_orchestrator.verification_evidence import (
    RetryPolicy,
    VerificationEvidenceJournal,
    VerificationFailureClass,
    classify_failure,
)
from personal_ai_orchestrator.verifier import VerificationResult, VerificationStage


def _store(tmp_path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="control", request_id="control-request", intent="control-plane")
    return store


def _shadow_observation(
    *,
    task_id: str,
    request_id: str,
    decision_id: str,
    reset_cycle_ids: tuple[str, ...],
) -> ShadowObservation:
    return ShadowObservation.build(
        task_id=task_id,
        request_id=request_id,
        decision_id=decision_id,
        reset_cycle_ids=reset_cycle_ids,
        manual_execution_target_id="m3-sub",
        scheduler_execution_target_id="m3-sub",
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        quota_snapshot_ids=("quota-before",),
        verified=True,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def _reset_ref(reset_cycle_id: str) -> ResetCycleReference:
    return ResetCycleReference(
        reset_cycle_id=reset_cycle_id,
        provider_id="minimax",
        quota_pool_id="minimax-token-plan-cn",
        quota_snapshot_id=f"quota-{reset_cycle_id}",
        reset_at=datetime(2026, 8, 30, tzinfo=UTC),
        confidence=EvidenceConfidence.EXACT,
        source=ResetCycleSource.PROVIDER_EXACT,
        source_method="fixture-provider-reset-metadata",
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def test_owner_approval_is_durable_and_immutable(tmp_path: Path) -> None:
    authority = ApprovalAuthority(_store(tmp_path))
    pending = authority.request(
        approval_id="approve-active-1",
        task_id="control",
        kind=ApprovalKind.PRODUCTION_ACTIVE_ROUTING,
    )
    assert pending.status is ApprovalStatus.PENDING
    approved = authority.resolve("approve-active-1", approved=True)
    assert approved.status is ApprovalStatus.APPROVED
    assert authority.resolve("approve-active-1", approved=True) == approved
    with pytest.raises(ValueError, match="immutable"):
        authority.resolve("approve-active-1", approved=False)


def test_active_gate_requires_persisted_owner_approval(tmp_path: Path) -> None:
    authority = ApprovalAuthority(_store(tmp_path))
    authority.request(
        approval_id="approve-active-1",
        task_id="control",
        kind=ApprovalKind.PRODUCTION_ACTIVE_ROUTING,
    )
    unapproved = build_active_gate(
        approvals=authority,
        owner_approval_id="approve-active-1",
        p0_safety_kernel_authoritative=True,
        p1_verifier_authoritative=True,
        adapter_fail_closed_validated=True,
        shadow_evidence_accepted=True,
        safe_bypass_validated=True,
    )
    assert unapproved.authorized is False

    authority.resolve("approve-active-1", approved=True)
    approved = build_active_gate(
        approvals=authority,
        owner_approval_id="approve-active-1",
        p0_safety_kernel_authoritative=True,
        p1_verifier_authoritative=True,
        adapter_fail_closed_validated=True,
        shadow_evidence_accepted=True,
        safe_bypass_validated=True,
    )
    assert approved.authorized is True


def test_shadow_review_eligibility_cannot_create_active_owner_approval(tmp_path: Path) -> None:
    authority = ApprovalAuthority(_store(tmp_path))
    journal = ShadowEvidenceJournal(tmp_path / "shadow")
    journal.append_reset_cycle(_reset_ref("minimax-week-1"))
    journal.append_reset_cycle(_reset_ref("minimax-week-2"))
    journal.append(
        _shadow_observation(
            task_id="task-1",
            request_id="req-1",
            decision_id="dec-1",
            reset_cycle_ids=("minimax-week-1",),
        )
    )
    journal.append(
        _shadow_observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
        )
    )
    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)
    gate = build_active_gate(
        approvals=authority,
        owner_approval_id="missing-owner-approval",
        p0_safety_kernel_authoritative=True,
        p1_verifier_authoritative=True,
        adapter_fail_closed_validated=True,
        shadow_evidence_accepted=summary.review_eligible,
        safe_bypass_validated=True,
    )

    assert summary.review_eligible is True
    assert gate.authorized is False
    assert authority.is_approved(
        "missing-owner-approval",
        kind=ApprovalKind.PRODUCTION_ACTIVE_ROUTING,
    ) is False


def _result(
    *,
    passed: bool,
    evidence_id: str | None,
    reason: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        profile="fixture",
        passed=passed,
        changed_paths=("src/value.txt",),
        unexpected_paths=(),
        stages=(
            VerificationStage(
                name="test",
                argv=("python", "-m", "pytest"),
                returncode=0 if passed else 1,
            ),
        ),
        evidence_id=evidence_id,
        failure_reason=reason,
    )


def test_verification_evidence_journal_is_append_only(tmp_path: Path) -> None:
    result = _result(passed=True, evidence_id="verify-1")
    journal = VerificationEvidenceJournal(tmp_path)
    first = journal.append(result)
    second = journal.append(result)
    assert first == second
    assert journal.load("verify-1") == result
    with pytest.raises(ValueError, match="no immutable evidence_id"):
        journal.append(_result(passed=True, evidence_id=None))


def test_retry_policy_never_retries_engineering_failure_by_default() -> None:
    result = _result(
        passed=False,
        evidence_id="verify-fail",
        reason="verifier command failed: tests",
    )
    assert classify_failure(result) is VerificationFailureClass.COMMAND_FAILURE
    policy = RetryPolicy(max_known_flaky_infra_retries=1)
    assert policy.should_retry(
        failure_class=VerificationFailureClass.COMMAND_FAILURE,
        previous_retries=0,
        host_attested_known_flaky_infra=True,
    ) is False
    assert policy.should_retry(
        failure_class=VerificationFailureClass.KNOWN_FLAKY_INFRA,
        previous_retries=0,
        host_attested_known_flaky_infra=False,
    ) is False
    assert policy.should_retry(
        failure_class=VerificationFailureClass.KNOWN_FLAKY_INFRA,
        previous_retries=0,
        host_attested_known_flaky_infra=True,
    ) is True
    assert policy.should_retry(
        failure_class=VerificationFailureClass.KNOWN_FLAKY_INFRA,
        previous_retries=1,
        host_attested_known_flaky_infra=True,
    ) is False
