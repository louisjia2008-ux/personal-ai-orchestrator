# Provider Quota Observability Audit

Status: P3 foundation audit

As of: 2026-08-29T01:08:00Z

This document records only quota/resource state that the orchestrator can justify from provider-published sources. It does not activate routing decisions.

## Rules

- `EXACT` means provider-reported quota data with sufficiently clear semantics.
- `ESTIMATED` means a value is derived from provider-published signals but the raw field semantics are not strong enough to present it as exact remaining quota.
- `UNKNOWN` means no reliable precise value is available.
- Provider/API failure is never converted into `EXHAUSTED`.
- Shared-plan truth is never rendered as fabricated per-model remaining quota.
- No scraping or reverse-engineered private endpoint is used as production authority.
- `QuotaBinding` and `ConsumptionRule` remain separate temporal facts in the model registry.

## MiniMax Token Plan

### Current MVP region

The active OpenCode provider identity for this MVP is `minimax-cn-coding-plan`, so the production collector defaults to the **CN region** rather than silently using the Global endpoint.

Provider-published MiniMax CLI documentation explicitly supports two regions:

- Global: `api.minimax.io`
- CN: `api.minimaxi.com`

The same official CLI constructs the quota path as `/v1/token_plan/remains`. Therefore the current MVP collector uses:

`GET https://api.minimaxi.com/v1/token_plan/remains`

The collector retains an explicit Global option. The Global Token Plan page separately documents:

`GET https://www.minimax.io/v1/token_plan/remains`

Provider-published sources:

- CN Token Plan: https://platform.minimaxi.com/subscribe/token-plan
- CN Paid Services Agreement: https://platform.minimaxi.com/protocol/paid-agreement
- Official MiniMax CLI: https://github.com/MiniMax-AI/cli
- Global Token Plan/API example: https://platform.minimax.io/subscribe/token-plan

### Authentication

Token Plan uses a subscription key and is distinct from pay-as-you-go API credentials. The collector accepts a supported credential from its caller and sends it only in the provider request header. It does not read OpenCode's credential store.

### Quota semantics

Provider documentation identifies simultaneous 5-hour rolling and weekly quota windows. The official MiniMax CLI consumes the remains endpoint and exposes provider-returned fields including:

- `current_interval_remaining_percent`
- `current_weekly_remaining_percent` when present
- interval start/end timestamps
- weekly start/end timestamps
- provider count fields

The count fields have changed meaning across provider responses. P3 therefore uses explicit remaining-percentage fields when present and refuses to manufacture a precise percentage from ambiguous count fields.

5-hour remaining percentage: `EXACT` when the official API returns an explicit remaining percentage.

Weekly remaining percentage: `EXACT` when the official API returns an explicit weekly remaining percentage; otherwise `UNKNOWN`.

Reset time: `EXACT` only when the official API returns a usable timestamp.

Quota pool scope: shared Token Plan truth. The orchestrator does not present the shared pool as independent per-model budgets.

### Rate limits / intended use

MiniMax documents dynamic RPM/TPM throttling; throttles typically reset within about one minute and may tighten during peak traffic. The Token Plan is described for individual/interactive developer use, while pay-as-you-go is recommended for production workloads.

### Runtime authentication integration

Current OpenCode authentication is known to exist from non-secret metadata, but this GitHub-connected execution surface has no supported way to obtain the MiniMax subscription credential without reading/copying the OpenCode credential store, which is forbidden.

`MINIMAX_RUNTIME_QUOTA_PROBE = AUTHENTICATION_INTEGRATION_BLOCKED`

No new key was requested. No OpenCode auth file was read. No completion request was used to test quota collection.

## Z.AI / GLM Coding Plan

### Official observability

Provider-published usage documentation:

- https://zcode.z.ai/en/docs/usage-stats
- https://zcode.z.ai/en/docs/configuration

Official provider repository:

- https://github.com/zai-org/zai-coding-plugins

The ZCode usage documentation describes remote Coding Plan views for:

- 5-hour prompt quota
- weekly quota
- monthly MCP/tool quota
- model usage
- tool usage

The provider-published `glm-plan-usage` plugin queries read-only monitoring endpoints including:

- `/api/monitor/usage/model-usage`
- `/api/monitor/usage/tool-usage`
- `/api/monitor/usage/quota/limit`

For the global Z.AI host, P3 records:

`GET https://api.z.ai/api/monitor/usage/quota/limit`

The provider plugin authenticates using its configured authorization token.

### Semantics and confidence

The provider plugin labels `TOKENS_LIMIT.percentage` as 5-hour token **usage**. P3 does not silently relabel that raw value as an exact remaining percentage. A remaining fraction derived as `1 - usage_fraction` is marked `ESTIMATED`.

The provider documentation confirms 5-hour and weekly concepts, but the adapter leaves reset timestamps `UNKNOWN` unless the response provides them with documented semantics.

### Runtime status

No additional Z.AI model calls are made in this phase. Stage C runtime completion remains deferred until quota reset, and quota probing is not allowed to trigger a new login or read the OpenCode credential store.

`ZAI_RUNTIME_QUOTA_PROBE = DEFERRED_PENDING_QUOTA_RESET_OR_AUTH`

## Collector contract

Provider-specific JSON stops at the collector boundary.

```text
provider API
  -> provider collector
  -> QuotaSnapshot
  -> pace trace / explanation
  -> future P3.5 scheduler
```

Normalized collection states:

- `SUCCESS`
- `STALE`
- `UNKNOWN`
- `AUTH_REQUIRED`
- `RATE_LIMITED`
- `PROVIDER_ERROR`

A failed refresh may expose a credential-free last-known-good snapshot as `STALE`. It never rewrites the failure as zero quota.

## Temporal scarcity

For an active window with reliable quota and reset data:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

For multiple simultaneous windows:

```text
effective_pace = min(valid_window_paces)
```

Central thresholds:

- `< 0.5` -> `CRITICAL`
- `0.5 <= pace < 0.8` -> `CONSERVE`
- `0.8 <= pace <= 1.2` -> `ON_PACE`
- `1.2 < pace <= 1.5` -> `SURPLUS`
- `> 1.5` -> `HARVEST`

If remaining quota or remaining-time fraction is not reliable, pace is `UNKNOWN`.

## Local snapshot cache

The cache is a lightweight JSON last-known-good store under a caller-supplied runtime state directory such as `.personal-ai-orchestrator/`.

Properties:

- atomic write via temporary file + `os.replace`
- schema version
- normalized source/provenance
- no credential fields
- previous good snapshot retained when refresh fails
- tests use temporary directories only

## Scope boundary

P3 does not implement model selection. It does not emit `SELECT M3`, `SELECT GLM`, or any other routing decision.

The next phase may consume normalized snapshots in shadow mode only after P3 acceptance.
