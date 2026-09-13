from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_orchestrator.delegation_evidence import (
    DelegationOutcomeJournal,
    DelegationOutcomePhase,
)
from personal_ai_orchestrator.delegation_quota_calibration import (
    DelegationQuotaCalibrationJournal,
    QuotaPairComparisonReason,
    build_quota_baseline,
    compare_quota_after,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.safety_kernel import TaskState
from tests.quota_identity_fixtures import snapshot
from tests.test_pi5_child_execution import _setup

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
POOL = "zai-coding-plan"


def _pair():
    before = snapshot(POOL, NOW, remaining=0.8).model_copy(update={"id": "quota-before"})
    after_windows = tuple(
        window.model_copy(update={"remaining_fraction": 0.7, "used_fraction": 0.3})
        for window in before.windows
    )
    after = before.model_copy(
        update={
            "id": "quota-after",
            "observed_at": NOW + timedelta(minutes=1),
            "windows": after_windows,
        }
    )
    return before, after


class _FakeRefresh:
    def __init__(self, before, after):
        self.current = before
        self.after = after
        self.refresh_count = 0
        self.providers: list[str] = []

    def snapshot_for_pool(self, pool_id):
        return self.current if self.current.quota_pool_id == pool_id else None

    def refresh(self, provider_id):
        self.refresh_count += 1
        self.providers.append(provider_id)
        self.current = self.after
        return ()


def test_comparable_pair_accepts_same_pool_window_and_reset_cycle():
    before, after = _pair()
    baseline = build_quota_baseline(
        observation_id="obs",
        provider_id="zai-coding-plan",
        quota_pool_id=POOL,
        snapshot=before,
    )
    result = compare_quota_after(baseline, after)
    assert result.comparable
    assert result.reason is QuotaPairComparisonReason.OK
    assert result.quota_before_snapshot_id == "quota-before"
    assert result.quota_after_snapshot_id == "quota-after"


@pytest.mark.parametrize("mutation", ["pool", "window", "reset", "time", "id"])
def test_comparable_pair_rejects_identity_or_cycle_mismatch(mutation):
    before, after = _pair()
    baseline = build_quota_baseline(
        observation_id="obs",
        provider_id="zai-coding-plan",
        quota_pool_id=POOL,
        snapshot=before,
    )
    if mutation == "pool":
        after = after.model_copy(update={"quota_pool_id": "other-pool"})
    elif mutation == "window":
        after = after.model_copy(
            update={
                "windows": tuple(
                    w.model_copy(update={"window_id": "other"}) for w in after.windows
                )
            }
        )
    elif mutation == "reset":
        after = after.model_copy(
            update={
                "windows": tuple(
                    w.model_copy(update={"reset_at": w.reset_at + timedelta(hours=5)})
                    for w in after.windows
                )
            }
        )
    elif mutation == "time":
        after = after.model_copy(update={"observed_at": NOW - timedelta(seconds=1)})
    else:
        after = after.model_copy(update={"id": "quota-before"})
    result = compare_quota_after(baseline, after)
    assert not result.comparable
    assert result.reason is not QuotaPairComparisonReason.OK


def test_baseline_rejects_wrong_pool_unknown_or_imprecise_snapshot():
    before, _ = _pair()
    assert build_quota_baseline(
        observation_id="obs",
        provider_id="zai-coding-plan",
        quota_pool_id="other",
        snapshot=before,
    ) is None
    unknown = before.model_copy(update={"confidence": EvidenceConfidence.UNKNOWN})
    assert build_quota_baseline(
        observation_id="obs",
        provider_id="zai-coding-plan",
        quota_pool_id=POOL,
        snapshot=unknown,
    ) is None
    imprecise = before.model_copy(
        update={
            "windows": tuple(
                w.model_copy(
                    update={
                        "remaining_fraction": None,
                        "used_fraction": None,
                        "confidence": EvidenceConfidence.UNKNOWN,
                    }
                )
                for w in before.windows
            )
        }
    )
    assert build_quota_baseline(
        observation_id="obs",
        provider_id="zai-coding-plan",
        quota_pool_id=POOL,
        snapshot=imprecise,
    ) is None


@pytest.mark.asyncio
async def test_default_off_performs_zero_extra_refreshes(tmp_path, monkeypatch):
    store, _, port, _, plan, _ = _setup(tmp_path, monkeypatch)
    before, after = _pair()
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "evidence"
    port.delegation_shadow_journal = DelegationShadowJournal(root)
    port.delegation_outcome_journal = DelegationOutcomeJournal(root)
    port.quota_calibration_journal = DelegationQuotaCalibrationJournal(root)
    port.quota_refresh_service = refresh
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert refresh.refresh_count == 0
        observation_id = port.delegation_shadow_journal.observation_id(
            parent_run_id=plan.parent_run_id,
            child_task_id=plan.child_task_id,
        )
        outcome = port.delegation_outcome_journal.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        )
        assert outcome is not None
        assert outcome.quota_before_snapshot_id is None
        assert outcome.quota_after_snapshot_id is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_enabled_campaign_refreshes_once_and_freezes_pair(tmp_path, monkeypatch):
    store, _, port, _, plan, _ = _setup(tmp_path, monkeypatch)
    before, after = _pair()
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "evidence"
    shadow_journal = DelegationShadowJournal(root)
    outcome_journal = DelegationOutcomeJournal(root)
    calibration_journal = DelegationQuotaCalibrationJournal(root)
    port.delegation_shadow_journal = shadow_journal
    port.delegation_outcome_journal = outcome_journal
    port.quota_calibration_journal = calibration_journal
    port.quota_refresh_service = refresh
    port.quota_calibration_enabled = True
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert refresh.refresh_count == 1
        assert refresh.providers == ["zai-coding-plan"]
        observation_id = shadow_journal.observation_id(
            parent_run_id=plan.parent_run_id,
            child_task_id=plan.child_task_id,
        )
        baseline = calibration_journal.load(observation_id)
        assert baseline is not None and baseline.snapshot_id == "quota-before"
        child = outcome_journal.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        )
        assert child is not None
        assert child.quota_before_snapshot_id == "quota-before"
        assert child.quota_after_snapshot_id == "quota-after"
        assert "comparable_quota_before_after_not_captured" not in child.limitations

        assert (await port.execute_child(plan)).verified
        assert refresh.refresh_count == 1

        store.finish_run("parent-run", status="FINISHED", result={})
        parent = store.get_task(plan.parent_task_id)
        parent = store.transition_task(
            parent.task_id,
            TaskState.WORKER_FINISHED,
            expected_version=parent.state_version,
            reason="synthetic parent worker finished",
        )
        parent = store.transition_task(
            parent.task_id,
            TaskState.VERIFYING,
            expected_version=parent.state_version,
            reason="synthetic parent verification",
        )
        store.transition_task(
            parent.task_id,
            TaskState.VERIFIED,
            expected_version=parent.state_version,
            reason="synthetic verifier pass",
        )
        port.record_parent_final_outcomes(
            parent_task_id=plan.parent_task_id,
            parent_run_id=plan.parent_run_id,
        )
        assert refresh.refresh_count == 1
        parent_outcome = outcome_journal.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.PARENT_FINAL,
        )
        assert parent_outcome is not None
        assert parent_outcome.quota_before_snapshot_id == "quota-before"
        assert parent_outcome.quota_after_snapshot_id == "quota-after"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_calibration_failure_never_blocks_verified_child(tmp_path, monkeypatch):
    store, _, port, _, plan, _ = _setup(tmp_path, monkeypatch)
    before, after = _pair()
    after = after.model_copy(update={"quota_pool_id": "wrong-pool"})
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "evidence"
    port.delegation_shadow_journal = DelegationShadowJournal(root)
    port.delegation_outcome_journal = DelegationOutcomeJournal(root)
    port.quota_calibration_journal = DelegationQuotaCalibrationJournal(root)
    port.quota_refresh_service = refresh
    port.quota_calibration_enabled = True
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert refresh.refresh_count == 1
        observation_id = port.delegation_shadow_journal.observation_id(
            parent_run_id=plan.parent_run_id,
            child_task_id=plan.child_task_id,
        )
        outcome = port.delegation_outcome_journal.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        )
        assert outcome is not None and outcome.child_verified is True
        assert outcome.quota_before_snapshot_id is None
        assert outcome.quota_after_snapshot_id is None
        assert any(item.startswith("quota_calibration_") for item in outcome.limitations)
    finally:
        store.close()
