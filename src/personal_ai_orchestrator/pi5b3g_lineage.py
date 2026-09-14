"""Fail-closed PI-5B3G child ownership checks over canonical task lineage."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import Field

from personal_ai_orchestrator.model_registry import RegistryModel
from personal_ai_orchestrator.pi5_identity import (
    CampaignExecutionIdentityFactory,
    CampaignObservationExecutionIdentities,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.safety_kernel import (
    DelegatedTaskLineageError,
    SafetyKernelStore,
)

PI5B3G_LINEAGE_VALIDATOR_VERSION = "pi5b3g-child-lineage-v1"
PI5B3G_LINEAGE_STAGE = "CANONICAL_TASK_SUBMITTED"


class Pi5b3gLineageRule(StrEnum):
    ALLOW = "ALLOW_CANONICAL_CHILD_LINEAGE"
    CAMPAIGN_CONTEXT_INVALID = "LINEAGE_CAMPAIGN_CONTEXT_INVALID"
    CHILD_IDENTITY_MISMATCH = "LINEAGE_CHILD_IDENTITY_MISMATCH"
    CROSS_CAMPAIGN_PARENT = "LINEAGE_CROSS_CAMPAIGN_PARENT"
    WRONG_OBSERVATION_PARENT = "LINEAGE_WRONG_OBSERVATION_PARENT"
    PARENT_IDENTITY_MISMATCH = "LINEAGE_PARENT_IDENTITY_MISMATCH"
    PARENT_RUN_MISMATCH = "LINEAGE_PARENT_RUN_MISMATCH"


class Pi5b3gLineageValidationResult(RegistryModel):
    validator_version: str = PI5B3G_LINEAGE_VALIDATOR_VERSION
    lineage_stage: str = PI5B3G_LINEAGE_STAGE
    child_task_id: str = Field(min_length=1)
    expected_parent_task_id: str = Field(min_length=1)
    resolved_parent_task_id: str | None = None
    resolution_result: str
    rule_id: str
    failure_category: str
    parent_found: bool
    campaign_match: bool
    observation_match: bool
    allowed: bool


def _is_canonical_identity(identity: CampaignObservationExecutionIdentities) -> bool:
    try:
        rebuilt = CampaignExecutionIdentityFactory(identity.campaign_id).observation(
            identity.observation_number
        )
    except ValueError:
        return False
    return rebuilt == identity


def _result(
    *,
    child_task_id: str,
    expected_parent_task_id: str,
    resolved_parent_task_id: str | None,
    rule_id: str,
    failure_category: str,
    parent_found: bool,
    campaign_match: bool,
    observation_match: bool,
    allowed: bool = False,
) -> Pi5b3gLineageValidationResult:
    result = Pi5b3gLineageValidationResult(
        child_task_id=child_task_id,
        expected_parent_task_id=expected_parent_task_id,
        resolved_parent_task_id=resolved_parent_task_id,
        resolution_result="PASS" if allowed else "FAIL",
        rule_id=rule_id,
        failure_category=failure_category,
        parent_found=parent_found,
        campaign_match=campaign_match,
        observation_match=observation_match,
        allowed=allowed,
    )
    assert_sanitized(result.model_dump(mode="json"))
    return result


def validate_pi5b3g_child_lineage(
    *,
    store: SafetyKernelStore,
    child_task_id: str,
    expected: CampaignObservationExecutionIdentities,
    current_campaign_parents: Mapping[
        str, CampaignObservationExecutionIdentities
    ],
) -> Pi5b3gLineageValidationResult:
    """Validate one child against host-owned current-campaign identities.

    Campaign and observation truth comes from identity objects created by the
    host when it starts the campaign. No task ID, filename, or prompt parsing is
    used to infer membership.
    """

    registered_expected = current_campaign_parents.get(expected.parent_task_id)
    if (
        not _is_canonical_identity(expected)
        or registered_expected != expected
        or any(
            key != identity.parent_task_id
            or identity.campaign_id != expected.campaign_id
            or not _is_canonical_identity(identity)
            for key, identity in current_campaign_parents.items()
        )
    ):
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=None,
            rule_id=Pi5b3gLineageRule.CAMPAIGN_CONTEXT_INVALID.value,
            failure_category="CAMPAIGN_CONTEXT",
            parent_found=False,
            campaign_match=False,
            observation_match=False,
        )

    try:
        lineage = store.resolve_delegated_task_lineage(child_task_id)
    except DelegatedTaskLineageError as error:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=None,
            rule_id=error.rule_id.value,
            failure_category=error.failure_category,
            parent_found=error.parent_found,
            campaign_match=False,
            observation_match=False,
        )

    campaign_identity = current_campaign_parents.get(lineage.parent_task_id)
    if campaign_identity is None:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=lineage.parent_task_id,
            rule_id=Pi5b3gLineageRule.CROSS_CAMPAIGN_PARENT.value,
            failure_category="CAMPAIGN",
            parent_found=True,
            campaign_match=False,
            observation_match=False,
        )
    if campaign_identity.observation_number != expected.observation_number:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=lineage.parent_task_id,
            rule_id=Pi5b3gLineageRule.WRONG_OBSERVATION_PARENT.value,
            failure_category="OBSERVATION",
            parent_found=True,
            campaign_match=True,
            observation_match=False,
        )
    if lineage.parent_task_id != expected.parent_task_id:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=lineage.parent_task_id,
            rule_id=Pi5b3gLineageRule.PARENT_IDENTITY_MISMATCH.value,
            failure_category="PARENT_IDENTITY",
            parent_found=True,
            campaign_match=True,
            observation_match=True,
        )
    if lineage.parent_run_id != expected.parent_run_id:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=lineage.parent_task_id,
            rule_id=Pi5b3gLineageRule.PARENT_RUN_MISMATCH.value,
            failure_category="PARENT_RUN",
            parent_found=True,
            campaign_match=True,
            observation_match=True,
        )
    if child_task_id != expected.child(ordinal=1).task_id:
        return _result(
            child_task_id=child_task_id,
            expected_parent_task_id=expected.parent_task_id,
            resolved_parent_task_id=lineage.parent_task_id,
            rule_id=Pi5b3gLineageRule.CHILD_IDENTITY_MISMATCH.value,
            failure_category="CHILD_IDENTITY",
            parent_found=True,
            campaign_match=True,
            observation_match=True,
        )
    return _result(
        child_task_id=child_task_id,
        expected_parent_task_id=expected.parent_task_id,
        resolved_parent_task_id=lineage.parent_task_id,
        rule_id=Pi5b3gLineageRule.ALLOW.value,
        failure_category="NONE",
        parent_found=True,
        campaign_match=True,
        observation_match=True,
        allowed=True,
    )


__all__ = [
    "PI5B3G_LINEAGE_STAGE",
    "PI5B3G_LINEAGE_VALIDATOR_VERSION",
    "Pi5b3gLineageRule",
    "Pi5b3gLineageValidationResult",
    "validate_pi5b3g_child_lineage",
]
