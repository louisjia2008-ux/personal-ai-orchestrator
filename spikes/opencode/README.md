# OpenCode V2 Routing Spike

Issue: #12

## Purpose

Prove that Personal AI Orchestrator can remain an out-of-process scheduler while a very thin OpenCode V2 adapter applies a session-scoped model choice.

This spike does **not** proxy model completions and does **not** own provider credentials.

## Current upstream assumptions

Validated against the OpenCode V2 documentation on 2026-08-27:

- plugins can call `ctx.session.switchModel({ sessionID, model })`;
- switching the session model does not rewrite the global OpenCode configuration;
- model availability is location/project scoped and comes from the active OpenCode catalog;
- the V2 plugin/client API is beta and should therefore remain isolated behind this adapter;
- `experimental.session.compacting` exists, but is not a correctness dependency for this spike.

Upstream references:

- https://opencode.ai/v2/docs/build/plugins/
- https://opencode.ai/v2/docs/models
- https://opencode.ai/v2/docs/compaction

## Stage A: contract and fake daemon

Run tests:

```bash
python -m pytest -q
```

Run the fake routing daemon:

```bash
python spikes/opencode/fake_daemon.py --model minimax/m3
```

The fake daemon listens only on `127.0.0.1:8765` by default and returns a fixed recommendation. It is spike-only code and must not become the production scheduler.

## Stage B: disposable OpenCode project

Use only a disposable repository/worktree.

Copy or reference `integrations/opencode/plugin.ts` from the disposable project's OpenCode plugin configuration. Start in `SHADOW` mode.

Example plugin configuration shape:

```json
{
  "plugins": [
    {
      "package": "/absolute/path/to/integrations/opencode/plugin.ts",
      "options": {
        "endpoint": "http://127.0.0.1:8765",
        "mode": "SHADOW"
      }
    }
  ]
}
```

Invoke the registered `orchestrator-route` command in a disposable session.

Expected SHADOW behavior:

1. plugin asks the daemon for a recommendation;
2. decision is stored/logged by the adapter;
3. current OpenCode model remains unchanged.

After that passes, explicitly opt into `ACTIVE` mode and repeat.

Expected ACTIVE behavior:

1. daemon returns a model currently present in the OpenCode project catalog;
2. plugin calls session-scoped `switchModel`;
3. only that session changes model;
4. another concurrent session retains its independent selection;
5. stopping the fake daemon causes safe bypass rather than blocking OpenCode.

## Why routing is not automatic yet

The first adapter intentionally uses an explicit command. OpenCode's prompt admission hooks are retry-safe but not an exactly-once side-effect boundary. Before automatic per-prompt routing is enabled, the real daemon needs durable idempotency for a stable request/message identifier and the disposable runtime spike must confirm the exact behavior of the OpenCode build being targeted.

The production direction remains:

```text
OpenCode prompt/session
  -> thin adapter
  -> durable daemon idempotency + scheduler
  -> RoutingDecision
  -> session-scoped switch when ACTIVE
```

## Safety gates

- daemon unavailable/timeout/invalid response => keep current model;
- Shadow Mode never switches models;
- no provider credential is sent to the daemon;
- no global OpenCode configuration mutation is required;
- no task is considered complete because the adapter or worker says so;
- deterministic host verification remains authoritative;
- experimental compaction hooks may enrich handoff context later but are never required for correctness.

## Not part of this spike

- ACP;
- OpenHands runtime integration;
- LiteLLM;
- real quota collectors;
- adaptive scheduling;
- macOS/WidgetKit;
- production persistence/API design.
