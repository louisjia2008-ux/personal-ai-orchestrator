from datetime import UTC, datetime

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.shadow_evidence import (
    ResetCycleReference,
    ResetCycleSource,
    ShadowAcceptancePolicy,
    ShadowCampaignState,
    ShadowCampaignStatus,
    ShadowEvidenceJournal,
    ShadowObservation,
)

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def observation(
    *,
    task_id: str = "task-1",
    request_id: str = "req-1",
    decision_id: str = "dec-1",
    reset_cycle_ids: tuple[str, ...] = ("minimax-week-1",),
    manual: str = "m3-sub",
    scheduler: str | None = "m3-sub",
    verified: bool = True,
    regression: bool = False,
    provider_id: str | None = "minimax",
    quota_pool_id: str | None = "minimax-token-plan-cn",
    task_family: str = "implementation",
    quota_confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
    collector_status: QuotaCollectionStatus | None = QuotaCollectionStatus.SUCCESS,
) -> ShadowObservation:
    return ShadowObservation.build(
        task_id=task_id,
        request_id=request_id,
        decision_id=decision_id,
        reset_cycle_ids=reset_cycle_ids,
        manual_execution_target_id=manual,
        scheduler_execution_target_id=scheduler,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        quota_snapshot_ids=("quota-before",),
        quota_after_snapshot_ids=("quota-after",),
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        task_family=task_family,
        quota_confidence=quota_confidence,
        collector_status=collector_status,
        predicted_burn_fraction=0.05,
        observed_burn_fraction=0.04,
        verified=verified,
        regression_detected=regression,
        attempts_to_green=1,
        time_to_green_seconds=42.0,
        observed_at=NOW,
    )


def reset_ref(
    reset_cycle_id: str,
    *,
    provider_id: str = "minimax",
    quota_pool_id: str = "minimax-token-plan-cn",
    source: ResetCycleSource = ResetCycleSource.PROVIDER_EXACT,
    confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
) -> ResetCycleReference:
    return ResetCycleReference(
        reset_cycle_id=reset_cycle_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        quota_snapshot_id=f"quota-{reset_cycle_id}",
        reset_at=NOW if source is ResetCycleSource.PROVIDER_EXACT else None,
        confidence=confidence,
        source=source,
        source_method="fixture-provider-reset-metadata",
        observed_at=NOW,
    )


def append_with_refs(
    journal: ShadowEvidenceJournal,
    item: ShadowObservation,
    *refs: ResetCycleReference,
) -> None:
    for ref in refs or tuple(reset_ref(cycle) for cycle in item.reset_cycle_ids):
        journal.append_reset_cycle(ref)
    journal.append(item)


def test_shadow_observation_builds_stable_identity() -> None:
    first = observation()
    second = observation()
    assert first.observation_id == second.observation_id
    assert first.recommendation_followed is True


def test_journal_is_append_only_and_idempotent(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    item = observation()
    journal.append_reset_cycle(reset_ref("minimax-week-1"))
    first = journal.append(item)
    second = journal.append(item)
    assert first == second
    assert journal.load_all() == (item,)

    conflicting = item.model_copy(update={"verified": False})
    with pytest.raises(ValueError, match="different content"):
        journal.append(conflicting)


def test_summary_never_equates_small_sample_with_active_acceptance(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    append_with_refs(journal, observation())
    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)
    assert summary.review_eligible is False
    assert summary.blocking_reasons


def test_summary_becomes_review_eligible_after_required_verified_cycles(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    append_with_refs(
        journal,
        observation(task_id="task-1", request_id="req-1", decision_id="dec-1"),
    )
    append_with_refs(
        journal,
        observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
        ),
    )
    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)
    assert summary.review_eligible is True
    assert summary.regressions == 0


def test_campaign_state_survives_journal_restart(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    state = ShadowCampaignState(
        campaign_id="shadow-2026-08-30",
        status=ShadowCampaignStatus.BOOTSTRAPPED,
        started_at=NOW,
        head="abc123",
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        providers_enabled=("minimax",),
        providers_unknown=("zai",),
        reset_cycles_required=2,
    )
    journal.save_campaign_state(state)
    append_with_refs(journal, observation())

    restarted = ShadowEvidenceJournal(tmp_path)

    assert restarted.load_campaign_state() == state
    assert restarted.load_all() == (observation(),)


def test_campaign_summary_groups_reset_cycles_and_provider_metrics(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.save_campaign_state(
        ShadowCampaignState(
            campaign_id="shadow-2026-08-30",
            status=ShadowCampaignStatus.BOOTSTRAPPED,
            started_at=NOW,
            head="abc123",
            catalog_snapshot_id="catalog-1",
            policy_snapshot_id="policy-1",
            providers_enabled=("minimax",),
            providers_unknown=("zai",),
        )
    )
    append_with_refs(
        journal,
        observation(task_id="task-1", request_id="req-1", decision_id="dec-1"),
    )
    append_with_refs(
        journal,
        observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
            manual="m2-sub",
            scheduler="m3-sub",
            quota_confidence=EvidenceConfidence.UNKNOWN,
            collector_status=QuotaCollectionStatus.UNKNOWN,
        ),
    )

    summary = journal.summarize_campaign(minimum_observations=2, minimum_reset_cycles=2)

    assert summary.campaign_id == "shadow-2026-08-30"
    assert summary.reset_cycles_observed == 2
    assert summary.real_reset_cycles_observed == 2
    assert summary.review_eligible is False
    assert summary.production_active_authorized is False
    assert len(summary.groups) == 2
    group = summary.groups[0]
    assert group.provider_id == "minimax"
    assert group.quota_pool_id == "minimax-token-plan-cn"
    assert group.execution_target_id == "m2-sub"
    assert group.task_family == "implementation"
    assert group.observations == 1
    assert group.review_eligible is False
    assert group.recommendation_matches == 0
    assert group.recommendation_disagreements == 1
    assert group.agreement_rate == 0.0
    assert group.attempts_to_green_median == 1.0
    assert group.time_to_green_p90_seconds == 42.0
    assert group.predicted_burn_fraction_total == 0.05
    assert group.observed_burn_fraction_total == 0.04
    assert group.unknown_quota_observations == 1
    assert group.collector_failure_observations == 1


def test_reset_cycle_reference_requires_truthful_confidence() -> None:
    ref = ResetCycleReference(
        reset_cycle_id="minimax-week-unknown",
        provider_id="minimax",
        quota_pool_id="minimax-token-plan-cn",
        confidence=EvidenceConfidence.UNKNOWN,
        source_method="provider-reset-metadata-unavailable",
    )

    assert ref.reset_at is None
    assert ref.confidence is EvidenceConfidence.UNKNOWN


def test_missing_reset_reference_fails_closed(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)

    with pytest.raises(ValueError, match="missing reset cycle reference"):
        journal.append(observation())


def test_reset_reference_conflict_is_rejected(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.append_reset_cycle(reset_ref("minimax-week-1"))

    with pytest.raises(ValueError, match="different content"):
        journal.append_reset_cycle(
            reset_ref("minimax-week-1", quota_pool_id="other-pool")
        )


def test_reset_reference_provider_and_pool_must_match_observation(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.append_reset_cycle(reset_ref("minimax-week-1", provider_id="other-provider"))

    with pytest.raises(ValueError, match="provider_id"):
        journal.append(observation())

    other = ShadowEvidenceJournal(tmp_path / "pool")
    other.append_reset_cycle(reset_ref("minimax-week-1", quota_pool_id="other-pool"))
    with pytest.raises(ValueError, match="quota_pool_id"):
        other.append(observation())


def test_unknown_and_synthetic_reset_cycles_do_not_count_for_real_acceptance(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    append_with_refs(
        journal,
        observation(task_id="task-1", request_id="req-1", decision_id="dec-1"),
        reset_ref(
            "minimax-week-1",
            source=ResetCycleSource.UNKNOWN,
            confidence=EvidenceConfidence.UNKNOWN,
        ),
    )
    append_with_refs(
        journal,
        observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
        ),
        reset_ref("minimax-week-2", source=ResetCycleSource.SYNTHETIC_TEST),
    )

    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)

    assert summary.reset_cycles == 2
    assert summary.real_reset_cycles_observed == 0
    assert summary.synthetic_reset_cycles == 1
    assert summary.review_eligible is False


def test_campaign_policy_snapshot_controls_review_thresholds(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.save_campaign_state(
        ShadowCampaignState(
            campaign_id="shadow-policy",
            status=ShadowCampaignStatus.BOOTSTRAPPED,
            started_at=NOW,
            head="abc123",
            catalog_snapshot_id="catalog-1",
            policy_snapshot_id="policy-1",
            providers_enabled=("minimax",),
            reset_cycles_required=3,
            acceptance_policy=ShadowAcceptancePolicy(
                minimum_observations=3,
                minimum_real_reset_cycles=3,
            ),
        )
    )
    append_with_refs(
        journal,
        observation(task_id="task-1", request_id="req-1", decision_id="dec-1"),
    )
    append_with_refs(
        journal,
        observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
        ),
    )

    summary = journal.summarize_campaign(minimum_observations=1, minimum_reset_cycles=2)

    assert summary.reset_cycles_required == 3
    assert summary.review_eligible is False
    assert any("need at least 3 observations" in reason for reason in summary.blocking_reasons)


def test_cohort_scoped_review_eligibility_isolated_by_target_and_pool(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.save_campaign_state(
        ShadowCampaignState(
            campaign_id="shadow-cohort",
            status=ShadowCampaignStatus.BOOTSTRAPPED,
            started_at=NOW,
            head="abc123",
            catalog_snapshot_id="catalog-1",
            policy_snapshot_id="policy-1",
            reset_cycles_required=2,
            acceptance_policy=ShadowAcceptancePolicy(
                minimum_observations=2,
                minimum_real_reset_cycles=2,
            ),
        )
    )
    append_with_refs(journal, observation(task_id="a1", request_id="r1", decision_id="d1"))
    append_with_refs(
        journal,
        observation(
            task_id="a2",
            request_id="r2",
            decision_id="d2",
            reset_cycle_ids=("minimax-week-2",),
        ),
    )
    append_with_refs(
        journal,
        observation(
            task_id="b1",
            request_id="r3",
            decision_id="d3",
            reset_cycle_ids=("zai-week-1",),
            manual="glm-sub",
            scheduler="glm-sub",
            provider_id="zai",
            quota_pool_id="zai-coding-plan",
        ),
        reset_ref(
            "zai-week-1",
            provider_id="zai",
            quota_pool_id="zai-coding-plan",
        ),
    )

    summary = journal.summarize_campaign()

    assert summary.review_eligible is False
    assert summary.active_eligible_cohorts == (
        "minimax|minimax-token-plan-cn|m3-sub|implementation",
    )
    assert {group.execution_target_id: group.review_eligible for group in summary.groups} == {
        "glm-sub": False,
        "m3-sub": True,
    }


def test_regression_blocks_shadow_review_eligibility(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    append_with_refs(journal, observation(regression=True))
    summary = journal.summarize(minimum_observations=1, minimum_reset_cycles=1)
    assert summary.review_eligible is False
    assert any("regression" in reason for reason in summary.blocking_reasons)
