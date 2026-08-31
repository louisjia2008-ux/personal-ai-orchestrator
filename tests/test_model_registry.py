from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    ConsumptionRule,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ModelCatalogSnapshot,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowSnapshot,
)

AUG_1 = datetime(2026, 8, 1, tzinfo=UTC)
AUG_20 = datetime(2026, 8, 20, tzinfo=UTC)
AUG_21 = datetime(2026, 8, 21, tzinfo=UTC)
AUG_22 = datetime(2026, 8, 22, tzinfo=UTC)
AUG_23 = datetime(2026, 8, 23, tzinfo=UTC)
AUG_25 = datetime(2026, 8, 25, tzinfo=UTC)
AUG_26 = datetime(2026, 8, 26, tzinfo=UTC)
NOW = datetime(2026, 8, 27, 13, tzinfo=UTC)


def evidence(
    observed_at: datetime,
    source_type: EvidenceSourceType = EvidenceSourceType.PROVIDER_API,
) -> EvidenceSource:
    return EvidenceSource(
        source_type=source_type,
        observed_at=observed_at,
        reference="provider://quota",
    )


def make_registry() -> ModelRegistry:
    providers = {
        "zai": Provider(id="zai", display_name="Z.AI"),
        "minimax": Provider(id="minimax", display_name="MiniMax"),
    }
    accounts = {
        "zai-main": Account(id="zai-main", provider_id="zai", label="Z.AI main"),
        "minimax-main": Account(
            id="minimax-main",
            provider_id="minimax",
            label="MiniMax main",
        ),
    }
    plans = {
        "zai-coding": Plan(
            id="zai-coding",
            account_id="zai-main",
            name="Coding Plan",
            kind=PlanKind.SUBSCRIPTION,
        ),
        "zai-api": Plan(
            id="zai-api",
            account_id="zai-main",
            name="API PAYG",
            kind=PlanKind.PAY_AS_YOU_GO,
        ),
        "minimax-coding": Plan(
            id="minimax-coding",
            account_id="minimax-main",
            name="Coding Plan",
            kind=PlanKind.SUBSCRIPTION,
        ),
    }
    quota_pools = {
        "zai-coding-shared": QuotaPool(
            id="zai-coding-shared",
            plan_id="zai-coding",
            name="Z.AI shared coding quota",
            reserve_fraction=0.15,
            snapshot=QuotaSnapshot(
                state=QuotaState.LIMITED,
                confidence=EvidenceConfidence.EXACT,
                source=evidence(NOW),
                windows=(
                    QuotaWindowSnapshot(
                        window_id="5h",
                        remaining_fraction=0.80,
                        window_started_at=datetime(2026, 8, 27, 10, tzinfo=UTC),
                        reset_at=datetime(2026, 8, 27, 15, tzinfo=UTC),
                        confidence=EvidenceConfidence.EXACT,
                        source=evidence(NOW),
                    ),
                    QuotaWindowSnapshot(
                        window_id="week",
                        remaining_fraction=0.20,
                        window_started_at=datetime(2026, 8, 24, tzinfo=UTC),
                        reset_at=datetime(2026, 8, 31, tzinfo=UTC),
                        confidence=EvidenceConfidence.EXACT,
                        source=evidence(NOW),
                    ),
                ),
            ),
        ),
        "zai-api-wallet": QuotaPool(
            id="zai-api-wallet",
            plan_id="zai-api",
            name="Z.AI API wallet",
            snapshot=QuotaSnapshot(
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.ESTIMATED,
                remaining_units=10.0,
                source=evidence(NOW, EvidenceSourceType.LOCAL_OBSERVATION),
            ),
        ),
        "minimax-coding-shared": QuotaPool(
            id="minimax-coding-shared",
            plan_id="minimax-coding",
            name="MiniMax shared coding quota",
            snapshot=QuotaSnapshot(
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.EXACT,
                source=evidence(NOW),
                windows=(
                    QuotaWindowSnapshot(
                        window_id="5h",
                        remaining_fraction=0.60,
                        window_started_at=datetime(2026, 8, 27, 10, tzinfo=UTC),
                        reset_at=datetime(2026, 8, 27, 15, tzinfo=UTC),
                        confidence=EvidenceConfidence.EXACT,
                        source=evidence(NOW),
                    ),
                ),
            ),
        ),
    }
    catalog_snapshots = {
        "models-dev-2026-08-27": ModelCatalogSnapshot(
            id="models-dev-2026-08-27",
            source="models.dev",
            as_of=NOW,
            fetched_at=NOW,
            content_hash="sha256:test-fixture",
        )
    }
    models = {
        "glm-5.3": ModelSKU(
            id="glm-5.3",
            provider_id="zai",
            display_name="GLM-5.3",
            catalog_snapshot_id="models-dev-2026-08-27",
            capabilities=CapabilityProfile(scores={"architecture": 0.95, "debugging": 0.94}),
        ),
        "glm-5.2": ModelSKU(
            id="glm-5.2",
            provider_id="zai",
            display_name="GLM-5.2",
            catalog_snapshot_id="models-dev-2026-08-27",
            capabilities=CapabilityProfile(scores={"architecture": 0.85, "debugging": 0.86}),
        ),
        "minimax-m3": ModelSKU(
            id="minimax-m3",
            provider_id="minimax",
            display_name="MiniMax M3",
            catalog_snapshot_id="models-dev-2026-08-27",
            capabilities=CapabilityProfile(scores={"implementation": 0.93, "debugging": 0.91}),
        ),
        "minimax-m2.7": ModelSKU(
            id="minimax-m2.7",
            provider_id="minimax",
            display_name="MiniMax M2.7",
            catalog_snapshot_id="models-dev-2026-08-27",
            capabilities=CapabilityProfile(scores={"implementation": 0.85, "debugging": 0.84}),
        ),
    }
    quota_bindings = (
        QuotaBinding(
            id="glm53-zai-coding-v1",
            model_sku_id="glm-5.3",
            quota_pool_id="zai-coding-shared",
            effective_from=AUG_1,
            recorded_at=AUG_1,
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_1, EvidenceSourceType.PROVIDER_DOCUMENTATION),
        ),
        QuotaBinding(
            id="glm52-zai-coding-v1",
            model_sku_id="glm-5.2",
            quota_pool_id="zai-coding-shared",
            effective_from=AUG_1,
            recorded_at=AUG_1,
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_1, EvidenceSourceType.PROVIDER_DOCUMENTATION),
        ),
        QuotaBinding(
            id="glm52-zai-api-v2",
            model_sku_id="glm-5.2",
            quota_pool_id="zai-api-wallet",
            effective_from=AUG_25,
            recorded_at=AUG_26,
            supersedes_binding_id="glm52-zai-coding-v1",
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_26, EvidenceSourceType.PROVIDER_ANNOUNCEMENT),
        ),
        QuotaBinding(
            id="m3-minimax-v1",
            model_sku_id="minimax-m3",
            quota_pool_id="minimax-coding-shared",
            effective_from=AUG_1,
            recorded_at=AUG_1,
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_1, EvidenceSourceType.PROVIDER_DOCUMENTATION),
        ),
        QuotaBinding(
            id="m27-minimax-v1",
            model_sku_id="minimax-m2.7",
            quota_pool_id="minimax-coding-shared",
            effective_from=AUG_1,
            recorded_at=AUG_1,
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_1, EvidenceSourceType.PROVIDER_DOCUMENTATION),
        ),
    )
    consumption_rules = (
        ConsumptionRule(
            id="glm53-burn-v1",
            model_sku_id="glm-5.3",
            quota_pool_id="zai-coding-shared",
            multiplier=1.0,
            native_unit="point",
            effective_from=AUG_1,
            recorded_at=AUG_1,
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_1, EvidenceSourceType.PROVIDER_DOCUMENTATION),
        ),
        ConsumptionRule(
            id="glm53-burn-v2",
            model_sku_id="glm-5.3",
            quota_pool_id="zai-coding-shared",
            multiplier=2.0,
            native_unit="point",
            effective_from=AUG_20,
            recorded_at=AUG_22,
            supersedes_rule_id="glm53-burn-v1",
            confidence=EvidenceConfidence.EXACT,
            source=evidence(AUG_22, EvidenceSourceType.PROVIDER_ANNOUNCEMENT),
        ),
    )
    memberships = (
        PoolMembership(pool=PoolKind.WORKER, model_sku_id="minimax-m3", priority=1),
        PoolMembership(pool=PoolKind.WORKER, model_sku_id="minimax-m2.7", priority=2),
        PoolMembership(pool=PoolKind.REASONING, model_sku_id="glm-5.3", priority=1),
        PoolMembership(pool=PoolKind.REASONING, model_sku_id="minimax-m3", priority=2),
    )
    return ModelRegistry(
        providers=providers,
        accounts=accounts,
        plans=plans,
        quota_pools=quota_pools,
        catalog_snapshots=catalog_snapshots,
        models=models,
        quota_bindings=quota_bindings,
        consumption_rules=consumption_rules,
        pool_memberships=memberships,
    )


def test_models_can_share_real_quota_pool_through_bindings() -> None:
    registry = make_registry()

    glm_53_pool = registry.quota_pool_for_model(
        "glm-5.3",
        effective_at=AUG_20,
        known_at=AUG_20,
    )
    glm_52_pool = registry.quota_pool_for_model(
        "glm-5.2",
        effective_at=AUG_20,
        known_at=AUG_20,
    )
    m3_pool = registry.quota_pool_for_model(
        "minimax-m3",
        effective_at=AUG_20,
        known_at=AUG_20,
    )
    m27_pool = registry.quota_pool_for_model(
        "minimax-m2.7",
        effective_at=AUG_20,
        known_at=AUG_20,
    )

    assert glm_53_pool.id == glm_52_pool.id == "zai-coding-shared"
    assert m3_pool.id == m27_pool.id == "minimax-coding-shared"


def test_binding_change_is_append_only_and_time_aware() -> None:
    registry = make_registry()

    before = registry.active_quota_binding(
        "glm-5.2",
        effective_at=AUG_25,
        known_at=AUG_25,
    )
    after = registry.active_quota_binding(
        "glm-5.2",
        effective_at=AUG_26,
        known_at=AUG_26,
    )

    assert before.id == "glm52-zai-coding-v1"
    assert after.id == "glm52-zai-api-v2"
    assert len([item for item in registry.quota_bindings if item.model_sku_id == "glm-5.2"]) == 2


def test_recorded_time_prevents_backdated_rule_from_rewriting_old_decision() -> None:
    registry = make_registry()

    decision_before_rule_was_known = registry.active_consumption_rule(
        "glm-5.3",
        effective_at=AUG_21,
        known_at=AUG_21,
    )
    replay_after_rule_was_learned = registry.active_consumption_rule(
        "glm-5.3",
        effective_at=AUG_21,
        known_at=AUG_23,
    )

    assert decision_before_rule_was_known is not None
    assert replay_after_rule_was_learned is not None
    assert decision_before_rule_was_known.id == "glm53-burn-v1"
    assert replay_after_rule_was_learned.id == "glm53-burn-v2"


def test_multiple_quota_windows_use_most_constrained_pace() -> None:
    registry = make_registry()
    pool = registry.quota_pools["zai-coding-shared"]

    five_hour, weekly = pool.snapshot.windows

    assert five_hour.pace() == pytest.approx(2.0)
    assert weekly.pace() == pytest.approx(0.4048192771)
    assert pool.snapshot.effective_pace() == pytest.approx(weekly.pace())
    assert registry.effective_pace_for_pool(pool.id) < 0.5


def test_unknown_window_confidence_cannot_claim_precise_fraction() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN confidence"):
        QuotaWindowSnapshot(
            window_id="week",
            remaining_fraction=0.5,
            window_started_at=datetime(2026, 8, 24, tzinfo=UTC),
            reset_at=datetime(2026, 8, 31, tzinfo=UTC),
            confidence=EvidenceConfidence.UNKNOWN,
            source=evidence(NOW, EvidenceSourceType.INFERRED),
        )


def test_unknown_pool_confidence_cannot_claim_precise_units() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN confidence"):
        QuotaSnapshot(
            state=QuotaState.UNKNOWN,
            confidence=EvidenceConfidence.UNKNOWN,
            remaining_units=5.0,
            source=evidence(NOW, EvidenceSourceType.INFERRED),
        )


def test_quota_binding_cannot_cross_provider_lineage() -> None:
    registry = make_registry()
    data = registry.model_dump(mode="json")
    data["quota_bindings"].append(
        {
            "id": "invalid-cross-provider",
            "model_sku_id": "glm-5.3",
            "quota_pool_id": "minimax-coding-shared",
            "effective_from": AUG_1.isoformat(),
            "effective_until": None,
            "recorded_at": AUG_1.isoformat(),
            "supersedes_binding_id": None,
            "confidence": EvidenceConfidence.EXACT,
            "source": evidence(AUG_1).model_dump(mode="json"),
        }
    )

    with pytest.raises(ValidationError, match="provider does not match"):
        ModelRegistry.model_validate(data)


def test_catalog_snapshot_reference_is_validated() -> None:
    registry = make_registry()
    data = registry.model_dump(mode="json")
    data["models"]["glm-5.3"]["catalog_snapshot_id"] = "missing"

    with pytest.raises(ValidationError, match="unknown catalog snapshot"):
        ModelRegistry.model_validate(data)


def test_one_provider_can_have_multiple_lightweight_plans() -> None:
    registry = make_registry()
    zai_plan_ids = {
        plan.id
        for plan in registry.plans.values()
        if registry.accounts[plan.account_id].provider_id == "zai"
    }

    assert zai_plan_ids == {"zai-coding", "zai-api"}


def test_registry_json_round_trip_preserves_temporal_facts() -> None:
    registry = make_registry()

    restored = ModelRegistry.model_validate_json(registry.model_dump_json())

    assert restored == registry
    assert len(restored.quota_bindings) == 5
    assert len(restored.consumption_rules) == 2


def test_candidates_are_sorted_by_priority_then_weight() -> None:
    registry = make_registry()
    data = registry.model_dump(mode="json")
    data["pool_memberships"].extend(
        [
            {
                "pool": PoolKind.WORKER,
                "model_sku_id": "glm-5.2",
                "priority": 2,
                "weight": 2.0,
            },
            {
                "pool": PoolKind.WORKER,
                "model_sku_id": "glm-5.3",
                "priority": 3,
                "weight": 1.0,
            },
        ]
    )
    expanded = ModelRegistry.model_validate(data)

    candidates = expanded.candidates_for_pool(PoolKind.WORKER)

    assert [candidate.model_sku_id for candidate in candidates] == [
        "minimax-m3",
        "glm-5.2",
        "minimax-m2.7",
        "glm-5.3",
    ]
