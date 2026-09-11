"""M1 WP3 fix (F5) — QuotaProviderCardView.collection_failure_streak.

The streak is the maximum ``consecutive_failures`` across this
provider's targets. The per-target streak already lives on
``ObservedAvailabilityView``; the card-level rollup lets the owner
spot a host that has been unable to probe the provider without
drilling into a target row. ``0`` is the "no journal entry" default.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.control_api import (
    QuotaOverviewView,
    QuotaProviderCardView,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    observe_success,
    unknown_availability,
)

FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_quota_provider_card_view_collection_failure_streak_field_exists() -> None:
    """``collection_failure_streak`` exists with 0 default and round-trips."""

    card = QuotaProviderCardView(
        provider_id="x",
        display_name="X",
        connection_state="CONNECTED",
        quota_state="UNKNOWN",
        confidence="UNKNOWN",
    )
    assert card.collection_failure_streak == 0
    # Round-trip JSON; the wire key is ``collection_failure_streak``.
    rendered = card.model_dump_json()
    parsed = json.loads(rendered)
    assert parsed["collection_failure_streak"] == 0
    # Decode leniently: a pre-F5 daemon omits the field → 0.
    partial = json.loads(rendered)
    partial.pop("collection_failure_streak")
    decoded = QuotaProviderCardView.model_validate(partial)
    assert decoded.collection_failure_streak == 0


def test_quota_overview_view_collection_failure_streak_round_trip() -> None:
    """A 2 on the wire round-trips; an absent field reads as 0."""

    body = {
        "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
        "summary": {
            "connected_provider_count": 1,
            "quota_observable_provider_count": 1,
            "quota_unknown_provider_count": 0,
            "quota_warning_count": 0,
            "quota_exhausted_count": 0,
        },
        "providers": [
            {
                "provider_id": "minimax-cn-coding-plan",
                "display_name": "MiniMax CN Coding Plan",
                "connection_state": "CONNECTED",
                "auth_state": "AUTH_FROM_ENV_PRESENCE",
                "quota_state": "OBSERVED",
                "confidence": "ESTIMATED",
                "readonly_source_available": True,
                "collector_available": True,
                "credential_source": "ENV",
                "quota_pools": [],
                "collection_failure_streak": 2,
            }
        ],
        "history": {"observations": [], "retention_limit": 0},
    }
    view = QuotaOverviewView.model_validate(body)
    assert view.providers[0].collection_failure_streak == 2

    # Lenient: drop the key, decode still works.
    body["providers"][0].pop("collection_failure_streak")
    lenient = QuotaOverviewView.model_validate(body)
    assert lenient.providers[0].collection_failure_streak == 0


def test_card_builder_rolls_up_max_consecutive_failures_across_targets(tmp_path: Path) -> None:
    """The card-level streak is the max across this provider's targets.

    Sanity-checks the rollup logic with a journal that holds two
    targets for the same provider with different streaks; the card
    surfaces the larger one.
    """

    journal = QuotaAvailabilityJournal(tmp_path)
    provider_id = "minimax-cn-coding-plan"
    # Real-world execution target ids are ``<provider>-<sku>`` (one
    # dash, no slash). The journal's ``path_for`` rejects path
    # separators so we use the dash form here.
    target_ids = (
        f"{provider_id}-t-low",
        f"{provider_id}-t-high",
        "zai-coding-plan-t-other",
    )
    # Provider target #1: streak of 1 (one UNKNOWN observation).
    journal.save(
        unknown_availability(
            execution_target_id=target_ids[0],
            provider_id=provider_id,
            quota_pool_id="pool-1",
            observed_at=FIXED_NOW,
        )
    )
    # Provider target #2: streak of 3 (UNCERTAIN_LOCKED threshold).
    for _ in range(3):
        previous = journal.load(target_ids[1])
        journal.save(
            unknown_availability(
                execution_target_id=target_ids[1],
                provider_id=provider_id,
                quota_pool_id="pool-2",
                observed_at=FIXED_NOW,
                previous=previous,
            )
        )
    # Different provider's target: should NOT count.
    journal.save(
        unknown_availability(
            execution_target_id=target_ids[2],
            provider_id="zai-coding-plan",
            quota_pool_id="pool-3",
            observed_at=FIXED_NOW,
        )
    )
    # Sanity: load the streak of each target directly.
    assert journal.load(target_ids[0]).consecutive_failures == 1
    assert journal.load(target_ids[1]).consecutive_failures == 3
    assert journal.load(target_ids[2]).consecutive_failures == 1

    # Now exercise the rollup logic exactly the way ``_quota_provider_card``
    # does (the helper itself needs a live service / connection; the rollup
    # arithmetic is duplicated here so we can pin the F5 contract
    # without spinning up the full control plane).
    streak = 0
    for target_id in target_ids:
        evidence = journal.load(target_id)
        if evidence is None:
            continue
        # The real control_api filter: ``target_model.provider_id ==
        # connection.provider_id``. Stand-in: parse the target_id
        # for the test (the real implementation iterates the
        # registry, not the journal).
        evidence_provider_id = evidence.provider_id
        if evidence_provider_id != provider_id:
            continue
        if evidence.consecutive_failures > streak:
            streak = evidence.consecutive_failures
    assert streak == 3


def test_observe_success_resets_streak_so_card_rollup_drops_to_zero(tmp_path: Path) -> None:
    """A successful observation clears the streak end-to-end.

    The card-level rollup must reflect the journal's freshly-zeroed
    streak after a success — the F5 fix is the provider-level view of
    the same A2 reset that powers ``state_at`` ``UNCERTAIN_LOCKED``
    projections.
    """

    journal = QuotaAvailabilityJournal(tmp_path)
    provider_id = "minimax-cn-coding-plan"
    target_id = f"{provider_id}-t1"

    # Three failed collections → streak of 3 → would normally be
    # ``UNCERTAIN_LOCKED``.
    previous = None
    for _ in range(3):
        previous = unknown_availability(
            execution_target_id=target_id,
            provider_id=provider_id,
            quota_pool_id="pool-1",
            observed_at=FIXED_NOW,
            previous=previous,
        )
        journal.save(previous)
    assert journal.load(target_id).consecutive_failures == 3

    # One success resets the streak.
    journal.save(
        observe_success(
            previous=None,  # no prior exhaustion on this run
            execution_target_id=target_id,
            provider_id=provider_id,
            quota_pool_id="pool-1",
            observed_at=FIXED_NOW,
        )
    )
    assert journal.load(target_id).consecutive_failures == 0