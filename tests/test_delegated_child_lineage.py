from __future__ import annotations

import json

import pytest

from personal_ai_orchestrator.pi5_identity import CampaignExecutionIdentityFactory
from personal_ai_orchestrator.pi5b3g_lineage import (
    PI5B3G_LINEAGE_VALIDATOR_VERSION,
    Pi5b3gLineageRule,
    validate_pi5b3g_child_lineage,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.safety_kernel import (
    DelegatedTaskLineageError,
    DelegatedTaskLineageRule,
    SafetyKernelStore,
    TaskState,
)

CAMPAIGN_A = "delegation-campaign-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CAMPAIGN_B = "delegation-campaign-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _prepare_parent(store: SafetyKernelStore, task_id: str, run_id: str) -> None:
    parent = store.submit_task(
        task_id=task_id,
        request_id=f"submit-{task_id}",
        intent="parent fixture",
        scheduling_policy="BALANCED",
    )
    parent = store.transition_task(
        task_id,
        TaskState.READY,
        expected_version=parent.state_version,
        reason="fixture ready",
    )
    store.transition_task(
        task_id,
        TaskState.RUNNING,
        expected_version=parent.state_version,
        reason="fixture running",
    )
    store.start_run(
        run_id=run_id,
        task_id=task_id,
        worker_id="fixture-worker",
        pid=12345,
    )


def _prepare_campaign_child(store: SafetyKernelStore, campaign: str, number: int):
    identity = CampaignExecutionIdentityFactory(campaign).observation(number)
    child_identity = identity.child(ordinal=1)
    _prepare_parent(store, identity.parent_task_id, identity.parent_run_id)
    store.submit_task(
        task_id=child_identity.task_id,
        request_id=child_identity.submit_request_id,
        intent="child fixture",
        scheduling_policy="BALANCED",
        delegated_parent=(identity.parent_task_id, identity.parent_run_id),
    )
    return identity, child_identity


def _replace_submission_payload(
    store: SafetyKernelStore, task_id: str, payload: object
) -> None:
    store.connection.execute(
        "UPDATE audit_events SET payload_json=? "
        "WHERE task_id=? AND event_type='TASK_SUBMITTED'",
        (json.dumps(payload, sort_keys=True), task_id),
    )


def test_canonical_delegated_lineage_resolves_valid_child() -> None:
    store = SafetyKernelStore()
    identity, child = _prepare_campaign_child(store, CAMPAIGN_A, 1)

    lineage = store.resolve_delegated_task_lineage(child.task_id)

    assert lineage.child_task_id == child.task_id
    assert lineage.parent_task_id == identity.parent_task_id
    assert lineage.parent_run_id == identity.parent_run_id


@pytest.mark.parametrize(
    ("fixture", "expected_rule"),
    [
        ("missing_task", DelegatedTaskLineageRule.CHILD_TASK_NOT_FOUND),
        ("missing_submission", DelegatedTaskLineageRule.TASK_SUBMISSION_MISSING),
        ("missing_lineage", DelegatedTaskLineageRule.METADATA_MISSING),
        ("partial_lineage", DelegatedTaskLineageRule.METADATA_PARTIAL),
        ("invalid_lineage", DelegatedTaskLineageRule.METADATA_INVALID),
        ("missing_parent", DelegatedTaskLineageRule.PARENT_TASK_NOT_FOUND),
        ("missing_run", DelegatedTaskLineageRule.PARENT_RUN_NOT_FOUND),
        ("run_task_mismatch", DelegatedTaskLineageRule.PARENT_RUN_TASK_MISMATCH),
        ("ambiguous", DelegatedTaskLineageRule.TASK_SUBMISSION_AMBIGUOUS),
    ],
)
def test_canonical_delegated_lineage_failures_are_deterministic(
    fixture: str, expected_rule: DelegatedTaskLineageRule
) -> None:
    store = SafetyKernelStore()
    if fixture == "missing_task":
        child_task_id = "missing-child"
    else:
        identity, child = _prepare_campaign_child(store, CAMPAIGN_A, 1)
        child_task_id = child.task_id
        if fixture == "missing_submission":
            store.connection.execute(
                "DELETE FROM audit_events WHERE task_id=? AND event_type='TASK_SUBMITTED'",
                (child_task_id,),
            )
        elif fixture == "missing_lineage":
            _replace_submission_payload(store, child_task_id, {"request_id": "fixture"})
        elif fixture == "partial_lineage":
            _replace_submission_payload(
                store,
                child_task_id,
                {"delegated_parent_task_id": identity.parent_task_id},
            )
        elif fixture == "invalid_lineage":
            _replace_submission_payload(
                store,
                child_task_id,
                {
                    "delegated_parent_task_id": "",
                    "delegated_parent_run_id": identity.parent_run_id,
                },
            )
        elif fixture == "missing_parent":
            _replace_submission_payload(
                store,
                child_task_id,
                {
                    "delegated_parent_task_id": "missing-parent",
                    "delegated_parent_run_id": identity.parent_run_id,
                },
            )
        elif fixture == "missing_run":
            _replace_submission_payload(
                store,
                child_task_id,
                {
                    "delegated_parent_task_id": identity.parent_task_id,
                    "delegated_parent_run_id": "missing-run",
                },
            )
        elif fixture == "run_task_mismatch":
            other = store.submit_task(
                task_id="other-parent",
                request_id="other-parent-submit",
                intent="other parent",
            )
            store.connection.execute(
                "UPDATE runs SET task_id=? WHERE run_id=?",
                (other.task_id, identity.parent_run_id),
            )
        elif fixture == "ambiguous":
            event = next(
                event
                for event in store.audit_events(child_task_id)
                if event["event_type"] == "TASK_SUBMITTED"
            )
            store.connection.execute(
                "INSERT INTO audit_events(task_id,event_type,payload_json,created_at) "
                "VALUES(?,?,?,?)",
                (
                    child_task_id,
                    "TASK_SUBMITTED",
                    json.dumps(event["payload"], sort_keys=True),
                    event["created_at"],
                ),
            )

    with pytest.raises(DelegatedTaskLineageError) as caught:
        store.resolve_delegated_task_lineage(child_task_id)
    assert caught.value.rule_id is expected_rule
    assert str(caught.value) == expected_rule.value


def test_old_campaign_d_observer_failure_and_repaired_observer() -> None:
    store = SafetyKernelStore()
    identity, child = _prepare_campaign_child(store, CAMPAIGN_A, 1)
    task = store.get_task(child.task_id)

    with pytest.raises(AttributeError, match="delegated_parent_task_id"):
        _ = task.delegated_parent_task_id

    result = validate_pi5b3g_child_lineage(
        store=store,
        child_task_id=child.task_id,
        expected=identity,
        current_campaign_parents={identity.parent_task_id: identity},
    )
    assert result.allowed
    assert result.validator_version == PI5B3G_LINEAGE_VALIDATOR_VERSION
    assert result.rule_id == Pi5b3gLineageRule.ALLOW.value
    assert result.resolved_parent_task_id == identity.parent_task_id
    assert result.parent_found
    assert result.campaign_match
    assert result.observation_match
    diagnostic = result.model_dump(mode="json")
    assert_sanitized(diagnostic)
    assert "intent" not in diagnostic
    assert "reason" not in diagnostic
    assert "transcript" not in diagnostic


def test_parent_task_used_as_child_fails_closed() -> None:
    store = SafetyKernelStore()
    identity, _ = _prepare_campaign_child(store, CAMPAIGN_A, 1)
    result = validate_pi5b3g_child_lineage(
        store=store,
        child_task_id=identity.parent_task_id,
        expected=identity,
        current_campaign_parents={identity.parent_task_id: identity},
    )
    assert not result.allowed
    assert result.rule_id == DelegatedTaskLineageRule.METADATA_MISSING.value


def test_child_from_different_campaign_is_rejected() -> None:
    store = SafetyKernelStore()
    expected = CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(1)
    _, other_child = _prepare_campaign_child(store, CAMPAIGN_B, 1)
    result = validate_pi5b3g_child_lineage(
        store=store,
        child_task_id=other_child.task_id,
        expected=expected,
        current_campaign_parents={expected.parent_task_id: expected},
    )
    assert not result.allowed
    assert result.rule_id == Pi5b3gLineageRule.CROSS_CAMPAIGN_PARENT.value
    assert result.parent_found
    assert not result.campaign_match
    assert not result.observation_match


def test_wrong_observation_parent_is_rejected_without_id_parsing() -> None:
    store = SafetyKernelStore()
    factory = CampaignExecutionIdentityFactory(CAMPAIGN_A)
    expected = factory.observation(1)
    actual, child = _prepare_campaign_child(store, CAMPAIGN_A, 2)
    parents = {
        expected.parent_task_id: expected,
        actual.parent_task_id: actual,
    }
    result = validate_pi5b3g_child_lineage(
        store=store,
        child_task_id=child.task_id,
        expected=expected,
        current_campaign_parents=parents,
    )
    assert not result.allowed
    assert result.rule_id == Pi5b3gLineageRule.WRONG_OBSERVATION_PARENT.value
    assert result.parent_found
    assert result.campaign_match
    assert not result.observation_match


def test_malformed_or_cross_campaign_identity_map_fails_closed() -> None:
    store = SafetyKernelStore()
    identity, child = _prepare_campaign_child(store, CAMPAIGN_A, 1)
    other = CampaignExecutionIdentityFactory(CAMPAIGN_B).observation(1)
    result = validate_pi5b3g_child_lineage(
        store=store,
        child_task_id=child.task_id,
        expected=identity,
        current_campaign_parents={identity.parent_task_id: other},
    )
    assert not result.allowed
    assert result.rule_id == Pi5b3gLineageRule.CAMPAIGN_CONTEXT_INVALID.value
