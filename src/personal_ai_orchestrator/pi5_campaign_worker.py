"""Fail-closed worker-role parsing for PI-5B3G campaign instrumentation."""

from __future__ import annotations

import re

from personal_ai_orchestrator.pi5_identity import CampaignExecutionRole

_CAMPAIGN_PARENT_TASK_ID = re.compile(r"^pi5b3g-[0-9a-f]{32}-obs[1-3]-parent-task$")
_DELEGATED_CHILD_TASK_ID = re.compile(r"^pi5-child-[0-9a-f]{20}$")


def campaign_worker_role(task_id: str) -> CampaignExecutionRole:
    """Classify only host-generated campaign-scoped parent/child task IDs."""

    if _CAMPAIGN_PARENT_TASK_ID.fullmatch(task_id) is not None:
        return CampaignExecutionRole.PARENT
    if _DELEGATED_CHILD_TASK_ID.fullmatch(task_id) is not None:
        return CampaignExecutionRole.CHILD
    raise ValueError("task_id is not a host-generated PI-5B3G worker identity")


__all__ = ["campaign_worker_role"]
