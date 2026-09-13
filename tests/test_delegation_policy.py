from __future__ import annotations

import pytest

from personal_ai_orchestrator.delegation_policy import (
    PI5B3A_POLICY_VERSION,
    DelegationPolicyInput,
    DelegationPolicyMode,
    DelegationReasonCode,
    DelegationVerdict,
    evaluate_delegation,
)


def _admissible(**overrides):
    values = {
        "delegation_feature_enabled": True,
        "eligible_child_count": 1,
        "quota_truth_required": True,
        "quota_truth_known": True,
        "require_burn_estimate": True,
        "predicted_child_burn_fraction": 0.05,
        "usable_child_headroom_fraction": 0.20,
    }
    values.update(overrides)
    return DelegationPolicyInput(**values)


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (_admissible(delegation_feature_enabled=False), DelegationReasonCode.FEATURE_DISABLED),
        (_admissible(eligible_child_count=0), DelegationReasonCode.NO_ELIGIBLE_CHILD),
        (
            _admissible(quota_truth_known=False),
            DelegationReasonCode.REQUIRED_QUOTA_TRUTH_MISSING,
        ),
        (
            _admissible(predicted_child_burn_fraction=None),
            DelegationReasonCode.REQUIRED_BURN_ESTIMATE_MISSING,
        ),
        (
            _admissible(predicted_child_burn_fraction=0.21),
            DelegationReasonCode.CHILD_HEADROOM_INSUFFICIENT,
        ),
        (
            _admissible(paid_usage_required=True, paid_usage_allowed=False),
            DelegationReasonCode.PAID_USAGE_NOT_ALLOWED,
        ),
        (
            _admissible(
                independence_required=True,
                different_provider_candidate_available=False,
            ),
            DelegationReasonCode.INDEPENDENCE_REQUIREMENT_UNSATISFIED,
        ),
    ],
)
def test_hard_denials_precede_positive_justification(facts, reason):
    facts = facts.model_copy(update={"host_required": True, "failure_count": 99})
    decision = evaluate_delegation(facts, mode=DelegationPolicyMode.ENFORCE)
    assert decision.mode is DelegationPolicyMode.ENFORCE
    assert decision.verdict is DelegationVerdict.DENY
    assert decision.reasons[0] is reason


def test_off_mode_is_distinct_from_disabled_feature():
    decision = evaluate_delegation(
        _admissible(host_required=True),
        mode=DelegationPolicyMode.OFF,
    )
    assert decision.mode is DelegationPolicyMode.OFF
    assert decision.verdict is DelegationVerdict.DENY
    assert decision.reasons[0] is DelegationReasonCode.POLICY_MODE_OFF

    disabled = evaluate_delegation(
        _admissible(delegation_feature_enabled=False),
        mode=DelegationPolicyMode.SHADOW,
    )
    assert disabled.reasons[0] is DelegationReasonCode.FEATURE_DISABLED


def test_host_required_delegation_allows_without_inventing_economics_score():
    decision = evaluate_delegation(
        _admissible(host_required=True, same_quota_pool_as_parent=True),
        mode=DelegationPolicyMode.ENFORCE,
    )
    assert decision.verdict is DelegationVerdict.ALLOW
    assert decision.reasons == (
        DelegationReasonCode.HOST_REQUIRED_DELEGATION,
        DelegationReasonCode.SHARED_QUOTA_POOL,
    )


def test_failure_escalation_uses_existing_threshold_semantics():
    decision = evaluate_delegation(
        _admissible(failure_count=2, failure_escalation_after=2),
        mode=DelegationPolicyMode.ENFORCE,
    )
    assert decision.verdict is DelegationVerdict.ALLOW
    assert decision.reasons == (DelegationReasonCode.FAILURE_ESCALATION_REACHED,)


def test_unjustified_request_remains_shadow_only_after_hard_gates_pass():
    decision = evaluate_delegation(
        _admissible(same_quota_pool_as_parent=False),
        mode=DelegationPolicyMode.ENFORCE,
    )
    assert decision.verdict is DelegationVerdict.SHADOW_ONLY
    assert decision.reasons == (
        DelegationReasonCode.DIFFERENT_QUOTA_POOL,
        DelegationReasonCode.INSUFFICIENT_JUSTIFICATION_FOR_ACTIVE_DELEGATION,
    )


def test_shadow_mode_records_allow_without_changing_the_verdict_contract():
    decision = evaluate_delegation(
        _admissible(host_required=True),
        mode=DelegationPolicyMode.SHADOW,
    )
    assert decision.mode is DelegationPolicyMode.SHADOW
    assert decision.verdict is DelegationVerdict.ALLOW


def test_same_pool_is_explanatory_not_an_uncalibrated_automatic_denial():
    decision = evaluate_delegation(
        _admissible(host_required=True, same_quota_pool_as_parent=True),
        mode=DelegationPolicyMode.SHADOW,
    )
    assert decision.verdict is DelegationVerdict.ALLOW
    assert DelegationReasonCode.SHARED_QUOTA_POOL in decision.reasons


def test_fixed_inputs_replay_to_the_same_versioned_decision():
    facts = _admissible(
        host_required=True,
        same_quota_pool_as_parent=False,
        independence_required=True,
        different_provider_candidate_available=True,
    )
    first = evaluate_delegation(facts, mode=DelegationPolicyMode.SHADOW)
    second = evaluate_delegation(facts, mode=DelegationPolicyMode.SHADOW)
    assert first.policy_version == PI5B3A_POLICY_VERSION
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_policy_is_provider_agnostic_by_construction():
    fields = DelegationPolicyInput.model_fields
    assert "provider_id" not in fields
    assert "model_id" not in fields
    assert "execution_target_id" not in fields


def test_predicted_burn_requires_headroom_when_quota_truth_is_required():
    with pytest.raises(ValueError, match="predicted burn requires usable headroom"):
        DelegationPolicyInput(
            delegation_feature_enabled=True,
            eligible_child_count=1,
            quota_truth_known=True,
            predicted_child_burn_fraction=0.05,
            usable_child_headroom_fraction=None,
        )
