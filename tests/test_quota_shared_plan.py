"""P4.2.6.5 — shared-plan quota semantics for MiniMax and GLM.

The invariant under test throughout: a subscription plan's remaining quota is a
property of the *pool*, and a model's reported figures are either its
consumption or its view of that pool — never a balance of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_collectors.minimax import normalize_minimax_quota
from personal_ai_orchestrator.quota_collectors.zai import (
    GLM_CODING_PLAN_DISPLAY_NAME,
    normalize_zai_model_usage,
    normalize_zai_quota,
)
from personal_ai_orchestrator.quota_plan import (
    BindingWindowReason,
    ConsumptionUnitKind,
    EquivalentScopeKind,
    ModelConsumptionObservation,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
    determine_binding_window,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


# ---------------------------------------------------------------------------
# GLM Coding Plan
# ---------------------------------------------------------------------------


def glm_payload() -> dict:
    """The exact shape returned by a live authenticated read on 2026-09-02.

    Both entries carry the same ``type``; ``unit``/``number`` is the only thing
    distinguishing the 5-hour window from the weekly one.
    """

    return {
        "code": 200,
        "msg": "Operation successful",
        "success": True,
        "data": {
            "level": "lite",
            "limits": [
                {
                    "type": "CREDIT_LIMIT",
                    "unit": 3,
                    "number": 5,
                    "usage": 2000,
                    "currentValue": 500,
                    "remaining": 1500,
                    "percentage": 25,
                    "nextResetTime": ms(NOW + timedelta(hours=2)),
                },
                {
                    "type": "CREDIT_LIMIT",
                    "unit": 6,
                    "number": 1,
                    "usage": 10000,
                    "currentValue": 8387,
                    "remaining": 1612,
                    "percentage": 83,
                    "nextResetTime": ms(NOW + timedelta(days=3)),
                },
            ],
        },
    }


def test_glm_keeps_the_providers_own_product_name() -> None:
    projection = normalize_zai_quota(glm_payload(), observed_at=NOW)

    # Not renamed to "GLM Token Plan" for symmetry with MiniMax: the owner
    # reconciles this against the Z.AI console.
    assert projection.plan.display_name == GLM_CODING_PLAN_DISPLAY_NAME
    assert projection.plan.plan_level == "lite"


def test_glm_five_hour_and_weekly_windows_stay_distinct() -> None:
    """The defect: both entries share a ``type``, so the first one won and
    weekly disappeared."""

    projection = normalize_zai_quota(glm_payload(), observed_at=NOW)

    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None
    assert weekly is not None
    assert five_hour.window_id != weekly.window_id
    assert five_hour.remaining_fraction == pytest.approx(0.75)
    assert weekly.remaining_fraction == pytest.approx(0.1612)
    assert five_hour.reset_at != weekly.reset_at


def test_glm_exact_counts_beat_the_rounded_percentage() -> None:
    """``percentage`` is integer-rounded; the counts are not.

    8387/10000 rounds to 83, so trusting the percentage would report 17.00%
    remaining instead of the true 16.12%.
    """

    projection = normalize_zai_quota(glm_payload(), observed_at=NOW)
    weekly = projection.window(QuotaWindowKind.WEEKLY)

    assert weekly is not None
    assert weekly.confidence is EvidenceConfidence.EXACT
    assert weekly.remaining_fraction == pytest.approx(0.1612)
    assert weekly.remaining_fraction != pytest.approx(0.17)
    assert weekly.remaining_units == pytest.approx(1612)
    assert weekly.used_units == pytest.approx(8387)


def test_glm_models_share_one_pool_and_own_no_balance() -> None:
    projection = normalize_zai_quota(
        glm_payload(),
        observed_at=NOW,
        covered_model_ids=("GLM-5.3", "GLM-5.3-Flash"),
    )

    assert projection.plan.quota_semantics is PlanQuotaSemantics.SHARED_POOL
    assert projection.pool.shared_across_models is True
    assert projection.pool.resource_kind is QuotaResourceKind.CODING_PLAN_USAGE_POOL
    assert set(projection.covered_model_ids()) == {"GLM-5.3", "GLM-5.3-Flash"}
    # Two models, one pool: exactly one set of windows, not one set per model.
    assert len(projection.windows) == 2


def test_glm_model_token_usage_never_becomes_a_model_quota() -> None:
    """Consumption and entitlement are different facts in different units."""

    usage = {
        "data": {
            "x_time": ["2026-09-01 21:00", "2026-09-01 22:00"],
            "modelDataList": [
                {
                    "modelName": "GLM-5.3",
                    "tokensUsage": [16835950, 120],
                    "totalTokens": 42440798,
                }
            ],
        }
    }

    observations = normalize_zai_model_usage(usage, observed_at=NOW)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.model_id == "GLM-5.3"
    # The windowed series is summed; the provider's larger lifetime total is
    # deliberately not mixed in.
    assert observation.consumed_units == pytest.approx(16836070)
    assert observation.unit_kind is ConsumptionUnitKind.TOKENS
    # The type has no remaining/entitlement field at all, so a per-model
    # balance cannot be derived from it even by mistake.
    assert not hasattr(observation, "remaining_fraction")
    assert not hasattr(observation, "remaining_units")


def test_glm_model_consumption_does_not_overwrite_plan_remaining() -> None:
    projection = normalize_zai_quota(glm_payload(), observed_at=NOW)
    weekly_before = projection.window(QuotaWindowKind.WEEKLY)
    assert weekly_before is not None

    with_usage = projection.model_copy(
        update={
            "model_consumption": normalize_zai_model_usage(
                {
                    "data": {
                        "modelDataList": [{"modelName": "GLM-5.3", "tokensUsage": [999_999_999]}]
                    }
                },
                observed_at=NOW,
            )
        }
    )

    weekly_after = with_usage.window(QuotaWindowKind.WEEKLY)
    assert weekly_after is not None
    assert weekly_after.remaining_fraction == weekly_before.remaining_fraction
    assert with_usage.confidence is projection.confidence


def test_glm_consumption_multiplier_stays_unknown_without_authority() -> None:
    """The pool is metered in credits, model usage in tokens.

    The provider publishes no conversion, so none is inferred — the units are
    simply kept apart.
    """

    projection = normalize_zai_quota(glm_payload(), observed_at=NOW)
    observations = normalize_zai_model_usage(
        {"data": {"modelDataList": [{"modelName": "GLM-5.3", "tokensUsage": [10]}]}},
        observed_at=NOW,
    )

    assert projection.pool.unit_kind is ConsumptionUnitKind.PLAN_CREDITS
    assert observations[0].unit_kind is ConsumptionUnitKind.TOKENS
    assert projection.pool.unit_kind is not observations[0].unit_kind


def test_glm_unreadable_plan_still_reports_a_reason() -> None:
    projection = normalize_zai_quota({"data": {"limits": []}}, observed_at=NOW)

    assert projection.confidence is EvidenceConfidence.UNKNOWN
    assert projection.state is QuotaState.UNKNOWN
    assert projection.unknown_reason == "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"


def test_glm_monthly_mcp_allowance_is_not_the_coding_pool() -> None:
    payload = glm_payload()
    payload["data"]["limits"].append(
        {"type": "TIME_LIMIT", "usage": 100, "currentValue": 40, "remaining": 60}
    )

    projection = normalize_zai_quota(payload, observed_at=NOW)
    monthly = projection.window(QuotaWindowKind.MONTHLY)

    assert monthly is not None
    assert monthly.window_id == "monthly-mcp"
    assert monthly.remaining_fraction == pytest.approx(0.6)
    # It is a distinct resource of the same plan, so it never replaces the
    # 5-hour or weekly coding windows.
    assert projection.window(QuotaWindowKind.FIVE_HOUR) is not None
    assert projection.window(QuotaWindowKind.WEEKLY) is not None


# ---------------------------------------------------------------------------
# MiniMax Token Plan
# ---------------------------------------------------------------------------


def minimax_agreeing_payload() -> dict:
    return {
        "model_remains": [
            {
                "model": "MiniMax-M3",
                "current_interval_remaining_percent": 63,
                "current_weekly_remaining_percent": 81,
                "start_time": ms(NOW - timedelta(hours=1)),
                "end_time": ms(NOW + timedelta(hours=4)),
                "weekly_start_time": ms(NOW - timedelta(days=2)),
                "weekly_end_time": ms(NOW + timedelta(days=5)),
            },
            {
                "model": "MiniMax-M2.7",
                "current_interval_remaining_percent": 63,
                "current_weekly_remaining_percent": 81,
                "start_time": ms(NOW - timedelta(hours=1)),
                "end_time": ms(NOW + timedelta(hours=4)),
                "weekly_start_time": ms(NOW - timedelta(days=2)),
                "weekly_end_time": ms(NOW + timedelta(days=5)),
            },
        ]
    }


def test_minimax_models_share_one_pool() -> None:
    projection = normalize_minimax_quota(
        minimax_agreeing_payload(),
        observed_at=NOW,
        known_model_ids=frozenset({"MiniMax-M3", "MiniMax-M2.7"}),
    )

    assert projection.plan.display_name == "MiniMax Token Plan"
    assert projection.plan.quota_semantics is PlanQuotaSemantics.SHARED_POOL
    assert projection.pool.shared_across_models is True
    assert projection.covered_model_ids() == ("MiniMax-M3", "MiniMax-M2.7")
    # Two models sharing one bar produce one pool with two windows — not two
    # pools, and not four windows.
    assert len(projection.windows) == 2


def test_minimax_differing_model_views_create_no_second_plan_balance() -> None:
    payload = minimax_agreeing_payload()
    payload["model_remains"][1]["current_interval_remaining_percent"] = 95

    projection = normalize_minimax_quota(payload, observed_at=NOW)

    # Still exactly one pool and one set of windows.
    assert len(projection.windows) == 2
    assert projection.pool.pool_id == "minimax-token-plan-cn"
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    assert five_hour is not None
    # The 5-hour figure is genuinely underivable from disagreeing views, so it
    # is UNKNOWN rather than averaged into a plausible-looking number.
    assert five_hour.remaining_fraction is None
    # Every provider view survives as an equivalent, labelled as such.
    assert len(projection.model_equivalents) == 4
    assert sorted(round(view.remaining_fraction, 4) for view in projection.model_equivalents) == [
        0.63,
        0.81,
        0.81,
        0.95,
    ]


def test_minimax_five_hour_and_weekly_remain_distinct() -> None:
    projection = normalize_minimax_quota(minimax_agreeing_payload(), observed_at=NOW)

    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None and weekly is not None
    assert five_hour.remaining_fraction == pytest.approx(0.63)
    assert weekly.remaining_fraction == pytest.approx(0.81)
    assert five_hour.duration_seconds != weekly.duration_seconds


def test_minimax_plan_level_counts_outrank_per_model_percentages() -> None:
    """Plan-level counts describe the bar directly, not through a model's lens."""

    payload = {
        "data": {
            "current_interval_total_count": 5_000_000,
            "current_interval_usage_count": 1_250_000,
            "current_interval_reset_time": "2026-09-02T21:00:00+08:00",
            "current_weekly_total_count": 35_000_000,
            "current_weekly_usage_count": 7_000_000,
            "current_weekly_reset_time": "2026-09-06T00:00:00+08:00",
            # A per-model percentage that contradicts the plan-level counts.
            "model_remains": [{"model": "MiniMax-M3", "current_interval_remaining_percent": 12}],
        }
    }

    projection = normalize_minimax_quota(payload, observed_at=NOW)
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)

    assert five_hour is not None
    assert five_hour.remaining_fraction == pytest.approx(0.75)
    assert five_hour.confidence is EvidenceConfidence.EXACT


def test_minimax_reset_time_without_a_zone_is_rejected_not_assumed_utc() -> None:
    payload = {
        "data": {
            "current_interval_total_count": 100,
            "current_interval_usage_count": 25,
            "current_interval_reset_time": "2026-09-02T21:00:00",
            "model_remains": [{"model": "MiniMax-M3"}],
        }
    }

    projection = normalize_minimax_quota(payload, observed_at=NOW)
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)

    assert five_hour is not None
    # The remaining figure is still exact; only the ambiguous countdown is dropped.
    assert five_hour.remaining_fraction == pytest.approx(0.75)
    assert five_hour.reset_at is None


def test_shared_plan_stays_unknown_when_no_authoritative_balance_exists() -> None:
    projection = normalize_minimax_quota({"model_remains": []}, observed_at=NOW)

    assert projection.confidence is EvidenceConfidence.UNKNOWN
    assert projection.has_shared_pool_truth is False
    assert projection.unknown_reason == "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"
    for window in projection.windows:
        assert window.remaining_fraction is None


# ---------------------------------------------------------------------------
# Binding window
# ---------------------------------------------------------------------------


def _window(
    window_id: str,
    kind: QuotaWindowKind,
    fraction: float | None,
    *,
    reset_at: datetime | None = None,
    confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
) -> QuotaWindowSnapshot:
    return QuotaWindowSnapshot(
        window_id=window_id,
        window_kind=kind,
        duration_seconds=3600.0,
        remaining_fraction=fraction,
        reset_at=reset_at,
        confidence=confidence,
        source=EvidenceSource(
            source_type=EvidenceSourceType.PROVIDER_API,
            observed_at=NOW,
            confidence=confidence,
        ),
    )


def test_binding_window_is_the_scarcest_comparable_window() -> None:
    binding = determine_binding_window(
        (
            _window("5h", QuotaWindowKind.FIVE_HOUR, 0.20),
            _window("weekly", QuotaWindowKind.WEEKLY, 0.68),
        ),
        at=NOW,
    )

    assert binding.window_id == "5h"
    assert binding.reason is BindingWindowReason.SCARCEST_COMPARABLE_WINDOW


def test_reset_horizon_is_reported_separately_from_scarcity() -> None:
    """20% left resetting in 30 minutes and 20% left resetting in six days are
    equally scarce and not equally urgent."""

    binding = determine_binding_window(
        (
            _window(
                "weekly",
                QuotaWindowKind.WEEKLY,
                0.20,
                reset_at=NOW + timedelta(days=6),
            ),
            _window(
                "5h",
                QuotaWindowKind.FIVE_HOUR,
                0.68,
                reset_at=NOW + timedelta(minutes=30),
            ),
        ),
        at=NOW,
    )

    assert binding.window_id == "weekly"
    assert binding.remaining_fraction == pytest.approx(0.20)
    assert binding.seconds_until_reset == pytest.approx(6 * 24 * 3600)


def test_unreadable_window_makes_the_binding_answer_estimated() -> None:
    """The scarcest *readable* window may not be the scarcest window."""

    binding = determine_binding_window(
        (
            _window("5h", QuotaWindowKind.FIVE_HOUR, 0.68),
            _window(
                "weekly",
                QuotaWindowKind.WEEKLY,
                None,
                confidence=EvidenceConfidence.UNKNOWN,
            ),
        ),
        at=NOW,
    )

    assert binding.window_id == "5h"
    assert binding.confidence is EvidenceConfidence.ESTIMATED


def test_no_readable_window_yields_no_binding_claim() -> None:
    binding = determine_binding_window(
        (
            _window(
                "5h",
                QuotaWindowKind.FIVE_HOUR,
                None,
                confidence=EvidenceConfidence.UNKNOWN,
            ),
        ),
        at=NOW,
    )

    assert binding.window_id is None
    assert binding.reason is BindingWindowReason.NO_COMPARABLE_WINDOWS


# ---------------------------------------------------------------------------
# Domain-model guardrails
# ---------------------------------------------------------------------------


def _projection(**overrides) -> dict:
    base = {
        "plan": PlanQuota(
            provider_id="p",
            plan_id="plan",
            display_name="Plan",
            quota_semantics=PlanQuotaSemantics.SHARED_POOL,
            observed_at=NOW,
        ),
        "pool": SharedQuotaPool(pool_id="pool", provider_id="p", plan_id="plan"),
        "source": EvidenceSource(source_type=EvidenceSourceType.PROVIDER_API, observed_at=NOW),
    }
    return {**base, **overrides}


def test_model_scoped_semantics_cannot_be_claimed_for_a_shared_pool() -> None:
    """MODEL_SCOPED is what would authorize per-model balances.

    Asserting it while the pool is shared is the exact contradiction that
    produces fabricated per-model quota, so the type refuses to be built.
    """

    with pytest.raises(ValidationError, match="MODEL_SCOPED"):
        PlanQuotaProjection(
            **_projection(
                plan=PlanQuota(
                    provider_id="p",
                    plan_id="plan",
                    display_name="Plan",
                    quota_semantics=PlanQuotaSemantics.MODEL_SCOPED,
                    observed_at=NOW,
                )
            )
        )


def test_consumption_must_reference_the_pool_it_was_measured_against() -> None:
    with pytest.raises(ValidationError, match="must reference this pool"):
        PlanQuotaProjection(
            **_projection(
                model_consumption=(
                    ModelConsumptionObservation(
                        provider_id="p",
                        plan_id="plan",
                        pool_id="some-other-pool",
                        model_id="m",
                        observed_at=NOW,
                        consumed_units=1.0,
                    ),
                )
            )
        )


def test_unknown_confidence_cannot_smuggle_a_precise_value() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN confidence"):
        QuotaWindowSnapshot(
            window_id="5h",
            window_kind=QuotaWindowKind.FIVE_HOUR,
            remaining_fraction=0.5,
            confidence=EvidenceConfidence.UNKNOWN,
            source=EvidenceSource(source_type=EvidenceSourceType.PROVIDER_API, observed_at=NOW),
        )


def test_minimax_resource_categories_are_not_reported_as_models() -> None:
    """The finding that made this distinction necessary.

    MiniMax calls the array ``model_remains``, but on the observed account its
    entries are named ``general`` and ``video`` while the account's routable
    models are ``MiniMax-M2.7`` and similar. Listing "video" among the models
    sharing this quota would send the owner looking for a model that does not
    exist.
    """

    payload = {
        "model_remains": [
            {
                "model_name": "general",
                "current_interval_remaining_percent": 96,
                "current_weekly_remaining_percent": 59,
            },
            {
                "model_name": "video",
                "current_interval_remaining_percent": 100,
                "current_weekly_remaining_percent": 100,
            },
        ]
    }

    projection = normalize_minimax_quota(
        payload,
        observed_at=NOW,
        known_model_ids=frozenset({"MiniMax-M2.7", "MiniMax-M3"}),
    )

    assert projection.covered_model_ids() == ()
    kinds = {view.scope_id: view.scope_kind for view in projection.model_equivalents}
    assert kinds == {
        "general": EquivalentScopeKind.PROVIDER_RESOURCE_SCOPE,
        "video": EquivalentScopeKind.PROVIDER_RESOURCE_SCOPE,
    }


def test_an_entry_matching_the_catalog_is_reported_as_a_model() -> None:
    payload = {
        "model_remains": [{"model": "MiniMax-M2.7", "current_interval_remaining_percent": 63}]
    }

    projection = normalize_minimax_quota(
        payload, observed_at=NOW, known_model_ids=frozenset({"MiniMax-M2.7"})
    )

    assert projection.covered_model_ids() == ("MiniMax-M2.7",)
    assert projection.model_equivalents[0].scope_kind is EquivalentScopeKind.MODEL


def test_without_a_catalog_no_entry_is_promoted_to_a_model() -> None:
    """Absent catalog evidence, an entry stays unclassified rather than assumed."""

    projection = normalize_minimax_quota(
        {"model_remains": [{"model": "MiniMax-M2.7", "current_interval_remaining_percent": 63}]},
        observed_at=NOW,
    )

    assert projection.covered_model_ids() == ()
    assert projection.model_equivalents[0].scope_kind is EquivalentScopeKind.UNKNOWN
