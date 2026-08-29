# OpenCode V2 Routing Spike

Issue: #12

## Purpose

Prove that Personal AI Orchestrator can remain an out-of-process scheduler while a very thin OpenCode V2 adapter applies a session-scoped model choice.

This spike does **not** proxy model completions and does **not** own provider credentials.

## Pinned integration target

The runtime spike is pinned to a matched OpenCode beta pair:

- `@opencode-ai/cli@0.0.0-beta-18387`
- `@opencode-ai/plugin@0.0.0-beta-18387`

During the spike, the floating `beta` tags resolved to different builds (`cli` advanced beyond the plugin SDK). Mixing those builds caused a real local-plugin runtime load failure even though a standalone TypeScript check could pass. The integration must therefore pin a known-compatible CLI/plugin pair and upgrade the pair together.

Other validated assumptions:

- plugins can call `ctx.session.switchModel({ sessionID, model })`;
- model switching is a durable session-state mutation, not a global OpenCode configuration rewrite;
- the V2 plugin/client API is beta and remains isolated behind this adapter;
- `experimental.session.compacting` is not a correctness dependency for this spike.

Upstream references:

- https://opencode.ai/v2/docs/build/plugins/
- https://opencode.ai/v2/docs/models
- https://opencode.ai/v2/docs/compaction

## Stage A — contract and fake daemon: PASS

Implemented and verified:

- typed `RoutingRequest`, `RoutingDecision`, `ModelRef`, and routing mode contract;
- `BYPASS`, `SHADOW`, and opt-in `ACTIVE` behavior;
- stale request ID / mode mismatch fail closed;
- conflicting decisions for one request ID are rejected;
- concurrent-session contract fixtures;
- daemon unavailable, timeout, non-OK, or invalid decision keeps the current OpenCode model;
- spike-only fixed-model HTTP daemon;
- strict TypeScript check for the thin OpenCode adapter.

Run the deterministic tests:

```bash
python -m pytest -q
```

Run the fake routing daemon manually if needed:

```bash
python spikes/opencode/fake_daemon.py --model minimax/m3
```

The fake daemon listens only on `127.0.0.1:8765` by default. It emits only disposable routing-contract metadata and must not become the production scheduler.

## Stage B — real disposable OpenCode runtime: PASS

Stage B is exercised in GitHub Actions against the pinned real `opencode2` beta runtime, using only disposable Git repositories, isolated OpenCode databases/config directories, fake model references, and zero provider credentials.

### B.1 Plugin load and command registration: PASS

Verified:

- the local TypeScript plugin is discovered by the real OpenCode runtime;
- setup completes without `failed to load plugin`;
- `orchestrator-route` appears in the real command registry;
- startup is readiness-polled because plugin activation is asynchronous relative to the first API response.

Observed beta quirk:

- `/api/plugin` may return an empty local-plugin list even after the local plugin has successfully registered its command. For beta-18387, command registration plus absence of load failure is the authoritative runtime gate.

### B.2 SHADOW and daemon-failure bypass: PASS

Harness: `spikes/opencode/runtime_shadow_bypass.sh`

Verified in a real disposable OpenCode session:

1. session creation is explicitly scoped with `location.directory` to the disposable repository;
2. `orchestrator-route` sends exactly one `SHADOW` request to the fake daemon;
3. SHADOW does not request a model switch;
4. the exact fake-daemon PID is stopped;
5. a second command remains non-fatal;
6. no second daemon request appears after shutdown;
7. OpenCode remains usable through the adapter's safe-bypass path.

Important pinned-beta detail:

- beta-18387's `/api` command surface requires a textual `text` field in addition to `command` and `arguments`;
- beta-18387 session creation must carry `location.directory` in the JSON payload. Header/query-only attempts created sessions at the shared service working directory and are rejected by the harness isolation assertion.

### B.3 ACTIVE session-local switching and isolation: PASS

Harness: `spikes/opencode/runtime_active_isolation.sh`

Verified with two real OpenCode sessions in one disposable repository:

1. sessions A and B are independently created with no selected model;
2. one `ACTIVE` routing command is executed only for session A;
3. the fake daemon returns `spike-provider/target-model`;
4. OpenCode persists that model selection only on session A;
5. session B remains unchanged;
6. OpenCode normalizes an omitted variant to `default`;
7. both sessions retain `cost = 0` and zero input/output/reasoning/cache token counters;
8. the daemon receives exactly one ACTIVE request, for session A only.

This proves the adapter's `ctx.session.switchModel()` path and session isolation without invoking a provider or consuming model quota.

It does **not** prove that `spike-provider/target-model` is a runnable catalog model. The reference is intentionally fake so Stage B can test OpenCode state semantics without authentication or completion traffic.

## Current CI gate

The OpenCode integration branch requires:

1. Python Ruff + pytest, including non-credential Stage C deterministic-oracle/session-cleanup tests;
2. shell syntax checks for both Stage C provider harness scripts;
3. OpenCode plugin strict TypeScript typecheck;
4. real OpenCode plugin-load/command-registration smoke;
5. real SHADOW + daemon-failure safe-bypass smoke;
6. real ACTIVE two-session isolation smoke.

Real provider completion remains local-only; credentials are never copied into GitHub Actions.

## Why routing is not automatic yet

The first adapter intentionally uses an explicit command. OpenCode prompt admission hooks are retry-safe but are not being treated as an exactly-once side-effect boundary.

Before automatic per-prompt routing is enabled, the production daemon needs durable idempotency tied to a stable request/message identity, and that behavior must be verified against the exact pinned OpenCode build.

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
- SHADOW never switches models;
- ACTIVE changes only the addressed session;
- provider credentials are never sent to the daemon;
- no global OpenCode model configuration mutation is required;
- disposable repository/session isolation is asserted, not assumed;
- no task is considered complete because the adapter or worker says so;
- deterministic host verification remains authoritative;
- experimental compaction hooks may enrich handoff context later but are never required for correctness.

## Stage C — provider-native authentication/completion: REPAIR PARTIAL

The Stage C harness has been repaired to use a strict repository-read completion oracle and
verified cleanup semantics.

### Completion oracle

A provider completion passes only when:

- an assistant completion exists;
- no assistant error exists;
- provider ID matches exactly;
- model ID matches exactly;
- `finish == stop`;
- output exactly equals the host-parsed Markdown H1 in the disposable `README.md`.

The fixture H1 contains a runtime-generated nonce. The prompt asks the model to read `README.md`
and return its H1; the expected H1 is never embedded in the prompt.

### Cleanup / sanitization

- `opencode serve` is launched directly so `$!` is the real server PID;
- the exact server PID and fake-daemon PID are stopped and verified absent;
- the fixture is verified clean, deleted, and not replaced by a real user repository;
- session deletion is recorded separately as attempted vs verified;
- raw HTTP/provider bodies and raw assistant-error objects are not committed;
- provider errors are reduced to bounded categories such as `HTTP_401`, `HTTP_403`, `HTTP_429`, `TIMEOUT`, `MODEL_UNAVAILABLE`, `AUTHORIZATION_ERROR`, `PROVIDER_UNAVAILABLE`, and `UNKNOWN_PROVIDER_ERROR`.

### MiniMax

The old MiniMax provider evidence was generated before the cleanup fix and before the strict
completion oracle. It is retained as historical evidence but is superseded for final acceptance.

`MINIMAX_STAGE_C = HARDENED_RETEST_REQUIRED`

The re-test must first discover a currently available model from the OpenCode catalog and then
run the repaired harness locally using existing OpenCode authentication.

### Z.AI / GLM

No additional Z.AI calls are made during this repair because the user has reported the weekly
quota is exhausted.

- Observed prior failure: `MODEL_UNAVAILABLE`.
- Possible contributing factor: weekly quota exhausted.
- Root cause: `NOT_YET_CONFIRMED`.
- Re-test: required after quota reset.

`ZAI_STAGE_C = DEFERRED_PENDING_QUOTA_RESET`

The repository does not claim that either provider entitlement or OpenCode is definitely broken.

### Runtime note

The pinned `@opencode-ai/cli@0.0.0-beta-18387` (`opencode2`) has no darwin-arm64 binary and cannot be installed on the Mac used for the earlier Stage C run, and 1.18.23's plugin command/switch API differs from beta-18387. Stage C therefore targets the standalone authenticated `opencode` runtime with the thin adapter realized as a host-side client over the documented session model switch. Stage A/B and their beta-18387 pin remain unchanged.

### Harness

- `runtime_provider_auth_smoke.sh` — auth + catalog preflight using supported metadata only;
- `runtime_provider_active_completion.sh` — disposable nonce fixture + real `opencode serve` + credential-free fake daemon + exact-PID cleanup verification;
- `stage_c_runtime.py` — drives SHADOW → ACTIVE → repository read → isolation → cancellation, applies the strict oracle, verifies session deletion, and emits sanitized JSON evidence.

## Not part of this spike

- ACP as the orchestration critical path;
- OpenHands runtime integration;
- LiteLLM completion proxying;
- production quota collectors;
- adaptive scheduling;
- macOS/WidgetKit control center;
- production persistence/API design.
