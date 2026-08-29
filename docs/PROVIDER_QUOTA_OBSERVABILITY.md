# Provider Quota Observability Audit

Status: P3 foundation audit

As of: 2026-08-29T00:58:00Z

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

### Official observability

Official API: YES

Endpoint:

`GET https://www.minimax.io/v1/token_plan/remains`

Authentication:

`Authorization: Bearer <Token Plan subscription key>`

Provider-published evidence:

- Token Plan page: https://platform.minimax.io/subscribe/token-plan
- Paid Services Agreement: https://platform.minimax.io/protocol/paid-agreement
- Official MiniMax CLI: https://github.com/MiniMax-AI/cli

The Token Plan page explicitly documents a 5-hour rolling quota and a weekly quota, and provides the `token_plan/remains` API as a supported way to check usage. It also states that Token Plan capacity is shared across eligible model/media usage, with provider-specific model consumption behavior.

The official MiniMax CLI consumes the same remains endpoint and exposes provider-returned fields such as:

- `current_interval_remaining_percent`
- `current_weekly_remaining_percent` when present
- interval start/end timestamps
- weekly start/end timestamps
- provider count fields

The P3 collector uses explicit provider remaining percentages when present. It does not infer a precise percentage from ambiguous count fields.

### Rate limits

The Token Plan page describes dynamic RPM/TPM throttling and states that throttles typically reset within approximately one minute, with tighter limits possible during peak traffic.

### Confidence

5-hour remaining percentage: `EXACT` when the official API returns an explicit remaining percentage.

Weekly remaining percentage: `EXACT` when the official API returns an explicit weekly remaining percentage; otherwise `UNKNOWN`.

Reset time: `EXACT` only when the official API returns a usable endpoint timestamp.

Quota pool scope: plan/shared-pool truth. The orchestrator does not present these values as independent per-model budgets.

### Authentication integration status

Current OpenCode authentication is known to exist from non-secret metadata, but this execution surface has no supported way to obtain the MiniMax subscription credential without reading/copying the OpenCode credential store, which is forbidden.

`MINIMAX_RUNTIME_QUOTA_PROBE = AUTHENTICATION_INTEGRATION_BLOCKED`

No new key was requested. No OpenCode auth file was read. No completion request was used to test quota collection.

## Z.AI / GLM Coding Plan

### Official observability

Provider-published usage documentation:

- https://zcode.z.ai/en/docs/usage-stats
- https://zcode.z.ai/en/docs/configuration

Official provider repository:

- https://github.com/zai-org/zai-coding-plugins

The ZCode usage documentation explicitly describes remote Coding Plan quota views for:

- 5-hour prompt quota
- weekly quota
- monthly MCP/tool quota
- model usage
- tool usage

The provider-published `glm-plan-usage` plugin queries read-only monitoring endpoints including:

- `/api/monitor/usage/model-usage`
- `/api/monitor/usage/tool-usage`
- `/api/monitor/usage/quota/limit`

For the global Z.AI host, P3 records the quota endpoint as:

`GET https://api.z.ai/api/monitor/usage/quota/limit`

The provider plugin authenticates using the configured authorization token.

### Semantics and confidence

The provider plugin labels the `TOKENS_LIMIT.percentage` value as 5-hour token usage. P3 therefore does not silently relabel the raw percentage as an exact remaining percentage. When a remaining fraction is derived as `1 - usage_fraction`, it is marked `ESTIMATED`.

The provider documentation confirms 5-hour and weekly quota concepts, but the current adapter intentionally leaves reset timestamps `UNKNOWN` unless the response surface provides them with documented semantics.

### Runtime status

No additional Z.AI model calls are made in this phase. The current Stage C runtime completion remains deferred until quota reset, and quota probing is not allowed to trigger a new login or read the OpenCode credential store.

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

Current normalized collector states:

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

For multiple simultaneous quota windows:

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

This phase does not implement model selection. It does not emit `SELECT M3`, `SELECT GLM`, or any other routing decision.

The next phase may consume these normalized snapshots in shadow mode only after P3 acceptance.
