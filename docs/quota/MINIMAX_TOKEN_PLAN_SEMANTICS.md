# MiniMax Token Plan — quota semantics

Provider: MiniMax. Regions: CN (`api.minimaxi.com`) and Global
(`www.minimax.io` / `api.minimax.io`).
Product name: **MiniMax Token Plan**.

Status labels are defined in `QUOTA_DOMAIN_MODEL.md`.

> ## Live verification not performed
>
> A previous agent printed the value of `MINIMAX_API_KEY` into a conversation
> transcript. That credential is treated as **compromised**, so no authenticated
> MiniMax request was made during this phase.
>
> **`OWNER_ACTION_REQUIRED_MINIMAX_KEY_ROTATION`** — rotate the key, then a live
> read-only acceptance of `/v1/token_plan/remains` can be run.
>
> Everything below is therefore documentation-derived or derived from the shape
> the previous collector was written against. Nothing here is labelled CONFIRMED
> on the strength of a live response from this account, and the collector is
> written to handle both documented shapes rather than committing to one.

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

### Shape B — per-model percentages (**OBSERVED**, this deployment)

The shape the prior collector was written against, and the one that produced
`QUOTA_VARIES_BY_MODEL`:

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
rather than through one model's lens. Per-model percentages are a fallback.

## The `QUOTA_VARIES_BY_MODEL` audit — what those differing values mean

The prior collector saw per-model entries whose percentages disagreed, concluded
no single plan figure could be derived, and discarded the whole observation.

Candidate interpretations considered, per §9:

| Interpretation | Verdict |
| --- | --- |
| Independent per-model quota pools | **Rejected** — contradicts the documented shared usage bar and the documented "reference equivalents" framing of per-model figures. |
| Different quota windows | **Rejected** — the response separates windows by field name (`current_interval_*` vs `current_weekly_*`), not by entry. |
| Separate media quota categories | **Plausible and not excluded** — MiniMax covers text, image and speech; entries named for media classes (e.g. `video`) may be category-scoped views. **UNKNOWN** without a live response. |
| Model-equivalent limits over one pool | **Best supported** — matches the documented "reference equivalents assuming exclusive use" semantics. |
| Collector misinterpretation | **Partly true** — the values were read correctly; the error was concluding that disagreement meant *nothing* was knowable. |

### Resulting behaviour

- Under a documented shared pool, disagreeing per-model percentages are
  **equivalents**, not balances. They are preserved as `ModelEquivalentView` and
  rendered in a section explicitly labelled as per-model views.
- A window whose per-model views **disagree** yields **no** plan-level figure for
  that window: it stays UNKNOWN. Averaging them would fabricate the number this
  phase exists to remove.
- A window whose views **agree** yields an EXACT plan figure — one shared bar
  seen through several models that coincide *is* readable.
- Windows are independent. In the observed case where models disagreed on the
  5-hour view but agreed on weekly, the weekly window is reported EXACT while
  only 5-hour stays UNKNOWN. **Partial knowledge beats hiding everything.**
- The sanitized reason code is now `SHARED_POOL_VIEWED_PER_MODEL`, which says
  what happened, rather than `QUOTA_VARIES_BY_MODEL`, which implied per-model
  quotas exist.

## Timestamps

ISO-8601 reset times are accepted **only with an explicit offset**. A reset time
without a zone is ambiguous by up to a day; treating it as UTC would draw a
countdown that is simply wrong, so it is dropped while the remaining figure is
kept. (Design decision, **not** provider-derived.)

## Known open questions

| Question | Status |
| --- | --- |
| Which response shape this account actually returns today | **UNKNOWN** — blocked on key rotation |
| Whether `model_remains` entries can be media-category rather than model scoped | **UNKNOWN** |
| Whether `remains`/`total` are model-scoped or pool-scoped units | **UNKNOWN** — surfaced as an equivalent view, never as a pool balance |
| The provider's unit for Token Plan quota | **UNKNOWN** — recorded as `PROVIDER_UNITS`, not forced into "tokens" |
| Per-model consumption reporting | **UNKNOWN** — no documented MiniMax usage endpoint equivalent to GLM's `/model-usage`; none is invented |

## Next step

After rotation, run a read-only acceptance against `/v1/token_plan/remains`,
record the sanitized schema here with **OBSERVED** status, and re-evaluate the
media-category question above.
