# Architecture

## Responsibility boundary

The project deliberately separates interoperability, worker runtime, safety policy, and client UX.

```text
Telegram / DeskPet / CLI
          |
          v
Typed Orchestrator API
          |
          v
+-------------------------------+
| Host-owned Safety Kernel      |
|-------------------------------|
| Task / Run state              |
| Audit log                     |
| Idempotency                   |
| Worktree Manager              |
| Single-writer lock            |
| Verification state machine    |
| Approval policy               |
| Routing policy                |
| Quota Governor                |
+-------------------------------+
          |
          | ACP v1
          v
+-------------------------------+
| Worker runtime / adapters     |
|-------------------------------|
| Codex                         |
| Claude Code                   |
| OpenCode -> MiniMax/...       |
+-------------------------------+
          |
          v
Task-specific Git worktree
          |
          v
Host-owned deterministic verifier
```

## What may be reused

### ACP v1
Used as the worker communication contract where supported. It is transport/interoperability, not the orchestrator's safety model.

### OpenHands SDK / Agent Server
May be selectively reused for subprocess lifecycle, event streaming, telemetry, credential plumbing, and workspace/runtime infrastructure after the feasibility spike proves the exact integration path.

OpenHands is not the source of truth for task acceptance.

### CAID
Used as an architectural reference for:

- centralized delegation;
- physical Git worktree isolation;
- structured assignments;
- explicit integration;
- test-gated verification.

Production code should not depend on CAID implementation details unless licensing and engineering suitability are separately established.

## State ownership

The orchestrator owns durable task state.

Suggested high-level states:

```text
SUBMITTED
  -> PREPARING
  -> RUNNING
  -> WORKER_FINISHED
  -> VERIFYING
  -> VERIFIED
  -> REVIEWING (optional)
  -> ACCEPTED
  -> INTEGRATED (future / policy controlled)

Any stage may transition to:
  -> BLOCKED
  -> FAILED
  -> CANCELLED
```

`WORKER_FINISHED` deliberately does not imply `VERIFIED` or `ACCEPTED`.

## Worktree model

For an implementation task the host resolves a base SHA and creates the worktree before launching a worker.

Example conceptual layout:

```text
~/.personal-ai-orchestrator/
  state/
    orchestrator.db
  worktrees/
    PT-0001/
    PT-0002/
  evidence/
    PT-0001/
  logs/
```

The worker receives only the task worktree as its writable project workspace.

## Verification model

Verifier commands come from trusted project configuration and are represented as argv arrays rather than arbitrary natural-language shell commands.

Example:

```yaml
profiles:
  python-default:
    hygiene:
      - ["git", "diff", "--check"]
    targeted:
      - ["python", "-m", "pytest", "-q"]
```

A task prompt can select an allowed verifier profile but cannot define executable verifier commands.

## Provider model

Worker Registry should describe connectivity and capabilities only.

Example:

```yaml
workers:
  codex:
    transport: acp
    command: ["<pinned-codex-acp-command>"]

  claude:
    transport: acp
    command: ["<pinned-claude-acp-command>"]

  minimax:
    transport: acp
    command: ["opencode", "acp"]
```

Rules such as "use MiniMax for bulk work" or "review with a different provider" belong in Routing Policy, not the registry.

## Security boundary

No single agent-protocol permission dialog is considered sufficient protection.

The intended defense-in-depth model is:

```text
worker-native restrictions
        +
task-specific worktree
        +
single-writer ownership
        +
host process/workspace policy
        +
deterministic verifier
        +
explicit acceptance/integration policy
```

LLM instructions are never a substitute for these mechanical controls.

## P4.0 local control plane

The P4.0 control plane exposes the authoritative core to local clients through a typed,
versioned JSON API (`/v1/...`) served on a permission-restricted Unix Domain Socket
(`0600`). The daemon remains the only authority; clients are transport-only.

```text
pao CLI (src/personal_ai_orchestrator/cli.py)
        |
ControlPlaneClient (typed, control_client.py)
        |
UDS HTTP: /v1/tasks, /v1/tasks/<id>/(runs|verification|routing|cancel|approvals),
          /v1/runs/<id>, /v1/providers, /v1/quota, /v1/active-status, /v1/health
        |
ControlPlaneService (whitelisted view models, sanitized errors)
        |
Safety Kernel / verifier journals / registry / observed availability / activation gate
```

Authority rules:

- submit and bounded cancel reuse the same Safety Kernel transactions as the execution path;
- RUNNING-task cancellation fails closed (`409`) because only the host execution supervisor
  may cancel a supervised process;
- verification reports are rendered from the immutable verification-evidence journal;
  no client payload can mark anything VERIFIED;
- approvals are read-only over the control plane;
- provider/quota health is projected from explicitly whitelisted fields, so credential
  references (for example account `credential_ref`) never transit the API;
- Production ACTIVE is read-only status (`DISABLED_BY_DESIGN` until the activation gate and
  owner approval exist);
- the pre-existing loopback routing API (`local_api.py`, `/v1/opencode/*`) is unchanged and
  remains the OpenCode adapter contract.

The daemon gains an optional `--control-socket` flag; both surfaces can run side by side
against the same durable state.

## P4.2 native dashboard read models

P4.2 adds dashboard-oriented read views without changing authority ownership:

```text
Personal AI Orchestrator.app
        |
MenuBarExtra + Dashboard Window + Settings Scene
        |
Shared OrchestratorStore (auto-refresh /v1/dashboard + /v1/providers/status)
        |
PAOControlClient
        |
/v1/dashboard, /v1/providers, /v1/providers/status, /v1/providers/refresh
        |
ControlPlaneService projections over Safety Kernel truth + ProviderRegistryManager
```

`/v1/dashboard` composes health, task counts, recent tasks, provider/quota health,
Production ACTIVE status, blockers, and recent audit events. `/v1/tasks/<id>/detail`
composes the task, run history, latest routing decision, verification report, approvals,
workspace metadata, and task audit events. Both are read-only, sanitized view models; they
do not expose credential references, API keys, raw logs, database internals, or mutation
handles.

The dashboard makes Shadow semantics explicit. `WOULD_SELECT` presentation stays distinct
from the actual execution target, and `UNKNOWN` quota never renders as a numeric remaining
percentage. Production ACTIVE remains `DISABLED_BY_DESIGN` unless the daemon activation
authority reports otherwise; the app has no enable, force, or override button.

### P4.2.4-B app and worker contract

The native macOS app is the owner control surface. A normal owner should be able to create
a task, watch execution progress, stop a running task, inspect changed files, and read
verification status without opening OpenCode directly.

```text
Personal AI Orchestrator
  = control plane + scheduler + safety authority + observability UI

OpenCode
  = execution harness

GLM / MiniMax / Codex / future models
  = compute workers

Host verifier
  = completion authority
```

OpenCode output is observable evidence, not authority. The app may show a read-only
execution console with bounded, sanitized worker metadata and task events, but it must not
offer an interactive shell for normal operation and must never treat model prose such as
`COMPLETE` as `VERIFIED`. Only the daemon-owned verifier and Safety Kernel transitions can
advance task authority.

### P4.2.4-A real provider registry

The bundled daemon owns a dynamic `ProviderRegistryManager` that performs
credential-safe provider discovery against the local OpenCode CLI:

```text
provider_discovery.discover()
        |
spawns `opencode providers list` + `opencode models <family>`
        |
parses provider labels + env-var names only (no token values)
        |
returns sanitized ProviderDiscovery records
        |
ProviderRegistryManager persists provider-registry.json
        |
ControlPlaneService projects through /v1/providers/status
        |
Dashboard Provider / Agents / Quota pages render real GLM / MiniMax rows
```

The discovery subprocess is bounded (8 s timeout, 256 KB stdout cap), never
spawns a shell, and redacts any token-shaped substring from captured output.
The Dashboard surfaces `evidence_source`, `auth_status`, `execution_status`,
and `last_checked` for every provider, with a CN vs international region tag
for MiniMax surfaces.

Runtime bootstrap now has a macOS application-support layout:

```text
~/Library/Application Support/Personal AI Orchestrator/
  runtime.json
  state.sqlite3
  runtime-state/
  provider-registry.json (sanitized dynamic registry; credential-free)
  logs/

~/Library/Caches/Personal AI Orchestrator/control.sock
```

The socket lives under Caches to keep the AF_UNIX path below macOS limits. Runtime config
bootstrap validates schema versioning, writes atomically, and rejects static config that
contains `credential_ref` values. The legacy `product-bootstrap-empty-registry-v1`
bootstrap snapshot is upgraded in place by the discovery cycle on first launch;
user-authored registries are preserved.

## P4.1 native macOS menu bar client

The menu-bar app (`macos/PAOMenuBar/`, SwiftPM) is a control-plane CLIENT, never the
orchestrator. SwiftUI `MenuBarExtra` (deployment target macOS 13) renders a compact control
surface; `PAOControlKit` holds the typed UDS HTTP client, `/v1` decoding models, connection
state machine and display store.

```text
MenuBarExtra (SwiftUI, read-mostly UI)
        |
OrchestratorStore (@MainActor display state, no durable local database)
        |
PAOControlClient (HTTP/1.1 over 0600 UDS, Connection: close framing)
        |
P4.0 /v1 control API (unchanged; sole backend)
```

Client-side rules:

- authority stays with the daemon: submit/cancel go through the API; RUNNING-task
  cancellation surfaces the daemon's `409` verbatim; the app never signals or spawns
  processes and never reads Safety Kernel SQLite directly;
- Production ACTIVE is a read-only label with daemon-reported blocking reasons — no toggle
  exists anywhere in the client;
- quota confidence semantics are preserved: only `EXACT` renders as a percentage;
  `ESTIMATED`/`UNKNOWN` render as "unknown (confidence: ...)";
- connection states are explicit (`CONNECTED`, `DAEMON_NOT_RUNNING`, `SOCKET_INVALID`,
  `SOCKET_PATH_TOO_LONG`, `ACCESS_DENIED`, `API_VERSION_MISMATCH`, `MALFORMED_RESPONSE`)
  with exponential backoff (2 s → 60 s) while disconnected;
- socket discovery is deterministic (UserDefaults `controlSocketPath` → `PAO_CONTROL_SOCKET`
  → `~/Library/Caches/Personal AI Orchestrator/control.sock`); no filesystem scanning;
- logging is privacy-conscious: connection transitions, operation categories and sanitized
  codes only (`PAO_MENUBAR_STDERR_LOG=1` mirrors them to stderr for acceptance evidence).
