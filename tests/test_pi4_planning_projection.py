from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from personal_ai_orchestrator.dispatch_recommendation_service import (
    DispatchRecommendationService,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    observe_exhaustion,
    observe_success,
)
from personal_ai_orchestrator.scheduler import RoutingObjective
from tests.quota_identity_fixtures import bind, snapshot


def _registry() -> ModelRegistry:
    minimax = Provider(id="minimax-cn-coding-plan", display_name="MiniMax CN")
    alternate = Provider(id="alternate-plan", display_name="Alternate")
    mm_account = Account(id=minimax.id, provider_id=minimax.id, label=minimax.display_name)
    alt_account = Account(id=alternate.id, provider_id=alternate.id, label=alternate.display_name)
    mm_model = ModelSKU(
        id="minimax-cn-coding-plan/MiniMax-M3",
        provider_id=minimax.id,
        display_name="MiniMax-M3",
    )
    alt_model = ModelSKU(
        id="alternate-plan/worker",
        provider_id=alternate.id,
        display_name="worker",
    )
    targets = {
        "minimax-cn-coding-plan-MiniMax-M3": ExecutionTarget(
            id="minimax-cn-coding-plan-MiniMax-M3",
            model_sku_id=mm_model.id,
            account_id=mm_account.id,
            runtime_id="opencode",
            runtime_provider_id="opencode",
            execution_verified=True,
        ),
        "pi-minimax-cn-coding-plan-MiniMax-M3": ExecutionTarget(
            id="pi-minimax-cn-coding-plan-MiniMax-M3",
            model_sku_id=mm_model.id,
            account_id=mm_account.id,
            runtime_id="pi",
            runtime_provider_id="minimax-cn",
            execution_verified=True,
        ),
        "alternate-plan-worker": ExecutionTarget(
            id="alternate-plan-worker",
            model_sku_id=alt_model.id,
            account_id=alt_account.id,
            runtime_id="opencode",
            runtime_provider_id="opencode",
            execution_verified=True,
        ),
    }
    registry = ModelRegistry(
        providers={minimax.id: minimax, alternate.id: alternate},
        accounts={mm_account.id: mm_account, alt_account.id: alt_account},
        models={mm_model.id: mm_model, alt_model.id: alt_model},
        execution_targets=targets,
    )

    return bind(registry, alt_model.id, "alternate-plan", datetime(2026, 1, 1, tzinfo=UTC))


class _ExecutionEvidence:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def latest_verified_for_target(self, _target_id: str):
        return SimpleNamespace(observed_at=self._now), None


class _QuotaRefresh:
    def __init__(self, now: datetime) -> None:
        self._snapshots = {
            pool: snapshot(pool, now) for pool in ("minimax-token-plan-cn", "alternate-plan")
        }

    def observations(self):
        return tuple(
            SimpleNamespace(provider_id=p)
            for p in (
                "minimax-cn-coding-plan",
                "alternate-plan",
            )
        )

    def snapshot_for_pool(self, pool):
        return self._snapshots.get(pool)


def _service(
    tmp_path: Path,
    now: datetime,
) -> tuple[DispatchRecommendationService, QuotaAvailabilityJournal]:
    journal = QuotaAvailabilityJournal(tmp_path)
    service = DispatchRecommendationService(
        SimpleNamespace(record_system_event=lambda *_args, **_kwargs: None),
        registry_provider=_registry,
        quota_refresh_service=_QuotaRefresh(now),
        execution_evidence_journal=_ExecutionEvidence(now),
        quota_availability_journal=journal,
        tier_table=None,
        runtime_availability=None,
        runtime_availability_fallback=lambda _target_id: True,
    )
    return service, journal


def test_planning_projects_sibling_pool_blocker_and_selects_alternative(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    service, journal = _service(tmp_path, now)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now,
            sanitized_reason_code="USAGE_LIMIT",
        )
    )
    journal.save(
        observe_success(
            None,
            execution_target_id="alternate-plan-worker",
            provider_id="alternate-plan",
            quota_pool_id="alternate-plan",
            observed_at=now,
        )
    )

    task = SimpleNamespace(min_tier="T1")
    recommendation, candidates, invalid = service.recommend_for_task(
        task,
        policy=RoutingObjective.BALANCED,
        now=now,
    )

    assert invalid is None
    by_id = {item.execution_target_id: item for item in candidates}
    assert (
        by_id["pi-minimax-cn-coding-plan-MiniMax-M3"].availability_state
        is QuotaAvailabilityState.COOLDOWN
    )
    assert recommendation.top_pick is not None
    assert recommendation.top_pick.execution_target_id == "alternate-plan-worker"
    minimax_rows = [
        row
        for row in recommendation.evaluations
        if row.model_sku_id == "minimax-cn-coding-plan/MiniMax-M3"
    ]
    assert len(minimax_rows) == 2
    assert all(row.admitted is False for row in minimax_rows)


def test_newer_sibling_success_makes_pi_candidate_available_again(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    service, journal = _service(tmp_path, now)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=2),
            sanitized_reason_code="USAGE_LIMIT",
        )
    )
    journal.save(
        observe_success(
            None,
            execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=1),
        )
    )

    candidates = service.collect_candidates(now=now)
    by_id = {item.execution_target_id: item for item in candidates}

    assert (
        by_id["pi-minimax-cn-coding-plan-MiniMax-M3"].availability_state
        is QuotaAvailabilityState.AVAILABLE_OBSERVED
    )
    assert (
        by_id["minimax-cn-coding-plan-MiniMax-M3"].availability_state
        is QuotaAvailabilityState.COOLDOWN
    )
