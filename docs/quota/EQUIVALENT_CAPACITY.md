# Model equivalent capacity — an estimate, and labelled as one

Implemented in `src/personal_ai_orchestrator/quota_equivalent_capacity.py`.

Status labels are defined in `QUOTA_DOMAIN_MODEL.md`.

## What this answers

A question no provider answers: *at the current shared remainder, roughly how
many more tasks like mine fit?*

> GLM-5.3 · 预计还可完成约 18 个类似任务 · 依据最近 12 次相似任务 · **估算**

It is useful, and it is a guess. Every guard below exists to keep it from being
read as a balance.

## What it is not

It is **not** a provider quota figure. It never appears in the shared-plan
section of the Quota page, always carries an "估算 / Estimated" badge, and is
rendered in a separate, secondary, collapsible section.

It is **not** authoritative for scheduling. The scheduler's inputs remain EXACT
provider quota, ESTIMATED provider quota, and existing validated consumption
rules. Equivalent capacity is explanatory/advisory unless a later policy
explicitly approves its use.

## The formula, and why it is not the whole design

```
estimated_remaining_tasks = pool_remaining_units / mean(consumed_units per comparable task)
```

The arithmetic is trivial. The design is in the preconditions, because a
plausible number produced from bad inputs is worse than no number.

## Preconditions — every failure yields absence, not a degraded number

| Reason | When |
| --- | --- |
| `POOL_REMAINING_UNKNOWN` | The shared pool has no readable remainder |
| `NO_HISTORY` | No comparable executions recorded |
| `INSUFFICIENT_SAMPLE` | Fewer than `minimum_sample_count` comparable executions |
| `UNSTABLE_HISTORY` | Relative standard deviation exceeds `maximum_relative_stddev` |
| `UNIT_MISMATCH` | Sample cost and pool remainder are metered in different units |
| `NON_POSITIVE_CONSUMPTION` | Mean cost is zero or non-finite |

Each returns `EquivalentCapacityUnavailable`, a **distinct type carrying no
numeric field at all**, so it cannot be rendered as a quantity. The UI shows
"历史数据不足，暂不估算".

> **Absent is not zero.** "0 tasks remaining" and "we do not know yet" look
> identical in a progress bar and mean opposite things. They are different
> return types precisely so they cannot be confused.

An estimate of exactly zero *is* produced when the pool is genuinely empty —
that is a real answer, and distinct from absence.

## Thresholds are policy, not constants

`EquivalentCapacityPolicy`:

| Field | Default | Rationale |
| --- | --- | --- |
| `minimum_sample_count` | 5 | The smallest sample for which the variance gate below is meaningful rather than dominated by a single outlier. |
| `maximum_relative_stddev` | 0.75 | Above this, the estimate would swing between refreshes, which reads as instability in the *quota* rather than in our sample. |
| `high_confidence_sample_count` | 12 | Below this the estimate publishes with a visible "small sample" qualifier rather than being suppressed. |

The threshold is not hard-coded at 5 anywhere in the estimator: it is read from
the policy, and `tests/test_quota_equivalent_capacity.py` pins behaviour at both
sides of the boundary and separately verifies that a custom threshold is
honoured. That is what stops the value drifting silently.

## Task normalization

Raw "number of tasks" is misleading because tasks are not interchangeable — a
one-line fix and a twenty-file refactor are both "one task".

- Estimation is built on **normalized workload**: the provider-metered units a
  comparable execution actually consumed.
- Samples are pooled only within one `task_class`. Averaging across unlike work
  is how a plausible number becomes a wrong one, and the class key is what
  prevents it.
- **Wall-clock duration is deliberately not used**, alone or as a fallback. It
  correlates with waiting, not with spending.
- Candidate dimensions for future `task_class` derivation: input/context tokens,
  output tokens, provider usage units, tool/model call counts, task
  classification, selected model. Whichever is used, it must resolve to provider
  consumption evidence rather than a proxy.

## The unit guard

The single most important precondition for our providers.

GLM meters its pool in **plan credits** and its model usage in **tokens**, with
no published conversion. Dividing a token-denominated cost into a
credit-denominated remainder produces a confident number with no meaning.
`remaining_unit_kind` must match every sample's `unit_kind`, or the estimator
returns `UNIT_MISMATCH`.

## Current state

**No estimate is produced for either provider today**, and that is the designed
outcome rather than a stub:

- **GLM** — the pool remainder is in plan credits; the only consumption evidence
  is in tokens. Until PAO records per-task consumption in credits, the unit guard
  correctly refuses.
- **MiniMax** — no live observation has been taken (key rotation pending), and no
  documented per-model consumption endpoint exists.

The Quota page therefore renders "历史数据不足，暂不估算" under 预计可用能力.
That is the truthful state, and it is what the section is required to show
before real history exists.

## Structural guarantees

- `EquivalentCapacityEstimate` rejects `confidence != ESTIMATED` at construction.
  There is no code path — and no future refactor — that can mark a derived figure
  EXACT.
- Estimating never mutates quota evidence: nothing in this module writes to the
  snapshot cache, the replay journal, or the scheduler's inputs.
- Changing the shared pool's remainder moves every covered model's estimate
  together, because they share one pool. Changing one model's cost moves only
  that model's estimate. Both are pinned by tests.
