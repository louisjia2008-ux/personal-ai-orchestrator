from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    EvidenceConfidence,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    RuntimeVariant,
)


def make_registry() -> ModelRegistry:
    providers = {
        "zai": Provider(id="zai", display_name="Z.AI"),
        "minimax": Provider(id="minimax", display_name="MiniMax"),
    }
    accounts = {
        "zai-main": Account(id="zai-main", provider_id="zai", label="Z.AI main"),
        "minimax-main": Account(
            id="minimax-main", provider_id="minimax", label="MiniMax main"
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
            snapshot=QuotaSnapshot(
                state=QuotaState.LIMITED,
                confidence=EvidenceConfidence.EXACT,
                used_fraction=0.72,
                reset_at=datetime(2026, 8, 31, tzinfo=UTC),
                source="provider-reported",
            ),
            reserve_fraction=0.15,
        ),
        "zai-api-wallet": QuotaPool(
            id="zai-api-wallet",
            plan_id="zai-api",
            name="Z.AI API wallet",
            snapshot=QuotaSnapshot(
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.ESTIMATED,
                remaining_units=10.0,
                source="local-accounting",
            ),
        ),
        "minimax-coding-shared": QuotaPool(
            id="minimax-coding-shared",
            plan_id="minimax-coding",
            name="MiniMax shared coding quota",
            snapshot=QuotaSnapshot(
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.ESTIMATED,
                used_fraction=0.31,
                source="local-telemetry",
            ),
        ),
    }
    models = {
        "glm-5.3": ModelSKU(
            id="glm-5.3",
            provider_id="zai",
            quota_pool_id="zai-coding-shared",
            display_name="GLM-5.3",
            capabilities=CapabilityProfile(scores={"architecture": 0.95, "debugging": 0.94}),
        ),
        "glm-5.2": ModelSKU(
            id="glm-5.2",
            provider_id="zai",
            quota_pool_id="zai-coding-shared",
            display_name="GLM-5.2",
            capabilities=CapabilityProfile(scores={"architecture": 0.85, "debugging": 0.86}),
        ),
        "minimax-m3": ModelSKU(
            id="minimax-m3",
            provider_id="minimax",
            quota_pool_id="minimax-coding-shared",
            display_name="MiniMax M3",
            capabilities=CapabilityProfile(scores={"implementation": 0.93, "debugging": 0.91}),
        ),
        "minimax-m2.7": ModelSKU(
            id="minimax-m2.7",
            provider_id="minimax",
            quota_pool_id="minimax-coding-shared",
            display_name="MiniMax M2.7",
            capabilities=CapabilityProfile(scores={"implementation": 0.85, "debugging": 0.84}),
        ),
    }
    runtime_variants = {
        "m3-standard": RuntimeVariant(
            id="m3-standard",
            model_sku_id="minimax-m3",
            name="standard",
            api_model_name="MiniMax-M3",
            context_window=1_000_000,
        ),
        "m3-high-speed": RuntimeVariant(
            id="m3-high-speed",
            model_sku_id="minimax-m3",
            name="high-speed",
            api_model_name="MiniMax-M3-highspeed",
            context_window=1_000_000,
        ),
        "glm-53-standard": RuntimeVariant(
            id="glm-53-standard",
            model_sku_id="glm-5.3",
            name="standard",
            api_model_name="glm-5.3",
        ),
    }
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
        models=models,
        runtime_variants=runtime_variants,
        pool_memberships=memberships,
    )


def test_models_share_real_quota_pool() -> None:
    registry = make_registry()

    assert registry.quota_pool_for_model("glm-5.3") is registry.quota_pool_for_model("glm-5.2")
    assert registry.quota_pool_for_model("minimax-m3") is registry.quota_pool_for_model(
        "minimax-m2.7"
    )


def test_one_provider_can_have_multiple_plans() -> None:
    registry = make_registry()
    zai_plan_ids = {
        plan.id
        for plan in registry.plans.values()
        if registry.accounts[plan.account_id].provider_id == "zai"
    }
    assert zai_plan_ids == {"zai-coding", "zai-api"}


def test_model_can_have_multiple_runtime_variants() -> None:
    variants = make_registry().runtimes_for_model("minimax-m3")
    assert {variant.id for variant in variants} == {"m3-standard", "m3-high-speed"}


def test_unknown_quota_cannot_claim_precise_percentage() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN confidence"):
        QuotaSnapshot(
            state=QuotaState.UNKNOWN,
            confidence=EvidenceConfidence.UNKNOWN,
            used_fraction=0.5,
        )


def test_unknown_quota_without_precision_is_valid() -> None:
    snapshot = QuotaSnapshot(
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
    )
    assert snapshot.used_fraction is None
    assert snapshot.remaining_units is None


def test_dangling_quota_pool_reference_fails_closed() -> None:
    data = make_registry().model_dump(mode="json")
    data["models"]["glm-5.3"]["quota_pool_id"] = "missing-pool"
    with pytest.raises(ValidationError, match="unknown quota pool"):
        ModelRegistry.model_validate(data)


def test_model_cannot_consume_quota_from_different_provider() -> None:
    data = make_registry().model_dump(mode="json")
    data["models"]["glm-5.3"]["quota_pool_id"] = "minimax-coding-shared"
    with pytest.raises(ValidationError, match="provider does not match"):
        ModelRegistry.model_validate(data)


def test_pool_membership_runtime_must_match_model() -> None:
    data = make_registry().model_dump(mode="json")
    data["pool_memberships"].append(
        {
            "pool": PoolKind.REASONING,
            "model_sku_id": "glm-5.3",
            "runtime_variant_id": "m3-standard",
            "priority": 1,
            "weight": 1.0,
        }
    )
    with pytest.raises(ValidationError, match="belongs to a different model SKU"):
        ModelRegistry.model_validate(data)


def test_registry_json_round_trip() -> None:
    registry = make_registry()
    assert ModelRegistry.model_validate_json(registry.model_dump_json()) == registry


def test_candidates_are_sorted_by_priority_then_weight() -> None:
    data = make_registry().model_dump(mode="json")
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
