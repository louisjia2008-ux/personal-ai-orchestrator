# OpenCode Stage C — Z.AI / GLM: PARTIAL (real completion blocked)

Routing/adapter path proven; the real completion is blocked by a provider-side
"model unavailable" condition. Sanitized machine evidence:
[`stage_c_zai_glm_evidence.json`](./stage_c_zai_glm_evidence.json).

## Identity

| Field | Value |
| --- | --- |
| Provider display name | Z.AI Coding Plan |
| Provider id | `zai-coding-plan` |
| Model id attempted | `glm-5.3-flash` |
| OpenCode | 1.18.23 |
| Auth present | YES (credential type: api) |
| Credentials exposed | NO |

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Auth metadata present | PASS | provider in `auth.json` credentials block |
| Model in real catalog | PASS | `zai-coding-plan/glm-5.3-flash` (and others) |
| SHADOW non-invasive | PASS | adapter action `RECORD_ONLY`; session model stayed `null` |
| ACTIVE selects real model | PASS | adapter action `SWITCH_MODEL`; session A → `zai-coding-plan/glm-5.3-flash` |
| Second session unchanged | PASS | session B model `null`, cost 0, tokens 0 |
| Real completion | **BLOCKED** | no assistant turn; `MODEL_UNAVAILABLE` |
| Fixture unchanged | PASS | `git status --short` empty; `git diff --check` clean |
| Daemon session accounting | PASS | 1 SHADOW + 1 ACTIVE, both session A; `active_route_count=1` |
| Cancellation | SKIPPED | provider model does not execute turns; nothing to cancel |
| Credential leak | NONE | no auth file read/copied/logged |

## Blocker (sanitized)

Every authenticated `zai-coding-plan` model fails at turn execution with
OpenCode's `SessionRunnerModel.ModelUnavailableError`:

```
Failed to drain Session
  cause: SessionRunnerModel.ModelUnavailableError: Model unavailable: zai-coding-plan/<model>
```

Confirmed across `glm-5.3-flash`, `glm-5.3`, `glm-5.3-highspeed`, `glm-5.2`,
`glm-4.7`, and `glm-5-turbo` — the credential and the session-scoped switch both
succeed, but OpenCode cannot resolve/execute any model on this plan for a turn.

This is a **provider/plan-side availability blocker**, not a missing credential
and not an orchestrator/adapter defect. Resolving it requires user action on the
Z.AI Coding Plan (re-authentication or plan/model entitlement) and is therefore a
stop condition for this provider; the orchestrator does not attempt to obtain or
change credentials.

- Tokens / cost: `UNKNOWN` (no turn executed)
- Quota remaining: `UNKNOWN`
- Provider error category: `MODEL_UNAVAILABLE`

## What is proven for Z.AI

The credential-free daemon → `resolve_adapter_outcome` → session-scoped switch
path works identically to MiniMax (SHADOW non-invasive, ACTIVE switches the exact
real model on session A only, session B isolated, fixture clean). Only the final
provider execution is blocked.
