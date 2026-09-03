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
plan-level figure and discarded the entire observation: both windows, every
reset time, and every per-model figure. The Quota page went to
`QUOTA_VARIES_BY_MODEL` while holding real data it had just read. Its
replacement kept the disagreement rule and only renamed the outcome, so
`general = 95%` beside `video = 60%` still collapsed **coding** quota to
UNKNOWN — see "Provider scope truth vs product workload relevance" below.

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

A per-scope view is **not** automatically a model. MiniMax's `model_remains`
entries are named `general` and `video` on the observed account, while its
routable models are `MiniMax-M2.7` and similar. `EquivalentScopeKind` records
whether an entry was confirmed as a model by the account's catalog, and only
confirmed models are listed as sharing the pool. `QuotaWorkloadScope` separately
records what each scope *meters*, which decides whether this product reads it.

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

## Provider scope truth vs product workload relevance

These are two different questions, and conflating them is what produced the
second MiniMax defect.

**Provider scope truth** is what the provider reported. On this account MiniMax
reports a `general` scope and a `video` scope with different remaining figures.
Both are true. Both are preserved verbatim as `ModelEquivalentView` rows, in the
projection and in the sanitized journal.

**Product workload relevance** is which of those scopes *this* product consumes.
Personal AI Orchestrator schedules coding, text, and agentic software-engineering
work. It does not schedule MiniMax video generation.

`quota_workload_scope.py` holds the typed relevance layer:

| Concept | Meaning |
| --- | --- |
| `QuotaWorkloadScope` | `CODING_TEXT`, `VIDEO_GENERATION`, `IMAGE_GENERATION`, `AUDIO`, `UNKNOWN` |
| `PROVIDER_SCOPE_WORKLOADS` | Provider scope name → workload. Evidence only; an unrecognized name stays `UNKNOWN` rather than being guessed at. |
| `ACTIVE_SCHEDULING_WORKLOADS` | What this build schedules. Today: `{CODING_TEXT}`. |
| `select_for_workload` | Scopes of the requested workload win outright; only when none exists do `UNKNOWN` scopes stand in. A scope of *another* workload never stands in. |

### CONFIRMED PRODUCT PROJECTION

> For Personal AI Orchestrator coding workloads, MiniMax **`general`** is the
> relevant workload scope. **`video` is not part of coding scheduling.**
>
> This does not claim MiniMax's video quota does not exist. It means the current
> product does not consume it.

Consequences, all enforced in `tests/test_quota_workload_scope.py`:

- `general = 95%` beside `video = 60%` yields coding quota **95%**, not UNKNOWN,
  not the mean (77.5%), and not the minimum (60%).
- `video` alone yields UNKNOWN with `GENERAL_QUOTA_NOT_AVAILABLE`. It is never
  read as a coding figure by default.
- `video` never enters `PlanQuotaProjection.windows`, so it can never reach the
  `QuotaSnapshot` the scheduler, Temporal Scarcity, and QUOTA_SAVER consume.
  That exclusion is structural, not a rule a caller must remember.
- The relevance layer is declarative. A future build that schedules video adds
  `VIDEO_GENERATION` to `ACTIVE_SCHEDULING_WORKLOADS`; it does not delete an
  `if scope == "video"` branch.

### Reason codes

A workload that cannot be read says why, in its own terms. None of these may be
raised because a scope belonging to *another* workload disagreed:

| Code | Meaning |
| --- | --- |
| `GENERAL_QUOTA_NOT_AVAILABLE` | No scope of the scheduled workload carries a remaining figure. |
| `GENERAL_QUOTA_READ_FAILED` | The coding scope carried a figure that could not be read. Fail closed. |
| `GENERAL_WINDOW_SEMANTICS_UNKNOWN` | One window readable; the other's window semantics are not identifiable in the response, and are not invented. |
| `CODING_SCOPE_VIEWS_DISAGREE` | Several *coding* scopes disagree on one window. |
| `VIDEO_SCOPE_IGNORED_FOR_CODING` | Advanced-Details note, not a failure: a real balance exists for a workload this build does not schedule. |

`QUOTA_VARIES_BY_MODEL` and `SHARED_POOL_VIEWED_PER_MODEL` are **retired**. Both
asserted that differing scope values meant no figure was derivable, which is the
error itself. They are no longer emitted and no longer carry a localized
sentence.

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
consume: provider-reported windows only — and, since P4.2.6.5.1, only the
windows of the plan's `active_workload_scope`. A scope this product does not
schedule is therefore not merely deprioritized by the scheduler; it is not
present in the scheduler's input at all. Model consumption and equivalent
capacity travel *beside* the snapshot, not inside it, so advisory numbers cannot
become routing truth by accident.

Scheduler-authoritative inputs remain:

- EXACT provider quota
- ESTIMATED provider quota
- existing validated consumption rules

Equivalent capacity is explanatory/advisory unless a later policy approves it.

## Persistence and migration

`CACHE_SCHEMA_VERSION = 2` (snapshots) and `PROJECTION_SCHEMA_VERSION = 2`
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

The projection version moved to 2 with the workload-scope projection: a v1
record's `windows` were derived by requiring *every* provider scope to agree, so
a v1 MiniMax record reads UNKNOWN precisely because a video balance disagreed —
the claim this version removes. Such records are dropped, and the next read
replaces them.

## Provider-scoped quota acceptance

`POST /v1/quota/refresh` with no provider filter means *refresh every connected
provider*, and an acceptance run once invoked it while intending to read one.
`quota_acceptance.py` separates the two operations by shape rather than by
discipline:

| Helper | Behaviour |
| --- | --- |
| `scoped_quota_refresh(client, provider_id=...)` | Keyword-only, no default. Blank or missing raises `ScopedRefreshTargetMissing`; a result naming any other provider raises `ScopedRefreshWidened`. |
| `refresh_all_provider_quota(client)` | The deliberate product-wide operation. Must be named to be invoked. |
| `python -m personal_ai_orchestrator.quota_acceptance --provider-id <id>` | CLI; `--provider-id` is required by the parser. |

Both paths are read-only: one authenticated `GET` per collector against a
documented quota endpoint, no model generation, no credential in output.

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
`docs/quota/GLM_CODING_PLAN_SEMANTICS.md`,
`docs/quota/EQUIVALENT_CAPACITY.md`, and
`docs/quota/CLIENT_QUOTA_PROJECTION.md` — the Dashboard's client-side history
series, burn rate and forecast, which are explanation only and never a
scheduling input.
