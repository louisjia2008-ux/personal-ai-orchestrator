"""Runtime-aware ModelRegistry composition.

A logical ModelSKU may have multiple concrete execution targets. This module
adds the Pi runtime targets to the existing OpenCode registry without creating
a second commercial/quota identity for the same PAO provider/model.
"""

from __future__ import annotations

from personal_ai_orchestrator.model_registry import ModelRegistry


def merge_runtime_registries(
    base: ModelRegistry,
    additional: ModelRegistry,
) -> ModelRegistry:
    providers = dict(base.providers)
    for provider_id, provider in additional.providers.items():
        providers.setdefault(provider_id, provider)

    accounts = dict(base.accounts)
    for account_id, account in additional.accounts.items():
        accounts.setdefault(account_id, account)

    plans = dict(base.plans)
    for plan_id, plan in additional.plans.items():
        plans.setdefault(plan_id, plan)

    quota_pools = dict(base.quota_pools)
    for pool_id, pool in additional.quota_pools.items():
        quota_pools.setdefault(pool_id, pool)

    catalog_snapshots = dict(base.catalog_snapshots)
    for snapshot_id, snapshot in additional.catalog_snapshots.items():
        catalog_snapshots.setdefault(snapshot_id, snapshot)

    models = dict(base.models)
    for model_id, model in additional.models.items():
        existing = models.get(model_id)
        if existing is not None:
            if existing.provider_id != model.provider_id:
                raise ValueError(f"runtime registry model provider conflict for {model_id!r}")
            continue
        models[model_id] = model

    execution_targets = dict(base.execution_targets)
    for target_id, target in additional.execution_targets.items():
        existing = execution_targets.get(target_id)
        if existing is not None and existing != target:
            raise ValueError(f"runtime registry execution target conflict for {target_id!r}")
        execution_targets[target_id] = target

    merged = ModelRegistry(
        providers=providers,
        accounts=accounts,
        plans=plans,
        quota_pools=quota_pools,
        catalog_snapshots=catalog_snapshots,
        models=models,
        execution_targets=execution_targets,
        quota_bindings=base.quota_bindings + additional.quota_bindings,
        consumption_rules=base.consumption_rules + additional.consumption_rules,
        pool_memberships=base.pool_memberships + additional.pool_memberships,
    )
    return merged


__all__ = ["merge_runtime_registries"]
