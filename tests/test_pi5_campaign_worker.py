from __future__ import annotations

import pytest

from personal_ai_orchestrator.pi5_campaign_worker import campaign_worker_role
from personal_ai_orchestrator.pi5_identity import (
    CampaignExecutionIdentityFactory,
    CampaignExecutionRole,
)

CAMPAIGN = "delegation-campaign-11111111111111111111111111111111"


def test_campaign_worker_role_uses_authoritative_identity_grammar() -> None:
    observation = CampaignExecutionIdentityFactory(CAMPAIGN).observation(1)
    assert campaign_worker_role(observation.parent_task_id) is CampaignExecutionRole.PARENT
    assert campaign_worker_role(observation.child().task_id) is CampaignExecutionRole.CHILD


def test_campaign_scoped_parent_does_not_depend_on_historical_prefix() -> None:
    parent_task_id = CampaignExecutionIdentityFactory(CAMPAIGN).observation(1).parent_task_id
    assert not parent_task_id.startswith("pi5b3g-obs")
    assert campaign_worker_role(parent_task_id) is CampaignExecutionRole.PARENT


@pytest.mark.parametrize(
    "task_id",
    [
        "pi5b3g-obs1-parent",
        "pi5b3g-11111111111111111111111111111111-obs4-parent-task",
        "pi5b3g-11111111111111111111111111111111-obs1-child-task",
        "arbitrary-task",
    ],
)
def test_unknown_campaign_worker_identity_fails_closed(task_id: str) -> None:
    with pytest.raises(ValueError, match="host-generated"):
        campaign_worker_role(task_id)
