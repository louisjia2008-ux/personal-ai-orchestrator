from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_ai_orchestrator.delegation_policy import (
    DelegationPolicyMode,
    DelegationReasonCode,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from tests.test_pi5_child_execution import _setup
from tests.test_pi_dispatch_executor import _registry


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
        assert record.enforcement_ready is False

        # The fixture parent and host-selected child consume one canonical pool.
        # Keep this observable without turning same-pool scarcity into an
        # uncalibrated automatic denial.
        assert record.parent_quota_pool_id == record.child_quota_pool_id == "zai-coding-plan"
        assert record.facts.same_quota_pool_as_parent is True
        assert DelegationReasonCode.SHARED_QUOTA_POOL in record.decision.reasons

        # The child-dispatch path has no canonical TaskProfile failure count.
        # BLOCKED dispatch rows include policy/infra failures and must not be
        # mislabelled as model-quality failures just to make escalation trigger.
        assert record.facts.failure_count == 0
        assert (
            "task_profile_failure_count_not_available_in_child_dispatch_context"
            in record.limitations
        )

        # The current owner-dispatch recommender has no calibrated per-task burn
        # estimate. SHADOW must record that absence instead of manufacturing one.
        assert record.facts.predicted_child_burn_fraction is None
        assert "predicted_child_burn_unavailable" in record.limitations
        assert DelegationReasonCode.REQUIRED_BURN_ESTIMATE_MISSING in record.decision.reasons
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
async def test_missing_quota_truth_is_preserved_in_shadow_before_child_block(tmp_path, monkeypatch):
    store, _, port, _, plan, observations = _setup(tmp_path, monkeypatch)
    journal = DelegationShadowJournal(tmp_path / "shadow-state")
    port.delegation_shadow_journal = journal

    service = port.recommendation_factory(store)
    service._quota_refresh_service = SimpleNamespace(
        observations=lambda: (),
        snapshot_for_pool=lambda _: None,
    )
    port.recommendation_factory = lambda _: service
    observation_id = journal.observation_id(
        parent_run_id=plan.parent_run_id,
        child_task_id=plan.child_task_id,
    )
    try:
        result = await port.execute_child(plan)
        assert result.final_state == "BLOCKED"
        assert not result.verified
        assert observations == []

        record = journal.load(observation_id)
        assert record is not None
        assert record.replay_matches()
        assert record.facts.quota_truth_known is False
        assert record.facts.eligible_child_count == 0
        assert record.selected_child_execution_target_id is None
        assert record.child_quota_pool_id is None
        assert record.decision.reasons[0] is DelegationReasonCode.NO_ELIGIBLE_CHILD
        assert record.enforcement_ready is False
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


def test_daemon_wires_shadow_journal_without_creating_files(tmp_path):
    from personal_ai_orchestrator.daemon import build_control_service
    from personal_ai_orchestrator.pi_runtime import PiRuntimeConfig
    from personal_ai_orchestrator.runtime_config import RuntimeConfig

    manager = SimpleNamespace(
        registry=_registry,
        pi_runtime_manager=lambda: object(),
        set_verified_execution_lookup=lambda _: None,
        connected_provider_ids=lambda: (),
        routing_connected_provider_ids=lambda: (),
        runtime_available=lambda _: True,
    )
    runtime_root = tmp_path / "runtime"
    service = build_control_service(
        config=RuntimeConfig(catalog_snapshot_id="synthetic", registry=_registry()),
        state_db=tmp_path / "state.db",
        runtime_state_root=runtime_root,
        execution_repo=tmp_path / "repo",
        provider_registry_manager=manager,
        pi_runtime=PiRuntimeConfig(delegation_enabled=True),
    )
    try:
        port = service.dispatch_executor._executors["pi"].delegation_child_port
        journal = port.delegation_shadow_journal
        assert isinstance(journal, DelegationShadowJournal)
        assert journal.directory == runtime_root / "delegation-shadow-history"
        # Construction alone has no I/O; the directory is created only on the
        # first real/synthetic delegation observation.
        assert not journal.directory.exists()
    finally:
        service.store.close()
