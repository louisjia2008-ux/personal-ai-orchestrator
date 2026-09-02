# MiniMax Token Plan — quota semantics

Provider: MiniMax. Regions: CN (`api.minimaxi.com`) and Global
(`www.minimax.io` / `api.minimax.io`).
Product name: **MiniMax Token Plan**.

Status labels are defined in `QUOTA_DOMAIN_MODEL.md`.

> ## Credential status — rotation still required
>
> A previous agent printed the value of `MINIMAX_API_KEY` into a conversation
> transcript. That credential is **compromised** and
> **`OWNER_ACTION_REQUIRED_MINIMAX_KEY_ROTATION`** still stands.
>
> One live MiniMax read did occur during this phase, unintentionally: an
> acceptance call to `POST /v1/quota/refresh` was issued without a provider
> filter, and the daemon refreshed every connected provider — MiniMax included.
> The call was read-only and non-billable (the documented quota endpoint, no
> model generation), and it used the credential already present in the daemon's
> environment, so it consumed no quota and created no new exposure. It was still
> a live MiniMax call before rotation, and is recorded here rather than omitted.
>
> Its response is the source of the **OBSERVED** findings below.

## Endpoint — read-only

| Endpoint | Status |
| --- | --- |
| `GET https://api.minimaxi.com/v1/token_plan/remains` (CN) | **CONFIRMED** endpoint exists (provider docs) |
| `GET https://www.minimax.io/v1/token_plan/remains` (Global) | **CONFIRMED** endpoint exists (provider docs) |

Authentication: `Authorization: Bearer <key>` (**CONFIRMED**, provider docs).

No model-generation call is made to discover quota.

## Product semantics

**CONFIRMED** (platform.minimax.io/docs/token-plan/intro):

- One subscription covers eligible MiniMax resources through a **shared usage
  bar**.
- Quota is metered over a **5-hour rolling** window and a **weekly** window.
- Unused included quota does not carry over between billing cycles.
- Supported models span the MiniMax lineup (M3 / M2.7 / image / speech), with
  some models excluded from Token Plan coverage.

**CONFIRMED**: per-model "calls per 5 hours" figures on the pricing page are
*reference equivalents* assuming that model is used exclusively. They are **not**
independent per-model quota buckets.

Therefore `quota_semantics = SHARED_POOL` and `shared_across_models = true`.

Covered models are populated only from models the response itself names. The
marketing lineup is not used to pad the list, because an owner reading "M2.7
shares this pool" will act on it.

## Response shapes

Two shapes are accepted, because account surfaces differ and MiniMax does not
formally publish this response.

### Shape A — plan-level counts (**INFERRED**)

From a community research document, not provider documentation:

```json
{"base_resp": {"status_code": 0, "status_msg": "success"},
 "data": {
   "current_interval_total_count": 5000000,
   "current_interval_usage_count": 1245300,
   "current_interval_reset_time": "2026-06-19T21:00:00+08:00",
   "current_weekly_total_count": 35000000,
   "current_weekly_usage_count": 8420000,
   "current_weekly_reset_time": "2026-06-23T00:00:00+08:00",
   "model_remains": [{"model": "MiniMax-M3", "remains": 4500000, "total": 5000000}]}}
```

| Field | Meaning | Status |
| --- | --- | --- |
| `current_interval_total_count` | 5-hour window capacity | **INFERRED** |
| `current_interval_usage_count` | Consumed in that window | **INFERRED** |
| `current_interval_reset_time` | ISO-8601 reset | **INFERRED** |
| `current_weekly_*` | Same, weekly | **INFERRED** |
| `model_remains[].model` | Model identifier | **INFERRED** |
| `model_remains[].remains` / `.total` | That model's *view* of the pool | **INFERRED** |

### Shape B — per-scope percentages (**OBSERVED**, this account, 2026-09-02)

This is the shape this account actually returns, and the one that produced
`QUOTA_VARIES_BY_MODEL`. Each entry is a **workload scope**, and each carries
both windows:

```json
{"model_remains": [
  {"model": "...", "current_interval_remaining_percent": 95,
   "current_weekly_remaining_percent": 60,
   "start_time": <ms>, "end_time": <ms>,
   "weekly_start_time": <ms>, "weekly_end_time": <ms>}]}
```

Timestamps here are epoch **milliseconds**. (**OBSERVED**)

### Precedence

Plan-level counts win when present: they describe the shared bar **directly**,
rather than through one scope's lens. Per-scope percentages are the fallback,
read from the scopes belonging to the workload being projected.

## The `model_remains` entries are workload scopes (**OBSERVED**)

The prior collector saw entries whose percentages disagreed, concluded no single
plan figure could be derived, and discarded the whole observation. Its
replacement kept that rule and only renamed the outcome — so the defect
survived in a different form.

Candidate interpretations considered:

| Interpretation | Verdict |
| --- | --- |
| Independent per-model quota pools | **Rejected** — contradicts the documented shared usage bar and the documented "reference equivalents" framing of per-model figures. |
| Different quota windows | **Rejected** — the response separates windows by field name (`current_interval_*` vs `current_weekly_*`), not by entry. |
| Separate media / workload categories | **CONFIRMED for this account.** See below. |
| Model-equivalent limits over one pool | **Not what this account returns** — the entries are not models at all. |
| Collector misinterpretation | **Partly true** — the values were read correctly; the errors were treating the entry names as models, and treating disagreement between *different resources* as evidence that nothing was knowable. |

The live response named its `model_remains` entries **`general`** and
**`video`**:

| Entry | 5-hour remaining | Weekly remaining |
| --- | --- | --- |
| `general` | 96% | 59% |
| `video` | 100% | 100% |

The same account's catalog lists its actual routable models as
`MiniMax-M2`, `MiniMax-M2.1`, `MiniMax-M2.5`, `MiniMax-M2.5-highspeed`,
`MiniMax-M2.7`, `MiniMax-M2.7-highspeed`. Neither `general` nor `video` is among
them.

So despite the field being called `model_remains`, its entries are **workload
scopes**: text/coding usage and video usage, metered separately. They do not
disagree about one quantity — they describe two.

This has three consequences in the code:

1. `ModelEquivalentView.scope_id` is deliberately **not** named `model_id`, and
   carries a `scope_kind` of `MODEL`, `PROVIDER_RESOURCE_SCOPE`, or `UNKNOWN`.
   Naming our field after the provider's misleading key would propagate the
   error into the UI. Nothing maps `general → MiniMax-M2.7`.
2. `covered_model_ids` is populated **only** from entries the account's own
   catalog confirms as models. Telling the owner that "video" is a model sharing
   this quota would send them looking for something they cannot route to.
3. Each view additionally carries a `workload_scope`, which is what decides
   whether this product reads it.

## CONFIRMED PRODUCT PROJECTION — coding uses `general`

> For Personal AI Orchestrator coding workloads, MiniMax **`general`** is the
> relevant workload scope. **`video` is not part of coding scheduling.**
>
> This does **not** claim MiniMax's video quota does not exist, and no video
> evidence is discarded. It means the current product does not consume it.

The orchestrator schedules coding, text, and agentic software-engineering work.
It does not schedule MiniMax video generation, so a video balance is not
evidence about coding capacity in either direction: it cannot supply a coding
figure, and it cannot suppress one.

### What the coding projection reads

`general`'s own `current_interval_remaining_percent` and
`current_weekly_remaining_percent` are provider-reported remaining fractions for
that scope, and the two windows are separately identified by field name. Both
are therefore projected as real windows at EXACT confidence — this is case **A**
of the window-semantics audit, not an aggregate that has to be split.

Note that `general` and `video` reporting *different* figures is itself evidence
against a single bar spanning both scopes; under either reading, `general`'s own
remaining percentage is the bound on coding work.

| Input | Coding projection |
| --- | --- |
| `general` 95%, `video` 60% | **95%** — not the mean (77.5%), not the minimum (60%) |
| `general` 20%, `video` 100% | 20% |
| `general` 100%, `video` 0% | 100%, AVAILABLE |
| `general` 0%, `video` 100% | 0%, EXHAUSTED |
| `general` only | that figure |
| `video` only | UNKNOWN, `GENERAL_QUOTA_NOT_AVAILABLE` — never 60% |
| `general` present but unreadable | UNKNOWN, `GENERAL_QUOTA_READ_FAILED` — fail closed |
| `general` 5h only, no weekly field | 5h shown; weekly UNKNOWN, `GENERAL_WINDOW_SEMANTICS_UNKNOWN`. Nothing invented. |

### What video may never do

`video` never enters `PlanQuotaProjection.windows`. Since `to_snapshot()` builds
the scheduler's `QuotaSnapshot` from exactly those windows, video cannot reach
the binding-window selection, Temporal Scarcity pace, QUOTA_SAVER scoring, or
equivalent-capacity estimation. The exclusion is structural rather than a rule
each caller must remember.

Video evidence is still preserved, classified, and rendered under 高级详情 with
an explicit statement that it does not participate in coding scheduling.

### Retired reason codes

`QUOTA_VARIES_BY_MODEL` and `SHARED_POOL_VIEWED_PER_MODEL` are no longer
emitted. Both said that differing scope values meant no figure was derivable,
which is the error itself. The codes that replaced them name what is actually
missing from the coding scope: `GENERAL_QUOTA_NOT_AVAILABLE`,
`GENERAL_QUOTA_READ_FAILED`, `GENERAL_WINDOW_SEMANTICS_UNKNOWN`,
`CODING_SCOPE_VIEWS_DISAGREE`, plus the informational
`VIDEO_SCOPE_IGNORED_FOR_CODING`.

### Not yet claimed

Membership of specific coding models in the `general` scope is **not** asserted
from a marketing lineup. `covered_model_ids` still requires catalog evidence, so
no model is presented as covered without it, and no model-level remaining
balance exists at all (§16).

## Timestamps

ISO-8601 reset times are accepted **only with an explicit offset**. A reset time
without a zone is ambiguous by up to a day; treating it as UTC would draw a
countdown that is simply wrong, so it is dropped while the remaining figure is
kept. (Design decision, **not** provider-derived.)

## Known open questions

| Question | Status |
| --- | --- |
| Which response shape this account returns | **OBSERVED** — Shape B, per-scope percentages |
| Whether `model_remains` entries are workload-scoped rather than model scoped | **OBSERVED** — workload scopes (`general`, `video`) on this account |
| Whether the "shared usage bar" spans those scopes or each holds its own allocation | **UNKNOWN** — the observed values differ. Either way `general` bounds coding work, so this no longer blocks the coding figure |
| Whether `remains`/`total` are model-scoped or pool-scoped units | **UNKNOWN** — surfaced as an equivalent view, never as a pool balance |
| The provider's unit for Token Plan quota | **UNKNOWN** — recorded as `PROVIDER_UNITS`, not forced into "tokens" |
| Per-model consumption reporting | **UNKNOWN** — no documented MiniMax usage endpoint equivalent to GLM's `/model-usage`; none is invented |

## Next step

Rotate the exposed key. Afterwards, a deliberate read-only acceptance against
`/v1/token_plan/remains` should establish:

- whether plan-level count fields (Shape A) are present on this account at all;
- whether the `general` / `video` scopes draw on one bar or on separate
  allocations. This is now a modelling refinement rather than a blocker: coding
  quota reads `general` under either answer.

The acceptance read must be **provider-scoped**:

```
python -m personal_ai_orchestrator.quota_acceptance --provider-id minimax-cn-coding-plan
```

`--provider-id` is required, and the helper refuses rather than widening into an
all-provider refresh — the mistake recorded at the top of this document.
