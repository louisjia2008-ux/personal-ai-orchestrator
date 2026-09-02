# Quota domain model — subscription plans as shared pools

Status legend used throughout `docs/quota/`:

| Label | Meaning |
| --- | --- |
| **CONFIRMED** | Stated in the provider's own documentation, or observed directly in an authenticated response from that provider. |
| **OBSERVED** | Seen in a live response from this account, but not documented by the provider. May be account- or tier-specific. |
| **INFERRED** | Derived from third-party sources (community clients, research write-ups) or from reasoning about the data. Not authoritative. |
| **UNKNOWN** | Not established. Modelled explicitly as unknown rather than guessed. |

## Why this model exists

Both providers we integrate sell the same shape of product: **one pool of quota
shared by several models**, with per-model consumption reported separately. The
previous domain had exactly one shape — a pool with windows — and no way to say
that. Two defects followed directly from the gap.

**MiniMax.** The collector required every entry in `model_remains` to report the
same percentage. When they disagreed it concluded there was no single honest
plan-level figure — correct — and then discarded the entire observation: both
windows, every reset time, and every per-model figure. The Quota page went to
`QUOTA_VARIES_BY_MODEL` while holding real data it had just read.

**GLM.** The two limit entries share one `type` and differ only in
`unit`/`number`. The collector took the first match and labelled it `5h`,
silently dropping the weekly window — usually the binding one.

## The hierarchy

```
PlanQuota                        the subscription product
└── SharedQuotaPool              the resource its models draw from
    ├── QuotaWindowSnapshot      5-hour / weekly / monthly-MCP windows
    ├── ModelConsumptionObservation   what each model spent
    ├── ModelEquivalentView      a provider's per-model view of the pool
    └── EquivalentCapacityEstimate    derived, always ESTIMATED
```

Defined in `src/personal_ai_orchestrator/quota_plan.py` and
`quota_equivalent_capacity.py`.

## The three things that must never merge

### A. Shared plan remaining quota

The pool's own balance, per window. May be EXACT when the provider reports it.

> 5-hour: 75% remaining · weekly: 16.12% remaining

### B. Model consumption

What one model contributed to the pool's consumption. **Not** entitlement.

> GLM-5.3 consumed 16,835,950 tokens in the last 24h

`ModelConsumptionObservation` deliberately has **no** `remaining_*` field. The
shape itself makes "M3 has 95% left" unrepresentable, so no future caller can
derive an independent per-model balance from consumption data.

### C. Model equivalent capacity

Derived: "at the current shared remainder, roughly how many more tasks like
mine fit". Always ESTIMATED, never an official balance.

`EquivalentCapacityEstimate` rejects `confidence=EXACT` at construction, so a
guess cannot be promoted into provider truth by any caller or later refactor.

## Invariants

1. **Shared remaining ≠ model remaining.** A model gets a `remaining_fraction`
   of its own only under `PlanQuotaSemantics.MODEL_SCOPED`, which requires
   provider documentation of an independent per-model pool. Neither provider
   documents one, so neither uses it. `PlanQuotaProjection` refuses to be
   constructed with `MODEL_SCOPED` semantics over a shared pool.
2. **Consumption is not entitlement.** See the missing field above.
3. **Units are not normalized.** GLM meters its pool in plan credits and its
   model usage in tokens. The provider publishes no conversion, so none is
   inferred; `ConsumptionUnitKind` keeps them apart, and the equivalent-capacity
   estimator refuses to divide one by the other (`UNIT_MISMATCH`).
4. **Partial knowledge beats hiding everything.** A projection may carry an
   UNKNOWN pool balance *and* real model consumption at the same time. That
   combination is the point, not an inconsistency.
5. **Absent is not zero.** No estimate and an estimate of zero are different
   return types, because they render identically in a progress bar and mean
   opposite things.

## Binding window

`determine_binding_window` picks the scarcest **comparable** window. Two
constraints shape it:

- Windows metered in different units are not comparable, so a raw `min()` over
  mixed units is never taken.
- **Reset horizon is a separate field from scarcity.** 20% remaining that resets
  in 30 minutes and 20% remaining that resets in six days are equally scarce and
  not equally urgent; collapsing them into one score would lose the distinction
  the scheduler needs.

When some window could not be read, the binding answer is downgraded to
ESTIMATED — the scarcest *readable* window may not be the scarcest window.

## What the scheduler consumes

Unchanged and deliberately so. `PlanQuotaProjection.to_snapshot()` produces the
same `QuotaSnapshot` the Quota Governor and Temporal Scarcity logic already
consume: provider-reported windows only. Model consumption and equivalent
capacity travel *beside* the snapshot, not inside it, so advisory numbers cannot
become routing truth by accident.

Scheduler-authoritative inputs remain:

- EXACT provider quota
- ESTIMATED provider quota
- existing validated consumption rules

Equivalent capacity is explanatory/advisory unless a later policy approves it.

## Persistence and migration

`CACHE_SCHEMA_VERSION = 2` (snapshots) and `PROJECTION_SCHEMA_VERSION = 1`
(plan projections) are versioned **independently**, in separate files:

- `runtime-state/quota/quota-<pool>.json` — last-known-good snapshot
- `runtime-state/quota/plan-<pool>.json` — last-known-good plan projection
- `runtime-state/quota/quota-history/<snapshot-id>.json` — append-only journal

Keeping the versions apart means adding plan semantics never invalidates the
snapshot history that historical routing decisions reference. A projection
written under a different schema version is **ignored**, never reinterpreted:
re-reading an old record under new semantics is how a stale fact becomes a
confident wrong answer. No model-level data is manufactured from old
plan-level records.

## Credential scope

Three separate contracts (`quota_credentials.CredentialScope`):

| Scope | Access | Owner |
| --- | --- | --- |
| `DISCOVERY` | Credential **values stripped** from the subprocess environment; presence only | `provider_discovery` |
| `QUOTA` | One allowlisted env var + one allowlisted OpenCode auth-store entry, per surface | `quota_credentials` |
| `EXECUTION` | Provider execution auth | execution path |

`SecretValue` redacts under `repr`/`str`/`format` and refuses to pickle, so a
credential cannot reach a log line, an exception message, an evidence file, or a
JSON response through the ordinary paths that produce them. `reveal()` is the
only accessor, and its only caller builds a request header.

See `docs/quota/MINIMAX_TOKEN_PLAN_SEMANTICS.md`,
`docs/quota/GLM_CODING_PLAN_SEMANTICS.md`, and
`docs/quota/EQUIVALENT_CAPACITY.md`.
