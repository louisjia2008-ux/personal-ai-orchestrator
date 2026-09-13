"""Replayable PI-5B3 SHADOW evidence for delegation decisions.

This module is deliberately observational.  It converts host-owned routing/quota
truth into the pure PI-5B3A policy contract and persists a sanitized immutable
record.  It never launches a child, changes target selection, or makes a shadow
verdict authoritative.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import Field

from personal_ai_orchestrator.delegation_policy import (
    PI5B3A_POLICY_VERSION,
    DelegationDecision,
    DelegationPolicyInput,
    DelegationPolicyMode,
    evaluate_delegation,
)
from personal_ai_orchestrator.dispatch_recommender import (
    DispatchCandidateInput,
    DispatchRecommendation,
)
from personal_ai_orchestrator.model_registry import RegistryModel
from personal_ai_orchestrator.runtime_quota_routing import quota_pool_id_for_target
from personal_ai_orchestrator.scheduler import RoutingPolicy


class DelegationShadowRecord(RegistryModel):
    """Sanitized immutable input + output needed to replay one SHADOW decision."""

    schema_version: int = Field(default=1, ge=1)
    observation_id: str = Field(min_length=1)
    observed_at: datetime
    parent_task_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    child_task_id: str = Field(min_length=1)
    parent_execution_target_id: str | None = None
    selected_child_execution_target_id: str | None = None
    parent_quota_pool_id: str | None = None
    child_quota_pool_id: str | None = None
    evidence_sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    facts: DelegationPolicyInput
    decision: DelegationDecision

    def replay(self) -> DelegationDecision:
        """Re-evaluate the versioned structured facts using SHADOW semantics."""
        if self.decision.policy_version != PI5B3A_POLICY_VERSION:
            raise ValueError("unsupported delegation shadow policy version")
        if self.decision.mode is not DelegationPolicyMode.SHADOW:
            raise ValueError("delegation shadow record is not SHADOW mode")
        return evaluate_delegation(self.facts, mode=DelegationPolicyMode.SHADOW)

    def replay_matches(self) -> bool:
        return self.replay().model_dump(mode="json") == self.decision.model_dump(mode="json")


class DelegationShadowJournal:
    """Append-only per-child SHADOW record store with atomic 0600 writes."""

    def __init__(self, root: str | Path) -> None:
        self.directory = Path(root) / "delegation-shadow-history"

    @staticmethod
    def observation_id(*, parent_run_id: str, child_task_id: str) -> str:
        payload = json.dumps(
            {
                "policy_version": PI5B3A_POLICY_VERSION,
                "parent_run_id": parent_run_id,
                "child_task_id": child_task_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"delegation-shadow-{sha256(payload).hexdigest()[:24]}"

    def path_for(self, observation_id: str) -> Path:
        if not observation_id or any(part in observation_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe delegation shadow observation id")
        return self.directory / f"{observation_id}.json"

    def append(self, record: DelegationShadowRecord) -> Path:
        target = self.path_for(record.observation_id)
        if target.exists():
            existing = DelegationShadowRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            if (
                existing.parent_run_id != record.parent_run_id
                or existing.child_task_id != record.child_task_id
                or existing.decision.policy_version != record.decision.policy_version
            ):
                raise ValueError("delegation shadow id already belongs to another identity")
            return target

        self.directory.mkdir(parents=True, exist_ok=True)
        rendered = record.model_dump_json(indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(self, observation_id: str) -> DelegationShadowRecord | None:
        target = self.path_for(observation_id)
        if not target.exists():
            return None
        return DelegationShadowRecord.model_validate_json(target.read_text(encoding="utf-8"))


def _provider_id_for_target(registry: Any, target_id: str | None) -> str | None:
    if target_id is None:
        return None
    target = registry.execution_targets.get(target_id)
    if target is None:
        return None
    model = registry.models.get(target.model_sku_id)
    return None if model is None else model.provider_id


def build_delegation_shadow_record(
    *,
    store: Any,
    registry: Any,
    parent_task_id: str,
    parent_run_id: str,
    child_task_id: str,
    recommendation: DispatchRecommendation,
    candidates: list[DispatchCandidateInput],
    observed_at: datetime | None = None,
) -> DelegationShadowRecord:
    """Build one SHADOW record strictly from host-owned durable/routing evidence."""

    now = observed_at or datetime.now(UTC)
    run = store.connection.execute(
        "SELECT worker_id FROM runs WHERE run_id=? AND task_id=?",
        (parent_run_id, parent_task_id),
    ).fetchone()
    parent_target = None if run is None else run["worker_id"]

    by_target = {candidate.execution_target_id: candidate for candidate in candidates}
    pick = recommendation.top_pick
    child_target = None if pick is None else pick.execution_target_id
    child_candidate = None if child_target is None else by_target.get(child_target)

    parent_pool = (
        quota_pool_id_for_target(registry, execution_target_id=parent_target, now=now)
        if parent_target is not None
        else None
    )
    child_pool = (
        quota_pool_id_for_target(registry, execution_target_id=child_target, now=now)
        if child_target is not None
        else None
    )
    same_pool = (
        parent_pool == child_pool if parent_pool is not None and child_pool is not None else None
    )

    remaining = () if child_candidate is None else child_candidate.remaining_fractions
    headroom = min(remaining) if remaining else None
    predicted_burn = None if pick is None else pick.predicted_burn_fraction
    metered_candidate = bool(remaining)

    blocked_dispatches = store.connection.execute(
        "SELECT COUNT(*) FROM owner_dispatches WHERE task_id=? AND status='BLOCKED'",
        (parent_task_id,),
    ).fetchone()[0]

    parent_provider = _provider_id_for_target(registry, parent_target)
    different_provider_available = any(
        evaluation.admitted
        and _provider_id_for_target(registry, evaluation.execution_target_id)
        not in (None, parent_provider)
        for evaluation in recommendation.evaluations
    ) if parent_provider is not None else False

    routing_policy = RoutingPolicy()
    facts = DelegationPolicyInput(
        delegation_feature_enabled=True,
        host_required=False,
        failure_count=int(blocked_dispatches),
        failure_escalation_after=routing_policy.failure_escalation_after,
        eligible_child_count=sum(1 for item in recommendation.evaluations if item.admitted),
        quota_truth_required=True,
        quota_truth_known=bool(remaining),
        # Do not manufacture a per-task burn estimate.  Metered candidates keep
        # the hard requirement and will surface the missing evidence in SHADOW.
        require_burn_estimate=metered_candidate,
        predicted_child_burn_fraction=predicted_burn,
        usable_child_headroom_fraction=headroom,
        # The owner-dispatch recommendation does not yet expose reliable PAYG
        # requirements.  Do not guess one; record the limitation below.
        paid_usage_required=False,
        paid_usage_allowed=routing_policy.allow_paid_usage,
        independence_required=False,
        different_provider_candidate_available=different_provider_available,
        same_quota_pool_as_parent=same_pool,
    )
    decision = evaluate_delegation(facts, mode=DelegationPolicyMode.SHADOW)

    sources = [
        "runs.worker_id",
        "dispatch_recommendation.evaluations",
        "dispatch_candidate.remaining_fractions",
        "runtime_quota_routing.quota_pool_id_for_target",
        "owner_dispatches.blocked_count",
    ]
    limitations: list[str] = []
    if predicted_burn is None and metered_candidate:
        limitations.append("predicted_child_burn_unavailable")
    if parent_pool is None:
        limitations.append("parent_quota_pool_unresolved")
    if child_target is not None and child_pool is None:
        limitations.append("child_quota_pool_unresolved")
    limitations.append("paid_usage_requirement_not_exposed_by_dispatch_recommendation")

    return DelegationShadowRecord(
        observation_id=DelegationShadowJournal.observation_id(
            parent_run_id=parent_run_id,
            child_task_id=child_task_id,
        ),
        observed_at=now,
        parent_task_id=parent_task_id,
        parent_run_id=parent_run_id,
        child_task_id=child_task_id,
        parent_execution_target_id=parent_target,
        selected_child_execution_target_id=child_target,
        parent_quota_pool_id=parent_pool,
        child_quota_pool_id=child_pool,
        evidence_sources=tuple(sources),
        limitations=tuple(limitations),
        facts=facts,
        decision=decision,
    )


__all__ = [
    "DelegationShadowJournal",
    "DelegationShadowRecord",
    "build_delegation_shadow_record",
]
