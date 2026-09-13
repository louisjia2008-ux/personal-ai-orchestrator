from __future__ import annotations

from datetime import UTC, datetime

from personal_ai_orchestrator.delegation_evidence import (
    DelegationCommercialMode,
    DelegationHostEvidence,
    DelegationOutcomeJournal,
    DelegationOutcomePhase,
    DelegationOutcomeRecord,
)
from personal_ai_orchestrator.delegation_policy import (
    DelegationDecision,
    DelegationPolicyInput,
    DelegationPolicyMode,
    DelegationReasonCode,
    DelegationVerdict,
    evaluate_delegation,
)
from personal_ai_orchestrator.delegation_readiness import (
    DelegationReadinessAdvisory,
    DelegationReadinessPolicy,
    DelegationReadinessReason,
    DelegationReadinessStatus,
    evaluate_delegation_readiness,
    load_readiness_evidence,
)
from personal_ai_orchestrator.delegation_shadow import (
    DelegationShadowJournal,
    DelegationShadowRecord,
)

NOW = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)


def _shadow(
    suffix: str,
    *,
    same_pool: bool | None = True,
    enforcement_ready: bool = True,
    replay_match: bool = True,
) -> DelegationShadowRecord:
    parent_pool = "pool-a"
    child_pool = (
        "pool-a" if same_pool is True else "pool-b" if same_pool is False else None
    )
    facts = DelegationPolicyInput(
        delegation_feature_enabled=True,
        host_required=True,
        eligible_child_count=1,
        quota_truth_required=True,
        quota_truth_known=True,
        require_burn_estimate=True,
        predicted_child_burn_fraction=0.05,
        usable_child_headroom_fraction=0.50,
        same_quota_pool_as_parent=same_pool,
    )
    decision = evaluate_delegation(facts, mode=DelegationPolicyMode.SHADOW)
    if not replay_match:
        decision = DelegationDecision(
            mode=DelegationPolicyMode.SHADOW,
            verdict=DelegationVerdict.DENY,
            reasons=(DelegationReasonCode.FEATURE_DISABLED,),
        )
    host_evidence = DelegationHostEvidence(
        parent_profile_present=True,
        parent_failure_count=0,
        child_profile_present=True,
        child_predicted_quota_fraction_p90=0.05,
        predicted_child_burn_fraction=0.05,
        host_required_delegation=True,
        independence_required=False,
        child_commercial_mode=DelegationCommercialMode.SUBSCRIPTION,
        paid_usage_required=False,
        parent_quota_pool_id=parent_pool,
        child_quota_pool_id=child_pool,
        same_quota_pool_as_parent=same_pool,
        raw_child_headroom_fraction=0.60,
        usable_child_headroom_fraction=0.50,
        enforcement_ready=enforcement_ready,
        limitations=() if enforcement_ready else ("synthetic_missing_signal",),
    )
    return DelegationShadowRecord(
        observation_id=f"obs-{suffix}",
        observed_at=NOW,
        parent_task_id=f"parent-{suffix}",
        parent_run_id=f"parent-run-{suffix}",
        child_task_id=f"child-{suffix}",
        parent_execution_target_id="target-parent",
        selected_child_execution_target_id="target-child",
        parent_quota_pool_id=parent_pool,
        child_quota_pool_id=child_pool,
        host_evidence=host_evidence,
        enforcement_ready=enforcement_ready,
        limitations=() if enforcement_ready else ("synthetic_missing_signal",),
        facts=facts,
        decision=decision,
    )


def _outcomes(
    shadow: DelegationShadowRecord,
    *,
    quota: bool = True,
    child_verified: bool = True,
    parent_verified: bool = True,
    target: str | None = "target-child",
) -> tuple[DelegationOutcomeRecord, DelegationOutcomeRecord]:
    before = f"quota-before-{shadow.observation_id}" if quota else None
    after = f"quota-after-{shadow.observation_id}" if quota else None
    child = DelegationOutcomeRecord(
        outcome_id=DelegationOutcomeJournal.outcome_id(
            observation_id=shadow.observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        ),
        observation_id=shadow.observation_id,
        phase=DelegationOutcomePhase.CHILD_FINAL,
        observed_at=NOW,
        parent_task_id=shadow.parent_task_id,
        parent_run_id=shadow.parent_run_id,
        child_task_id=shadow.child_task_id,
        child_execution_target_id=target,
        child_quota_pool_id=shadow.child_quota_pool_id,
        child_state="VERIFIED" if child_verified else "BLOCKED",
        child_verified=child_verified,
        parent_state="RUNNING",
        parent_verified=False,
        child_verifier_passed=True if child_verified else None,
        quota_before_snapshot_id=before,
        quota_after_snapshot_id=after,
    )
    parent = DelegationOutcomeRecord(
        outcome_id=DelegationOutcomeJournal.outcome_id(
            observation_id=shadow.observation_id,
            phase=DelegationOutcomePhase.PARENT_FINAL,
        ),
        observation_id=shadow.observation_id,
        phase=DelegationOutcomePhase.PARENT_FINAL,
        observed_at=NOW,
        parent_task_id=shadow.parent_task_id,
        parent_run_id=shadow.parent_run_id,
        child_task_id=shadow.child_task_id,
        child_execution_target_id=target,
        child_quota_pool_id=shadow.child_quota_pool_id,
        child_state="VERIFIED" if child_verified else "BLOCKED",
        child_verified=child_verified,
        parent_state="VERIFIED" if parent_verified else "BLOCKED",
        parent_verified=parent_verified,
        child_verifier_passed=True if child_verified else None,
        parent_verifier_passed=True if parent_verified else None,
        quota_before_snapshot_id=before,
        quota_after_snapshot_id=after,
    )
    return child, parent


def test_no_policy_and_no_evidence_fail_closed():
    report = evaluate_delegation_readiness((), (), policy=None)
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert report.blocking_reasons == (
        DelegationReadinessReason.POLICY_REQUIRED,
        DelegationReadinessReason.NO_SHADOW_EVIDENCE,
    )
    assert report.total_shadow_observations == 0


def test_explicit_policy_can_produce_ready_for_fully_complete_sample():
    shadow = _shadow("ready")
    outcomes = _outcomes(shadow)
    policy = DelegationReadinessPolicy(min_complete_samples=1)
    report = evaluate_delegation_readiness((shadow,), outcomes, policy=policy)
    assert report.status is DelegationReadinessStatus.READY_FOR_LIMITED_EXPERIMENT
    assert report.blocking_reasons == ()
    assert report.complete_sample_count == 1
    assert report.child_verified_count == 1
    assert report.parent_verified_count == 1
    assert report.quota_comparable_count == 1


def test_replay_mismatch_and_shadow_not_ready_block():
    replay_bad = _shadow("replay", replay_match=False)
    limited = _shadow("limited", enforcement_ready=False)
    outcomes = (*_outcomes(replay_bad), *_outcomes(limited))
    report = evaluate_delegation_readiness(
        (replay_bad, limited),
        outcomes,
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.SHADOW_REPLAY_MISMATCH in report.blocking_reasons
    assert DelegationReadinessReason.SHADOW_NOT_ENFORCEMENT_READY in report.blocking_reasons
    assert report.complete_sample_count == 0


def test_missing_outcomes_and_verifier_regressions_block():
    missing = _shadow("missing")
    child_bad = _shadow("child-bad")
    parent_bad = _shadow("parent-bad")
    child_bad_outcomes = _outcomes(child_bad, child_verified=False)
    parent_bad_outcomes = _outcomes(parent_bad, parent_verified=False)
    report = evaluate_delegation_readiness(
        (missing, child_bad, parent_bad),
        (*child_bad_outcomes, *parent_bad_outcomes),
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.MISSING_CHILD_OUTCOME in report.blocking_reasons
    assert DelegationReadinessReason.MISSING_PARENT_OUTCOME in report.blocking_reasons
    assert DelegationReadinessReason.CHILD_NOT_VERIFIED in report.blocking_reasons
    assert DelegationReadinessReason.CHILD_VERIFIER_NOT_PASSED in report.blocking_reasons
    assert DelegationReadinessReason.PARENT_NOT_VERIFIED in report.blocking_reasons
    assert DelegationReadinessReason.PARENT_VERIFIER_NOT_PASSED in report.blocking_reasons


def test_missing_quota_comparability_blocks_only_when_policy_requires_it():
    shadow = _shadow("quota")
    outcomes = _outcomes(shadow, quota=False)
    strict = evaluate_delegation_readiness(
        (shadow,),
        outcomes,
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert strict.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.QUOTA_COMPARABILITY_MISSING in strict.blocking_reasons

    descriptive = evaluate_delegation_readiness(
        (shadow,),
        outcomes,
        policy=DelegationReadinessPolicy(
            min_complete_samples=1,
            require_quota_comparability=False,
        ),
    )
    assert descriptive.status is DelegationReadinessStatus.READY_FOR_LIMITED_EXPERIMENT
    assert DelegationReadinessAdvisory.NO_QUOTA_COMPARABLE_SAMPLES in descriptive.advisories


def test_identity_mismatch_fails_closed():
    shadow = _shadow("identity")
    outcomes = _outcomes(shadow, target="other-target")
    report = evaluate_delegation_readiness(
        (shadow,),
        outcomes,
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.OUTCOME_IDENTITY_MISMATCH in report.blocking_reasons


def test_pool_coverage_accounting_and_optional_requirements():
    same = _shadow("same", same_pool=True)
    different = _shadow("different", same_pool=False)
    unknown = _shadow("unknown", same_pool=None)
    outcomes = (*_outcomes(same), *_outcomes(different), *_outcomes(unknown))
    report = evaluate_delegation_readiness(
        (same, different, unknown),
        outcomes,
        policy=DelegationReadinessPolicy(
            min_complete_samples=3,
            require_same_pool_sample=True,
            require_different_pool_sample=True,
        ),
    )
    assert report.status is DelegationReadinessStatus.READY_FOR_LIMITED_EXPERIMENT
    assert report.same_pool_count == 1
    assert report.different_pool_count == 1
    assert report.unknown_pool_count == 1
    assert DelegationReadinessAdvisory.UNKNOWN_POOL_RELATION_OBSERVED in report.advisories

    missing_different = evaluate_delegation_readiness(
        (same,),
        _outcomes(same),
        policy=DelegationReadinessPolicy(
            min_complete_samples=1,
            require_different_pool_sample=True,
        ),
    )
    assert missing_different.status is DelegationReadinessStatus.NOT_READY
    assert (
        DelegationReadinessReason.DIFFERENT_POOL_COVERAGE_MISSING
        in missing_different.blocking_reasons
    )


def test_minimum_complete_sample_count_is_explicit_and_enforced():
    shadow = _shadow("sample")
    report = evaluate_delegation_readiness(
        (shadow,),
        _outcomes(shadow),
        policy=DelegationReadinessPolicy(min_complete_samples=2),
    )
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.INSUFFICIENT_COMPLETE_SAMPLES in report.blocking_reasons


def test_input_order_does_not_change_digest_or_report():
    first = _shadow("a", same_pool=True)
    second = _shadow("b", same_pool=False)
    outcomes = (*_outcomes(first), *_outcomes(second))
    policy = DelegationReadinessPolicy(min_complete_samples=2)
    forward = evaluate_delegation_readiness((first, second), outcomes, policy=policy)
    reverse = evaluate_delegation_readiness(
        (second, first),
        tuple(reversed(outcomes)),
        policy=policy,
    )
    assert forward.input_digest == reverse.input_digest
    assert forward == reverse


def test_duplicate_outcome_identity_fails_closed():
    shadow = _shadow("duplicate")
    child, parent = _outcomes(shadow)
    report = evaluate_delegation_readiness(
        (shadow,),
        (child, child.model_copy(), parent),
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert report.status is DelegationReadinessStatus.NOT_READY
    assert DelegationReadinessReason.OUTCOME_IDENTITY_MISMATCH in report.blocking_reasons


def test_read_only_loader_does_not_modify_existing_evidence(tmp_path):
    shadow = _shadow("disk")
    child, parent = _outcomes(shadow)
    shadow_journal = DelegationShadowJournal(tmp_path)
    outcome_journal = DelegationOutcomeJournal(tmp_path)
    shadow_path = shadow_journal.append(shadow)
    child_path = outcome_journal.append(child)
    parent_path = outcome_journal.append(parent)
    before = {path: path.read_bytes() for path in (shadow_path, child_path, parent_path)}

    shadows, outcomes = load_readiness_evidence(tmp_path)
    report = evaluate_delegation_readiness(
        shadows,
        outcomes,
        policy=DelegationReadinessPolicy(min_complete_samples=1),
    )
    assert report.status is DelegationReadinessStatus.READY_FOR_LIMITED_EXPERIMENT
    assert {path: path.read_bytes() for path in before} == before
