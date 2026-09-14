from __future__ import annotations

import re

import pytest

from personal_ai_orchestrator.pi5_identity import (
    PI5B3G_CAMPAIGN_IDENTITY_VERSION,
    CampaignExecutionIdentityFactory,
)

CAMPAIGN_A = "delegation-campaign-11111111111111111111111111111111"
CAMPAIGN_B = "delegation-campaign-22222222222222222222222222222222"
CANONICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _all_values(factory: CampaignExecutionIdentityFactory) -> tuple[str, ...]:
    values: list[str] = []
    for number in range(1, 4):
        observation = factory.observation(number)
        values.extend(observation.parent_values())
        values.extend(observation.child().values())
    return tuple(values)


def test_identity_scheme_version_is_frozen() -> None:
    assert PI5B3G_CAMPAIGN_IDENTITY_VERSION == "pi5b3g-campaign-identity-v1"


def test_same_campaign_reconstruction_is_idempotent() -> None:
    first = CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(1)
    second = CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(1)
    assert first == second
    assert first.child() == second.child()


def test_new_campaign_same_observation_is_unique() -> None:
    first = CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(1)
    second = CampaignExecutionIdentityFactory(CAMPAIGN_B).observation(1)
    assert set(first.parent_values()).isdisjoint(second.parent_values())
    assert set(first.child().values()).isdisjoint(second.child().values())


def test_observations_roles_and_operations_are_unique() -> None:
    factory = CampaignExecutionIdentityFactory(CAMPAIGN_A)
    all_values = _all_values(factory)
    assert len(all_values) == len(set(all_values))
    assert len({factory.observation(number).parent_task_id for number in range(1, 4)}) == 3


def test_two_complete_campaigns_have_no_collision() -> None:
    first = set(_all_values(CampaignExecutionIdentityFactory(CAMPAIGN_A)))
    second = set(_all_values(CampaignExecutionIdentityFactory(CAMPAIGN_B)))
    assert first.isdisjoint(second)


def test_historical_static_identifiers_cannot_collide() -> None:
    generated = set(_all_values(CampaignExecutionIdentityFactory(CAMPAIGN_A)))
    assert generated.isdisjoint(
        {
            "pi5b3g-obs1-parent",
            "pi5b3g-obs1-submit",
            "pi5b3g-obs1-dispatch",
            "pi5b3g-obs1-timeout-cancel",
        }
    )


def test_all_generated_identifiers_fit_canonical_api_contract() -> None:
    values = _all_values(CampaignExecutionIdentityFactory(CAMPAIGN_A))
    assert all(CANONICAL_ID.fullmatch(value) for value in values)
    assert max(map(len, values)) <= 128


@pytest.mark.parametrize(
    "campaign_id",
    [
        "",
        "campaign-11111111111111111111111111111111",
        "delegation-campaign-short",
        "delegation-campaign-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "delegation-campaign-11111111111111111111111111111111-extra",
    ],
)
def test_invalid_or_non_authoritative_campaign_id_fails_closed(campaign_id: str) -> None:
    with pytest.raises(ValueError, match="authoritative"):
        CampaignExecutionIdentityFactory(campaign_id)


@pytest.mark.parametrize("observation", [0, 4, -1, 100])
def test_out_of_range_observation_fails_closed(observation: int) -> None:
    with pytest.raises(ValueError, match="authorized"):
        CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(observation)


def test_child_ordinal_is_stable_and_distinct() -> None:
    observation = CampaignExecutionIdentityFactory(CAMPAIGN_A).observation(1)
    first = observation.child(ordinal=1)
    second = observation.child(ordinal=2)
    assert first == observation.child(ordinal=1)
    assert set(first.values()).isdisjoint(second.values())
