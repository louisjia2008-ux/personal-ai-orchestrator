"""Gate-R: daemon side of the routing plan / role / decision contract.

See docs/ROUTING_ROLE_CONTRACT.md.

These tests pin the wire shape so the macOS client can be built against it
before the execution layer plans roles. They deliberately do *not* assert that
roles are produced: role assignment is authoritative execution state, and this
milestone does not fabricate it.
"""

import json
import pathlib

import pytest

from personal_ai_orchestrator.control_api import (
    RoutingDecisionRecordView,
    RoutingPlanCandidateView,
    RoutingPlanView,
    RoutingRoleStateView,
    TaskDetailView,
)

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "macos/PAOMenuBar/Tests/PAOControlKitTests/Fixtures/routing"
)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


PLAN_FIXTURES = [
    "plan_primary_only",
    "plan_primary_reviewer",
    "plan_primary_reviewer_auditor",
    "plan_reviewer_declared_unassigned",
    "plan_reviewer_reroute",
    "plan_completed_failure_outcome",
]


def test_routing_plan_is_optional_and_absent_today():
    """The daemon does not yet plan roles, and must not invent one."""
    assert TaskDetailView.model_fields["routing_plan"].default is None


@pytest.mark.parametrize("name", PLAN_FIXTURES)
def test_every_plan_fixture_validates_against_the_daemon_models(name):
    """The client fixtures are the contract; the daemon models must accept them.

    `_ViewModel` forbids extra keys, so this also catches a fixture drifting a
    field name away from what the daemon would emit.
    """
    detail = TaskDetailView.model_validate(_fixture(name))
    assert detail.routing_plan is not None
    assert detail.routing_plan.task_id == detail.task.task_id


def test_legacy_fixture_validates_without_a_plan():
    detail = TaskDetailView.model_validate(_fixture("legacy_single_worker"))
    assert detail.routing_plan is None
    assert detail.routing.decision["selected_execution_target_id"]


@pytest.mark.parametrize("name", PLAN_FIXTURES)
def test_declared_roles_and_role_entries_agree(name):
    """`roles` carries exactly one entry per declared role, in the same order."""
    plan = TaskDetailView.model_validate(_fixture(name)).routing_plan
    assert list(plan.declared_roles) == [role.role for role in plan.roles]


@pytest.mark.parametrize("name", PLAN_FIXTURES)
def test_active_decision_id_names_a_real_decision(name):
    plan = TaskDetailView.model_validate(_fixture(name)).routing_plan
    for role in plan.roles:
        if role.active_decision_id is None:
            assert role.status == "UNASSIGNED"
            assert role.decisions == ()
        else:
            ids = {decision.decision_id for decision in role.decisions}
            assert role.active_decision_id in ids


@pytest.mark.parametrize("name", PLAN_FIXTURES)
def test_decisions_are_attributed_to_their_own_role(name):
    plan = TaskDetailView.model_validate(_fixture(name)).routing_plan
    for role in plan.roles:
        for decision in role.decisions:
            assert decision.role == role.role


def test_reroute_supersedes_the_decision_it_replaced():
    plan = TaskDetailView.model_validate(_fixture("plan_reviewer_reroute")).routing_plan
    reviewer = next(role for role in plan.roles if role.role == "REVIEWER")
    assert len(reviewer.decisions) == 2
    first, second = reviewer.decisions
    assert first.supersedes_decision_id is None
    assert second.supersedes_decision_id == first.decision_id
    assert second.reroute_reason == "EXECUTION_TARGET_EXHAUSTED"
    # The active assignment stays distinguishable from the history.
    assert reviewer.active_decision_id == second.decision_id


def test_declared_but_unassigned_role_is_explicit_not_absent():
    plan = TaskDetailView.model_validate(
        _fixture("plan_reviewer_declared_unassigned")
    ).routing_plan
    assert "REVIEWER" in plan.declared_roles
    reviewer = next(role for role in plan.roles if role.role == "REVIEWER")
    assert reviewer.status == "UNASSIGNED"
    assert reviewer.outcome == "NONE"
    assert reviewer.decisions == ()


def test_completed_failure_keeps_status_and_outcome_separate():
    plan = TaskDetailView.model_validate(
        _fixture("plan_completed_failure_outcome")
    ).routing_plan
    reviewer = next(role for role in plan.roles if role.role == "REVIEWER")
    assert reviewer.status == "COMPLETED"
    assert reviewer.outcome == "FAIL"
    primary = next(role for role in plan.roles if role.role == "PRIMARY")
    assert (primary.status, primary.outcome) == ("COMPLETED", "PASS")


def test_outcome_defaults_to_none_when_a_daemon_omits_it():
    role = RoutingRoleStateView(role="PRIMARY", status="RUNNING")
    assert role.outcome == "NONE"


def test_plan_revision_defaults_to_one():
    plan = RoutingPlanView(plan_id="rp-1", task_id="PT-1", created_at="2026-09-03T00:00:00Z")
    assert plan.revision == 1
    assert plan.superseded_by_plan_id is None
    assert plan.declared_roles == ()


def test_unknown_enum_values_are_carried_verbatim():
    """Role/status/outcome are open vocabularies on the wire.

    Validation must not reject a value this build does not know: dropping it
    would hide a real assignment from the owner.
    """
    role = RoutingRoleStateView(role="SECOND_REVIEWER", status="QUARANTINED", outcome="PARTIAL")
    assert (role.role, role.status, role.outcome) == (
        "SECOND_REVIEWER",
        "QUARANTINED",
        "PARTIAL",
    )


def test_candidate_and_decision_records_round_trip():
    candidate = RoutingPlanCandidateView(
        execution_target_id="p/m", provider_id="p", model_sku_id="m",
        score=0.5, eligible=True, admitted=True, selected=True,
    )
    decision = RoutingDecisionRecordView(
        decision_id="rd-1", role="PRIMARY", created_at="2026-09-03T00:00:00Z",
        candidates=(candidate,),
    )
    restored = RoutingDecisionRecordView.model_validate(
        json.loads(decision.model_dump_json())
    )
    assert restored == decision
