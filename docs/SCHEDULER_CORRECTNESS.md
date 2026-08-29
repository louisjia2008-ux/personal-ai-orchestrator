# Scheduler Correctness Hardening

Status: pre-ACTIVE safety contract

This document closes correctness gaps found during the global review of the model-resource scheduler foundation. The scheduler remains recommendation-only until Shadow Mode evidence is accepted.

## Decision pipeline

Routing is not one weighted score over every model. It is a three-stage pipeline:

```text
TaskProfile + immutable registry/quota/runtime inputs
  -> hard eligibility gates
  -> quota/task admission feasibility
  -> deterministic ranking
  -> auditable recommendation
```

A strong score can never compensate for a failed hard gate.

## Hard eligibility

At minimum, a candidate is excluded before scoring when:

- the model or execution target is disabled;
- the runtime is unavailable;
- a required task capability floor is not met;
- the quota binding is missing or temporally ambiguous;
- the quota snapshot is stale;
- a metered subscription has an unknown binding quota window;
- the pool is exhausted;
- paid usage is not explicitly allowed.

## Quota pace is not admission

Temporal pace remains:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

It answers whether capacity is being consumed faster or slower than the reset clock. It does **not** answer whether enough capacity remains to finish the next task.

For simultaneous binding windows, routing is fail-closed: if any active binding window cannot produce a reliable pace, `effective_pace = UNKNOWN`. A known 5-hour window must not hide an unknown weekly window.

The implementation also exposes `known_min_pace` for diagnostics. It is not routing authority when completeness is false.

## Task admission

For metered subscription capacity:

```text
usable_headroom = minimum_remaining_fraction
                - reserve_fraction
                - uncertainty_margin

predicted_burn = task_burn_p90 * applicable_consumption_multiplier
```

Admission requires the predicted burn to fit usable headroom. Therefore a near-reset `HARVEST` signal cannot admit a task that is larger than the remaining usable quota.

The first safe scheduler requires a burn estimate for subscription-backed work. This can be relaxed later only with evidence-backed policy.

## Immutable quota replay

Every `QuotaSnapshot` has a stable immutable ID. Successful observations may be appended to `QuotaSnapshotJournal`, while the existing cache remains only the mutable last-known-good pointer.

Routing decisions should record the exact quota snapshot IDs used so a historical decision can be replayed from the observations actually known at that time.

## Temporal supersession

`QuotaBinding` and `ConsumptionRule` remain append-only facts with effective time and recorded/knowledge time.

Resolution now applies explicit `supersedes_*` semantics. A later correction with an earlier effective date may supersede an older fact. Multiple overlapping facts that survive supersession are considered ambiguous and fail closed; lexical ID ordering is not a conflict-resolution policy.

## Execution targets

A logical `ModelSKU` is distinct from a concrete `ExecutionTarget`.

Example:

```text
GLM-5.3
  -> Z.AI subscription target
  -> Z.AI PAYG target
```

An execution target identifies the account/runtime path used to execute the model. Quota bindings and consumption rules may be target-specific. This allows the same logical model to have different commercial/quota paths without duplicating the model identity or reintroducing a heavyweight runtime-variant hierarchy.

## Ranking

Only admitted candidates are scored. The initial deterministic ranker may consider:

- capability fit;
- local success prior;
- candidate-pool priority/weight;
- temporal surplus/conservation signal;
- expected latency;
- expected cost-to-green.

The score is optimization after safety/admission, never a way to override reserve, unknown quota, payment, context, capability, or runtime constraints.

## ACTIVE gate

This hardening does not authorize ACTIVE routing. Before ACTIVE is considered, Shadow Mode should record multiple reset cycles of:

- manual choice;
- scheduler recommendation;
- immutable registry/policy/quota snapshot references;
- quota before/after;
- verified outcome;
- time-to-green;
- handoff count.

The Safety Kernel and deterministic verifier remain authoritative regardless of routing mode.
