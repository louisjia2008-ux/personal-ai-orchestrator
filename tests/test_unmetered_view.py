"""M1 WP4 commit 4 — pool_kind + UnmeteredObservationView surface tests.

The card builder exposes ``pool_kind`` and an ``unmetered`` block
that carries read-time metrics for the unmetered providers. The
tests below pin the wire shape and the lenient defaults so
pre-WP4 fixtures continue to render the legacy chrome.
"""

from __future__ import annotations

import json

from personal_ai_orchestrator.control_api import (
    ExecutionTargetHealthView,
    QuotaProviderCardView,
    UnmeteredObservationView,
)


def test_execution_target_view_carries_pool_kind_and_auth_kind() -> None:
    """ExecutionTargetHealthView exposes ``pool_kind`` and ``auth_kind``."""

    view = ExecutionTargetHealthView(
        execution_target_id="opencode-big-pickle",
        model_sku_id="opencode-big-pickle",
        runtime_id="opencode",
        enabled=True,
        execution_verified=False,
        auth_kind="none",
        pool_kind="unmetered",
    )
    assert view.auth_kind == "none"
    assert view.pool_kind == "unmetered"
    # Lenient defaults preserve the pre-WP4 behaviour for windowed targets.
    windowed = ExecutionTargetHealthView(
        execution_target_id="zai-coding-plan-glm-5.3",
        model_sku_id="zai-coding-plan-glm-5.3",
        runtime_id="opencode",
        enabled=True,
        execution_verified=True,
    )
    assert windowed.auth_kind == "env"
    assert windowed.pool_kind == "windowed"


def test_quota_provider_card_view_carries_unmetered_block() -> None:
    """QuotaProviderCardView carries the read-time unmetered block when pool_kind=unmetered."""

    card = QuotaProviderCardView(
        provider_id="opencode",
        display_name="OpenCode Free",
        connection_state="CONNECTED",
        auth_state="AUTH_FROM_ENV_PRESENCE",
        quota_state="OBSERVED",
        confidence="ESTIMATED",
        pool_kind="unmetered",
        unmetered=UnmeteredObservationView(
            rpm_observed=12,
            error_rate_1h=0.05,
            cooldown_until=None,
        ),
    )
    assert card.pool_kind == "unmetered"
    assert card.unmetered is not None
    assert card.unmetered.rpm_observed == 12
    assert card.unmetered.error_rate_1h == 0.05
    assert card.unmetered.cooldown_until is None


def test_quota_provider_card_view_unmetered_block_defaults_to_none() -> None:
    """Windowed providers carry ``unmetered=None`` so the legacy chrome renders."""

    card = QuotaProviderCardView(
        provider_id="zai-coding-plan",
        display_name="GLM / Z.AI",
        connection_state="CONNECTED",
        auth_state="AUTH_FROM_ENV_PRESENCE",
        quota_state="OBSERVED",
        confidence="EXACT",
    )
    assert card.pool_kind == "windowed"
    assert card.unmetered is None


def test_unmetered_observation_view_round_trips_through_json() -> None:
    """UnmeteredObservationView JSON round-trip preserves every field."""

    observation = UnmeteredObservationView(
        rpm_observed=8,
        error_rate_1h=0.125,
        cooldown_until="2026-09-07T12:15:00+00:00",
    )
    rendered = observation.model_dump_json()
    parsed = json.loads(rendered)
    assert parsed["rpm_observed"] == 8
    assert parsed["error_rate_1h"] == 0.125
    assert parsed["cooldown_until"] == "2026-09-07T12:15:00+00:00"
    decoded = UnmeteredObservationView.model_validate_json(rendered)
    assert decoded == observation


def test_unmetered_observation_view_error_rate_none_means_no_data() -> None:
    """V3: ``error_rate_1h=None`` is the "no data" sentinel.

    A provider with no execution-evidence rows in the last hour
    must NOT default ``error_rate_1h`` to ``0.0`` — that value
    would be ambiguous (it means "ran and all-verified"). The
    daemon must emit ``null`` so the Swift card can render the
    "no data" hint instead of a fabricated 0% error rate.
    """

    # Construct via model_dump — same path the daemon takes.
    observation = UnmeteredObservationView(
        rpm_observed=0,
        error_rate_1h=None,
        cooldown_until=None,
    )
    rendered = observation.model_dump_json()
    assert '"error_rate_1h":null' in rendered
    parsed = json.loads(rendered)
    assert parsed["error_rate_1h"] is None
    # Round-trip preserves the None.
    decoded = UnmeteredObservationView.model_validate_json(rendered)
    assert decoded.error_rate_1h is None
    assert decoded.rpm_observed == 0
