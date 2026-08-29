from pathlib import Path

import pytest

from personal_ai_orchestrator.activation_authority import build_active_gate
from personal_ai_orchestrator.approval import ApprovalAuthority, ApprovalKind, ApprovalStatus
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
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
