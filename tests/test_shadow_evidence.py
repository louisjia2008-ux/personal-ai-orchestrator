from datetime import UTC, datetime

import pytest

from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal, ShadowObservation

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
        predicted_burn_fraction=0.05,
        observed_burn_fraction=0.04,
        verified=verified,
        regression_detected=regression,
        attempts_to_green=1,
        time_to_green_seconds=42.0,
        observed_at=NOW,
    )


def test_shadow_observation_builds_stable_identity() -> None:
    first = observation()
    second = observation()
    assert first.observation_id == second.observation_id
    assert first.recommendation_followed is True


def test_journal_is_append_only_and_idempotent(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    item = observation()
    first = journal.append(item)
    second = journal.append(item)
    assert first == second
    assert journal.load_all() == (item,)

    conflicting = item.model_copy(update={"verified": False})
    with pytest.raises(ValueError, match="different content"):
        journal.append(conflicting)


def test_summary_never_equates_small_sample_with_active_acceptance(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.append(observation())
    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)
    assert summary.review_eligible is False
    assert summary.blocking_reasons


def test_summary_becomes_review_eligible_after_required_verified_cycles(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.append(observation(task_id="task-1", request_id="req-1", decision_id="dec-1"))
    journal.append(
        observation(
            task_id="task-2",
            request_id="req-2",
            decision_id="dec-2",
            reset_cycle_ids=("minimax-week-2",),
        )
    )
    summary = journal.summarize(minimum_observations=2, minimum_reset_cycles=2)
    assert summary.review_eligible is True
    assert summary.regressions == 0


def test_regression_blocks_shadow_review_eligibility(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path)
    journal.append(observation(regression=True))
    summary = journal.summarize(minimum_observations=1, minimum_reset_cycles=1)
    assert summary.review_eligible is False
    assert any("regression" in reason for reason in summary.blocking_reasons)
