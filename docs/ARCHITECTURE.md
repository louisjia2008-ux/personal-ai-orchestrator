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
