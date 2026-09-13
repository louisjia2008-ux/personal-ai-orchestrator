from __future__ import annotations

import pytest

from personal_ai_orchestrator.delegation_policy import (
    DelegationPolicyMode,
    DelegationReasonCode,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from tests.test_pi5_child_execution import _setup


async def _run_with_shadow(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    journal = DelegationShadowJournal(tmp_path / "shadow-state")
    port.delegation_shadow_journal = journal
    result = await port.execute_child(plan)
    observation_id = journal.observation_id(
        parent_run_id=plan.parent_run_id,
        child_task_id=plan.child_task_id,
    )
    return store, port, plan, observations, journal, observation_id, result


@pytest.mark.asyncio
async def test_shadow_record_replays_without_changing_verified_execution(tmp_path, monkeypatch):
    store, port, plan, observations, journal, observation_id, result = await _run_with_shadow(
        tmp_path, monkeypatch
    )
    try:
        assert result.verified
        assert observations == [result.selected_execution_target_id]
        record = journal.load(observation_id)
        assert record is not None
        assert record.decision.mode is DelegationPolicyMode.SHADOW
        assert record.replay_matches()
        assert record.parent_task_id == plan.parent_task_id
        assert record.parent_run_id == plan.parent_run_id
        assert record.child_task_id == plan.child_task_id
        assert record.selected_child_execution_target_id == result.selected_execution_target_id
        assert record.facts.eligible_child_count >= 1
        # The current owner-dispatch recommender has no calibrated per-task burn
        # estimate. SHADOW must record that absence instead of manufacturing one.
        assert record.facts.predicted_child_burn_fraction is None
        assert "predicted_child_burn_unavailable" in record.limitations
        assert (
            DelegationReasonCode.REQUIRED_BURN_ESTIMATE_MISSING
            in record.decision.reasons
        )
        # A SHADOW denial is observational only: the exact PI-5B2 execution still
        # reached VERIFIED through the ordinary scheduler/quota/verifier chain.
        assert result.verified

        path = journal.path_for(observation_id)
        rendered = path.read_text(encoding="utf-8")
        assert plan.intent not in rendered
        assert plan.reason not in rendered

        before = path.read_bytes()
        assert (await port.execute_child(plan)).verified
        assert path.read_bytes() == before
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shadow_journal_failure_cannot_block_child_execution(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)

    class FailingJournal:
        def append(self, record):
            raise OSError("synthetic journal failure")

    port.delegation_shadow_journal = FailingJournal()
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert observations == [result.selected_execution_target_id]
        events = store.connection.execute(
            "SELECT event_type,payload_json FROM audit_events "
            "WHERE event_type='DELEGATION_SHADOW_RECORD_FAILED'"
        ).fetchall()
        assert len(events) == 1
        assert "synthetic journal failure" not in events[0]["payload_json"]
        assert "SHADOW_CAPTURE_FAILED" in events[0]["payload_json"]
    finally:
        store.close()


def test_shadow_path_rejects_unsafe_observation_ids(tmp_path):
    journal = DelegationShadowJournal(tmp_path)
    for value in ("", "../escape", "a/b", "a\\b"):
        try:
            journal.path_for(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe id accepted: {value!r}")
