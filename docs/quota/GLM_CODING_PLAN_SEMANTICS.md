# GLM Coding Plan — quota semantics

Provider: Z.AI (`api.z.ai`) / BigModel (`open.bigmodel.cn`).
Product name: **GLM Coding Plan**.

> The product is *not* renamed to "GLM Token Plan" for symmetry with MiniMax.
> The owner reconciles this card against the Z.AI console, and a renamed product
> breaks that reconciliation. The UI may group both under "subscription quota",
> but each provider's own terminology is preserved.

Status labels are defined in `QUOTA_DOMAIN_MODEL.md`.

## Endpoints — all read-only

| Endpoint | Purpose | Status |
| --- | --- | --- |
| `GET /api/monitor/usage/quota/limit` | Plan quota windows | **CONFIRMED** (live 200 response) |
| `GET /api/monitor/usage/model-usage?startTime=&endTime=` | Per-model token consumption | **CONFIRMED** (live 200 response) |
| `GET /api/monitor/usage/tool-usage?startTime=&endTime=` | MCP tool call counts | **CONFIRMED** (live 200 response); not yet consumed by PAO |

Authentication: the raw token in `Authorization`, with **no `Bearer ` prefix**
(**CONFIRMED**).

No model-generation call is made to discover quota. There is no completion
surface on the collector at all, and `assert_read_only_collector` fails closed if
one is ever added.

## Observed response — `/quota/limit`

Live authenticated read, 2026-09-02 (**CONFIRMED**):

```json
{"code": 200, "msg": "Operation successful", "success": true,
 "data": {
   "level": "lite",
   "limits": [
     {"type": "CREDIT_LIMIT", "unit": 3, "number": 5,
      "usage": 2000,  "currentValue": 0,    "remaining": 2000, "percentage": 0},
     {"type": "CREDIT_LIMIT", "unit": 6, "number": 1,
      "usage": 10000, "currentValue": 8387, "remaining": 1612, "percentage": 83,
      "nextResetTime": 1788671284992}
   ]}}
```

### Field semantics

| Field | Meaning | Status |
| --- | --- | --- |
| `type` | `CREDIT_LIMIT` for plan windows; `TIME_LIMIT` for the monthly MCP allowance | **OBSERVED** |
| `unit` / `number` | **The window discriminator.** `(3, 5)` = 5-hour, `(6, 1)` = weekly | **OBSERVED** |
| `usage` | The window's **capacity**, despite the name | **OBSERVED** |
| `currentValue` | Consumed | **OBSERVED** |
| `remaining` | `usage − currentValue` | **OBSERVED** |
| `percentage` | Percent **used**, integer-rounded | **OBSERVED** |
| `nextResetTime` | Epoch **milliseconds**. Present only while a window is actively consuming | **OBSERVED** |
| `level` | Plan tier (`lite` on this account) | **OBSERVED** |

### Two findings that broke the previous implementation

1. **`type` is `CREDIT_LIMIT`, not `TOKENS_LIMIT`.** The widely-copied community
   plugin (`opencode-glm-quota`) still matches `TOKENS_LIMIT`, which the provider
   appears to have renamed. PAO's collector matched the stale name, so a live
   account produced **no window at all**. Both names are now accepted, so a
   rename in either direction cannot blank the page.

2. **Both entries carry the same `type`.** `unit`/`number` is the *only* thing
   distinguishing 5-hour from weekly. Taking "the first `TOKENS_LIMIT` entry" and
   labelling it `5h` — which is what both the plugin and PAO did — silently
   discards the weekly window. On this account the weekly window is the binding
   one (16.12% vs 100%), so the discarded window was the one that mattered.

### Confidence: EXACT, not ESTIMATED

`percentage` is integer-rounded: 8387/10000 reports `83`, which would render as
17.00% remaining instead of the true **16.12%**. Because exact counts are
present, remaining is derived from `remaining / usage` and the figure is EXACT.
The rounded percentage is used only as a fallback, and then honestly reports
ESTIMATED.

Capacity is carried as `total_units` rather than derived from
`remaining + used`: the provider rounds those two independently (1612 + 8387 =
9999, against a capacity of 10000), so a derived total would silently contradict
the fraction computed from capacity.

## Observed response — `/model-usage`

Requires `startTime`/`endTime` in **`yyyy-MM-dd HH:mm:ss`** format
(**CONFIRMED** — epoch milliseconds are rejected with
`"Parameter validation failed: incorrect time format"`).

```json
{"data": {
  "x_time": ["2026-09-01 21:00", "..."],
  "modelCallCount": [109, 0, ...],
  "tokensUsage": [16835950, 0, ...],
  "totalUsage": {"totalModelCallCount": 109, "totalTokensUsage": 16835950,
                 "modelSummaryList": [{"modelName": "GLM-5.3",
                                       "totalTokens": 42440798, "sortOrder": 1}]},
  "modelDataList": [{"modelName": "GLM-5.3", "sortOrder": 1,
                     "tokensUsage": [16835950, 0, ...], "totalTokens": 42440798}],
  "granularity": "hourly"}}
```

- `modelDataList[].tokensUsage` is an hourly series aligned to `x_time`, covering
  the requested period. PAO sums it for the period figure. (**OBSERVED**)
- `modelDataList[].totalTokens` is a **larger** provider-side total
  (42,440,798 vs 16,835,950 for the same 24h window), apparently lifetime or
  billing-cycle scoped. (**OBSERVED**) Its exact scope is **UNKNOWN**, so the two
  are deliberately not mixed.
- `x_time` bucket labels carry no UTC offset. PAO interprets them as local
  wall-clock and converts; stamping them UTC directly would shift every record by
  the machine's offset. (**INFERRED**)

## Shared-pool semantics

**CONFIRMED** (zcode.z.ai usage-stats docs): supported coding tools share the
subscription quota; the Coding Plan tab presents the 5-hour prompt pool, weekly
quota, and monthly MCP quota as one plan's components, with per-model token
consumption as line items within it — not as separate quotas.

Therefore `quota_semantics = SHARED_POOL` and `shared_across_models = true`.

Covered models are populated from actual provider evidence (models the usage API
reported consumption for), never from a marketing page.

## Model consumption multipliers

**UNKNOWN**, deliberately.

The pool is denominated in **plan credits** (`CREDIT_LIMIT`, capacities of 2000
and 10000). Model usage is denominated in **tokens** (16.8 million over 24h).
Those magnitudes make it plain they are not the same unit, and the provider
publishes no conversion between them.

GLM documentation indicates high-capability models may consume shared quota at
different effective rates, possibly varying with peak/off-peak conditions. No
current, authoritative multiplier could be established, so **none is recorded**.
Stale promotional multipliers are explicitly *not* hard-coded as permanent truth.

Consequence: equivalent-capacity estimation for GLM reports "not enough history"
rather than a number, until PAO records per-task consumption in the pool's own
credit unit. See `EQUIVALENT_CAPACITY.md`.

## Reset semantics

- 5-hour: dynamically refreshed; resets 5 hours after quota consumption
  (**CONFIRMED**, docs). `nextResetTime` is absent when the window is idle —
  consistent with a rolling window that has not started. (**INFERRED**)
- Weekly: starts at subscription activation, resets every 7 days
  (**CONFIRMED**, docs).
- Reset cards can restore a quota to 100% during off-peak periods
  (**CONFIRMED**, docs). PAO does not model these; a restored quota simply
  appears as a higher remaining figure on the next refresh.

## Credential availability — the `CREDENTIAL_NOT_AVAILABLE` root cause

The Quota page reported `CREDENTIAL_NOT_AVAILABLE` for GLM despite the product
having real GLM execution history. Two independent causes, both now fixed:

1. **The collector only read `ZAI_API_KEY` from the environment.** On this
   machine that variable is unset, while the OpenCode auth store holds a working
   `zai-coding-plan` API credential. The daemon is normally launched by the GUI
   app, which inherits LaunchServices' environment rather than a login shell's,
   so the variable is absent *exactly* in the situation the owner cares about.
   Reporting "no credential" there is true of the environment and misleading
   about the product: the owner **is** signed in.

   Historical execution evidence does *not* by itself grant quota-query auth —
   the audit confirmed a real, separately-held credential rather than assuming
   one from past runs.

2. **The stale `TOKENS_LIMIT` matcher** above meant that even with a credential,
   the response produced no window.

`QuotaCredentialResolver` now resolves environment first, then an explicit
allowlist of OpenCode auth-store entries per surface. Discovery still runs with
credential values stripped; execution auth is untouched.

Verified end-to-end 2026-09-02: `credential_source = OPENCODE_AUTH_STORE`,
`quota_state = OBSERVED`, `confidence = EXACT`, both windows present, binding
window `weekly`, one covered model (`GLM-5.3`) with real token consumption.
