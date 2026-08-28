# OpenCode Stage C — Final Report

**Status: `OPEN_CODE_STAGE_C_PARTIAL`**

Real provider-native runtime proof of the OpenCode routing spike on macOS
(`opencode` 1.18.23), driven by the committed credential-free routing daemon and
the committed thin-adapter decision logic. MiniMax passes every required gate,
including a real completion; Z.AI/GLM passes routing/switch/isolation but its
real completion is blocked by a provider-side "model unavailable" condition.

| Provider | Provider id | Model | SHADOW | ACTIVE | Isolation | Completion | Cancellation | Overall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MiniMax | `minimax-cn-coding-plan` | `MiniMax-M2.5` | PASS | PASS | PASS | PASS | PASS | **PASS** |
| Z.AI/GLM | `zai-coding-plan` | `glm-5.3-flash` | PASS | PASS | PASS | BLOCKED (`MODEL_UNAVAILABLE`) | SKIPPED | **PARTIAL** |

Per the spike contract, overall Stage C is **PARTIAL** because both providers did
not complete every required real-runtime gate.

## What Stage C proves

For each provider the flow was exercised end to end against the real,
already-authenticated OpenCode runtime:

```
orchestrator (credential-free fake daemon)
  -> RoutingRequest / RoutingDecision  (typed contract)
  -> resolve_adapter_outcome()         (committed thin-adapter logic)
  -> session-scoped model switch        (POST /api/session/{id}/model)
  -> real, harmless completion          (POST /api/session/{id}/prompt)  [MiniMax only]
  -> second session provably untouched
Orchestrator never receives credentials; OpenCode owns provider auth.
```

- **SHADOW** returns the real catalog model as a recommendation and does **not**
  switch the session model (`resolve_adapter_outcome` → `RECORD_ONLY`).
- **ACTIVE** applies a session-scoped switch to the exact real model on session A
  only (`resolve_adapter_outcome` → `SWITCH_MODEL`), variant normalized to
  `default`; session B stays model-less with zero cost/tokens.
- **MiniMax completion** returned the fixture H1 exactly
  (`# OpenCode Stage C Disposable Fixture`), `finish=stop`,
  `tokens: input=184, output=31, cache={read:2727, write:313}`.
- **Cancellation** (MiniMax): an in-flight turn was interrupted precisely,
  producing `finish=error` / "Provider turn interrupted"; the server stayed
  healthy and no orphan remained.
- The disposable fixture was byte-clean after every run; all disposable sessions
  were deleted.

## Runtime note (drift from the Stage A/B pin)

The pinned `@opencode-ai/cli@0.0.0-beta-18387` (`opencode2`) has **no
darwin-arm64 binary** and cannot be installed on this Mac; its plugin
command/switch API also differs from the standalone runtime that holds the real
credentials. Stage C therefore runs on `opencode` 1.18.23 with
`@opencode-ai/plugin@1.18.23` / `@opencode-ai/sdk@1.18.23`, realizing the thin
adapter as a host-side client over the documented `POST /api/session/{id}/model`
switch (same `session.next.model.switched` durable event as Stage B). Stage A/B
and their beta-18387 pin are unchanged. See `STAGE_C_ENVIRONMENT.md`.

## Z.AI blocker

All `zai-coding-plan` models fail at turn execution with
`SessionRunnerModel.ModelUnavailableError: Model unavailable: zai-coding-plan/<model>`.
The credential is present and the switch succeeds; only provider execution is
unavailable. This requires user action on the Z.AI Coding Plan and is a stop
condition for that provider. See `STAGE_C_ZAI_GLM.md`.

## Credential safety

`auth.json` was never read, copied, or logged. Authentication was confirmed only
through supported metadata surfaces. No API keys, tokens, cookies, or headers
appear in any evidence file. See `STAGE_C_ENVIRONMENT.md`.

## Reproduce

```bash
# preflight only (refuses if the provider is not authenticated)
spikes/opencode/runtime_provider_auth_smoke.sh \
  --provider minimax-cn-coding-plan --model MiniMax-M2.5

# full SHADOW + ACTIVE + completion + isolation + cancellation
spikes/opencode/runtime_provider_active_completion.sh \
  --provider minimax-cn-coding-plan --model MiniMax-M2.5 \
  --python .venv/bin/python --evidence-out /tmp/minimax.json
```

Requires a local `opencode` 1.18.23 with the target provider already
authenticated. Not wired into CI (CI has no provider credentials).
