# Personal AI Orchestrator

A safety-first personal orchestration layer for heterogeneous coding agents such as Codex, Claude Code, and OpenCode/MiniMax.

> Status: **Pre-alpha / architecture & feasibility stage**

## Why this project exists

Coding agents are becoming powerful, but connecting multiple providers directly creates a dangerous control-plane problem: task state, repository isolation, permissions, verification, quota routing, and recovery can become fragmented across agents.

This project takes a deliberately thin-orchestrator approach:

- **ACP v1** for interoperable worker communication.
- **OpenHands SDK / Agent Server** selectively reused for mature agent lifecycle, event streaming, telemetry, and credential plumbing where appropriate.
- **CAID-inspired engineering principles** for physical Git worktree isolation, structured delegation, and test-gated integration.
- A small **host-owned Safety Kernel** remains the source of truth for task state, worktrees, writer locks, verification, approvals, routing, and quota policy.

## Core invariant

A worker saying `COMPLETE` is **not** task completion.

A task may only advance after host-owned deterministic verification passes.

```text
Client / Telegram / DeskPet
          |
          v
Personal Orchestrator Core
  - Task state
  - Audit log
  - Quota policy
  - Worktree manager
  - Single-writer lock
          |
          v
       ACP v1
   /      |       \
Codex   Claude   OpenCode
                  |
               MiniMax
          |
          v
Deterministic Verifier
          |
          v
Review / Acceptance Gate
```

## Safety principles

1. Workers do not write to the main repository.
2. Every implementation task gets a task-specific Git worktree.
3. A worktree has at most one active writer.
4. Agents do not create or destroy their own safety boundaries.
5. Natural-language prompts cannot define executable verifier commands.
6. Trusted project profiles define build/test/hygiene commands.
7. Worker completion and task completion are separate states.
8. Unknown or inconsistent state fails closed to `BLOCKED`.
9. Credentials remain in provider-native stores or macOS Keychain; secrets are not stored in plaintext task records.
10. Multi-agent execution is opt-in and justified by task complexity, not the default.

## Planned phases

| Phase | Goal |
|---|---|
| Spike / PoC | Prove Codex, Claude Code, and OpenCode/MiniMax can be driven through a common ACP control path with streaming, cancellation, auth, telemetry, and disposable-worktree isolation. |
| P0 Safety Kernel | Durable task/run/audit state, idempotency, worktree manager, single-writer locking, process supervision, fail-closed recovery. |
| P1 Verification | Trusted verifier profiles, deterministic build/test/diff checks, structured evidence, explicit `VERIFIED` gate. |
| P2 Multi-worker | Provider registry, primary/reviewer model, different-provider review, strict writer ownership. |
| P3 Quota / Harvest | Provider-specific quota collectors, reset-aware routing, reserves, conservative `UNKNOWN` handling. |
| P4 Clients | Typed Telegram and DeskPet clients for submit/status/cancel/approve/report. |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for acceptance gates.

## Initial technology direction

- Python 3.12+
- `asyncio`
- Pydantic typed models
- SQLite + WAL
- ACP v1
- Git worktrees
- pytest + disposable Git repositories + fake ACP workers
- structured JSON audit logs first; OpenTelemetry later
- Unix Domain Socket for local IPC
- macOS Keychain for orchestrator-owned secrets

The MVP intentionally avoids PostgreSQL, Redis, Kafka, Kubernetes, public REST APIs, vector databases, and distributed scheduling.

## Development policy

Changes should be small, auditable, and milestone-oriented. Prefer one focused branch/PR per engineering milestone. Every milestone should leave the repository in a deterministic, testable state.

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## License

No open-source license has been selected yet. Until a license is added, the repository should be treated as **all rights reserved** even if it later becomes public.
