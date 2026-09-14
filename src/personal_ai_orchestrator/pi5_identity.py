"""Host-owned deterministic identities for bounded PI-5 delegation campaigns."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

PI5B3G_CAMPAIGN_IDENTITY_VERSION = "pi5b3g-campaign-identity-v1"
PI5B3G_MIN_OBSERVATION = 1
PI5B3G_MAX_OBSERVATION = 3

_CAMPAIGN_ID = re.compile(r"^delegation-campaign-([0-9a-f]{32})$")
_CANONICAL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CampaignExecutionRole(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class CampaignExecutionOperation(StrEnum):
    TASK = "task"
    SUBMIT = "submit"
    DISPATCH = "dispatch"
    DELEGATION = "delegation"
    TIMEOUT_CANCEL = "timeout-cancel"


def _validated_identifier(value: str) -> str:
    if _CANONICAL_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("generated campaign execution identity is invalid")
    return value


def delegation_child_task_id(
    *, parent_task_id: str, parent_run_id: str, ordinal: int
) -> str:
    """Mint the stable child task ID from host-bound parent identity."""

    if not parent_task_id or not parent_run_id:
        raise ValueError("parent task and run identities are required")
    if not 1 <= ordinal <= 3:
        raise ValueError("delegation ordinal is out of range")
    digest = hashlib.sha256(
        f"{parent_task_id}\x00{parent_run_id}\x00{ordinal}".encode()
    ).hexdigest()[:20]
    return _validated_identifier(f"pi5-child-{digest}")


def delegation_child_submit_request_id(child_task_id: str) -> str:
    """Return the canonical task-submission idempotency key for one child."""

    return _validated_identifier(f"pi5-child-submit-{child_task_id}")


def delegation_child_dispatch_request_id(child_task_id: str) -> str:
    """Return the canonical dispatch idempotency key for one child."""

    return _validated_identifier(f"pi5-child-dispatch-{child_task_id}")


def dispatch_id_for_request(request_id: str) -> str:
    return _validated_identifier(f"owner-dispatch-{request_id}")


def run_id_for_dispatch(dispatch_id: str) -> str:
    return _validated_identifier(f"run-{dispatch_id}")


@dataclass(frozen=True)
class CampaignChildExecutionIdentities:
    delegation_id: str
    task_id: str
    submit_request_id: str
    dispatch_request_id: str
    dispatch_id: str
    run_id: str

    def values(self) -> tuple[str, ...]:
        return (
            self.delegation_id,
            self.task_id,
            self.submit_request_id,
            self.dispatch_request_id,
            self.dispatch_id,
            self.run_id,
        )


@dataclass(frozen=True)
class CampaignObservationExecutionIdentities:
    campaign_id: str
    observation_number: int
    parent_task_id: str
    parent_submit_request_id: str
    parent_dispatch_request_id: str
    parent_dispatch_id: str
    parent_run_id: str
    parent_timeout_cancel_request_id: str

    def child(self, *, ordinal: int = 1) -> CampaignChildExecutionIdentities:
        if not 1 <= ordinal <= 3:
            raise ValueError("delegation ordinal is out of range")
        child_task_id = delegation_child_task_id(
            parent_task_id=self.parent_task_id,
            parent_run_id=self.parent_run_id,
            ordinal=ordinal,
        )
        token = CampaignExecutionIdentityFactory.campaign_token(self.campaign_id)
        delegation_id = _validated_identifier(
            f"pi5b3g-{token}-obs{self.observation_number}-child-delegation-{ordinal}"
        )
        dispatch_request_id = delegation_child_dispatch_request_id(child_task_id)
        dispatch_id = dispatch_id_for_request(dispatch_request_id)
        return CampaignChildExecutionIdentities(
            delegation_id=delegation_id,
            task_id=child_task_id,
            submit_request_id=delegation_child_submit_request_id(child_task_id),
            dispatch_request_id=dispatch_request_id,
            dispatch_id=dispatch_id,
            run_id=run_id_for_dispatch(dispatch_id),
        )

    def parent_values(self) -> tuple[str, ...]:
        return (
            self.parent_task_id,
            self.parent_submit_request_id,
            self.parent_dispatch_request_id,
            self.parent_dispatch_id,
            self.parent_run_id,
            self.parent_timeout_cancel_request_id,
        )


class CampaignExecutionIdentityFactory:
    """Derive stable PI-5B3G identities from one authoritative campaign ID."""

    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = campaign_id
        self._campaign_token = self.campaign_token(campaign_id)

    @staticmethod
    def campaign_token(campaign_id: str) -> str:
        match = _CAMPAIGN_ID.fullmatch(campaign_id)
        if match is None:
            raise ValueError("campaign_id is not an authoritative delegation campaign ID")
        return match.group(1)

    def _identity(
        self,
        observation_number: int,
        role: CampaignExecutionRole,
        operation: CampaignExecutionOperation,
    ) -> str:
        if not PI5B3G_MIN_OBSERVATION <= observation_number <= PI5B3G_MAX_OBSERVATION:
            raise ValueError("observation number is outside the authorized PI-5B3G range")
        if not isinstance(role, CampaignExecutionRole):
            raise ValueError("unknown campaign execution role")
        if not isinstance(operation, CampaignExecutionOperation):
            raise ValueError("unknown campaign execution operation")
        return _validated_identifier(
            f"pi5b3g-{self._campaign_token}-obs{observation_number}-{role.value}-{operation.value}"
        )

    def observation(self, observation_number: int) -> CampaignObservationExecutionIdentities:
        parent_task_id = self._identity(
            observation_number,
            CampaignExecutionRole.PARENT,
            CampaignExecutionOperation.TASK,
        )
        parent_submit_request_id = self._identity(
            observation_number,
            CampaignExecutionRole.PARENT,
            CampaignExecutionOperation.SUBMIT,
        )
        parent_dispatch_request_id = self._identity(
            observation_number,
            CampaignExecutionRole.PARENT,
            CampaignExecutionOperation.DISPATCH,
        )
        parent_dispatch_id = dispatch_id_for_request(parent_dispatch_request_id)
        return CampaignObservationExecutionIdentities(
            campaign_id=self.campaign_id,
            observation_number=observation_number,
            parent_task_id=parent_task_id,
            parent_submit_request_id=parent_submit_request_id,
            parent_dispatch_request_id=parent_dispatch_request_id,
            parent_dispatch_id=parent_dispatch_id,
            parent_run_id=run_id_for_dispatch(parent_dispatch_id),
            parent_timeout_cancel_request_id=self._identity(
                observation_number,
                CampaignExecutionRole.PARENT,
                CampaignExecutionOperation.TIMEOUT_CANCEL,
            ),
        )


__all__ = [
    "CampaignChildExecutionIdentities",
    "CampaignExecutionIdentityFactory",
    "CampaignExecutionOperation",
    "CampaignExecutionRole",
    "CampaignObservationExecutionIdentities",
    "PI5B3G_CAMPAIGN_IDENTITY_VERSION",
    "delegation_child_dispatch_request_id",
    "delegation_child_submit_request_id",
    "delegation_child_task_id",
    "dispatch_id_for_request",
    "run_id_for_dispatch",
]
