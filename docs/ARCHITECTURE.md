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
