"""Replayable PI-5B3 SHADOW evidence for delegation decisions.

This module is deliberately observational. It converts host-owned routing/quota
truth into the pure PI-5B3A policy contract and persists a sanitized immutable
record. It never launches a child, changes target selection, or makes a shadow
verdict authoritative.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from personal_ai_orchestrator.delegation_evidence import (
    DelegationCommercialMode,
    DelegationHostEvidence,
    build_delegation_host_evidence,
)
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


class DelegationShadowRecord(RegistryModel):
    """Sanitized immutable input + output needed to replay one SHADOW decision."""

    schema_version: int = Field(default=2, ge=1)
    observation_id: str = Field(min_length=1)
    observed_at: datetime
    parent_task_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    child_task_id: str = Field(min_length=1)
    parent_execution_target_id: str | None = None
    selected_child_execution_target_id: str | None = None
    parent_quota_pool_id: str | None = None
    child_quota_pool_id: str | None = None
    host_evidence: DelegationHostEvidence | None = None
    evidence_sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    enforcement_ready: bool = False
    facts: DelegationPolicyInput
    decision: DelegationDecision

    @model_validator(mode="after")
    def validate_shadow_contract(self) -> DelegationShadowRecord:
        if self.decision.mode is not DelegationPolicyMode.SHADOW:
            raise ValueError("delegation shadow record must use SHADOW mode")
        if self.enforcement_ready and self.limitations:
            raise ValueError("limited shadow evidence cannot be enforcement-ready")
        return self

    def replay(self) -> DelegationDecision:
        """Re-evaluate the versioned structured facts using SHADOW semantics."""
        if self.decision.policy_version != PI5B3A_POLICY_VERSION:
            raise ValueError("unsupported delegation shadow policy version")
        return evaluate_delegation(self.facts, mode=DelegationPolicyMode.SHADOW)

    def replay_matches(self) -> bool:
        return self.replay().model_dump(mode="json") == self.decision.model_dump(
            mode="json"
        )


class DelegationShadowJournal:
    """Append-only first-observation store with atomic 0600 writes."""

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
        ).encode()
        return f"delegation-shadow-{sha256(payload).hexdigest()[:24]}"

    def path_for(self, observation_id: str) -> Path:
        if not observation_id or any(
            part in observation_id for part in ("/", "\\", "..")
        ):
            raise ValueError("unsafe delegation shadow observation id")
        return self.directory / f"{observation_id}.json"

    def append(self, record: DelegationShadowRecord) -> Path:
        target = self.path_for(record.observation_id)
        if target.exists():
            # One child in one parent run has one initial SHADOW decision. Re-entry
            # returns that first observation rather than silently time-shifting the
            # evidence after quota/recommendation state may have changed.
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
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=self.directory,
        )
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
        return DelegationShadowRecord.model_validate_json(
            target.read_text(encoding="utf-8")
        )

    def records_for_parent_run(
        self,
        parent_run_id: str,
    ) -> tuple[DelegationShadowRecord, ...]:
        if not self.directory.exists():
            return ()
        records: list[DelegationShadowRecord] = []
        for path in sorted(self.directory.glob("delegation-shadow-*.json")):
            record = DelegationShadowRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if record.parent_run_id == parent_run_id:
                records.append(record)
        return tuple(records)


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
    task_profile_provider: Callable[[str], Any | None] | None = None,
    policy_resolution_provider: Callable[[Any, Any], Any | None] | None = None,
    host_required_delegation: bool | None = None,
    independence_required: bool | None = None,
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
    remaining = () if child_candidate is None else child_candidate.remaining_fractions
    availability_state = (
        None
        if child_candidate is None
        else child_candidate.availability_state.value
    )

    parent_profile = (
        None
        if task_profile_provider is None
        else task_profile_provider(parent_task_id)
    )
    child_profile = (
        None
        if task_profile_provider is None
        else task_profile_provider(child_task_id)
    )
    try:
        parent_task = store.get_task(parent_task_id)
    except KeyError:
        parent_task = None
    policy_resolution = (
        None
        if policy_resolution_provider is None or parent_task is None
        else policy_resolution_provider(store, parent_task)
    )

    host_evidence = build_delegation_host_evidence(
        registry=registry,
        parent_profile=parent_profile,
        child_profile=child_profile,
        policy_resolution=policy_resolution,
        parent_execution_target_id=parent_target,
        child_execution_target_id=child_target,
        child_remaining_fractions=remaining,
        child_observed_availability_state=availability_state,
        now=now,
        host_required_delegation=host_required_delegation,
        independence_required=independence_required,
    )

    parent_provider = _provider_id_for_target(registry, parent_target)
    different_provider_available = (
        any(
            evaluation.admitted
            and _provider_id_for_target(registry, evaluation.execution_target_id)
            not in (None, parent_provider)
            for evaluation in recommendation.evaluations
        )
        if parent_provider is not None
        else False
    )

    resolved_policy = host_evidence.resolved_policy
    commercial_mode = host_evidence.child_commercial_mode
    metered_subscription = commercial_mode in {
        DelegationCommercialMode.SUBSCRIPTION,
        DelegationCommercialMode.PREPAID,
    }
    # B3B already treated an admitted target with observed quota fractions as
    # metered and fail-closed on a missing burn estimate. B3C may learn that the
    # registry lacks enough plan lineage to name the commercial mode; that new
    # UNKNOWN limitation must not weaken the older conservative burn gate.
    observed_metered_unknown = (
        commercial_mode is DelegationCommercialMode.UNKNOWN and bool(remaining)
    )
    burn_gated_candidate = metered_subscription or observed_metered_unknown
    quota_truth_required = commercial_mode not in {
        DelegationCommercialMode.PAY_AS_YOU_GO,
        DelegationCommercialMode.UNMETERED,
    }
    quota_truth_known = (
        bool(remaining)
        or commercial_mode is DelegationCommercialMode.UNMETERED
        or commercial_mode is DelegationCommercialMode.PAY_AS_YOU_GO
    )
    require_burn = burn_gated_candidate and (
        True
        if resolved_policy is None
        else resolved_policy.require_burn_estimate_for_subscription
    )

    facts = DelegationPolicyInput(
        delegation_feature_enabled=True,
        host_required=host_required_delegation is True,
        failure_count=host_evidence.parent_failure_count or 0,
        failure_escalation_after=(
            resolved_policy.failure_escalation_after
            if resolved_policy is not None
            else 2
        ),
        eligible_child_count=sum(
            1 for item in recommendation.evaluations if item.admitted
        ),
        quota_truth_required=quota_truth_required,
        quota_truth_known=quota_truth_known,
        require_burn_estimate=require_burn,
        predicted_child_burn_fraction=host_evidence.predicted_child_burn_fraction,
        usable_child_headroom_fraction=(
            host_evidence.usable_child_headroom_fraction
        ),
        paid_usage_required=host_evidence.paid_usage_required is True,
        paid_usage_allowed=(
            False if resolved_policy is None else resolved_policy.allow_paid_usage
        ),
        independence_required=independence_required is True,
        different_provider_candidate_available=different_provider_available,
        same_quota_pool_as_parent=host_evidence.same_quota_pool_as_parent,
    )
    decision = evaluate_delegation(facts, mode=DelegationPolicyMode.SHADOW)

    sources = [
        "runs.worker_id",
        "dispatch_recommendation.evaluations",
        "dispatch_candidate.remaining_fractions",
        *host_evidence.evidence_sources,
    ]
    limitations = list(host_evidence.limitations)

    if (
        burn_gated_candidate
        and host_evidence.predicted_child_burn_fraction is None
        and "predicted_child_burn_unavailable" not in limitations
    ):
        limitations.append("predicted_child_burn_unavailable")

    return DelegationShadowRecord(
        schema_version=2,
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
        parent_quota_pool_id=host_evidence.parent_quota_pool_id,
        child_quota_pool_id=host_evidence.child_quota_pool_id,
        host_evidence=host_evidence,
        evidence_sources=tuple(dict.fromkeys(sources)),
        limitations=tuple(dict.fromkeys(limitations)),
        enforcement_ready=host_evidence.enforcement_ready and not limitations,
        facts=facts,
        decision=decision,
    )


__all__ = [
    "DelegationShadowJournal",
    "DelegationShadowRecord",
    "build_delegation_shadow_record",
]
