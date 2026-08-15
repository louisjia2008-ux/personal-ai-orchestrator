# Roadmap

This roadmap is intentionally conservative. The project should earn autonomy by passing explicit engineering gates rather than accumulating agent features first.

## Spike / PoC — ACP feasibility

### Objective
Prove that Codex, Claude Code, and OpenCode/MiniMax can be driven through a common ACP-oriented control path on a disposable repository/worktree.

### Required experiments
- Launch each worker through a pinned ACP-compatible command.
- Reuse provider-native subscription authentication without copying credentials into project files.
- Capture streaming events and terminal/tool activity.
- Verify cancellation works and does not leave uncontrolled child processes.
- Run read-only tasks for all workers.
- Run one small edit + test task through OpenCode/MiniMax.
- Verify the main repository remains unchanged while workers operate only in disposable worktrees.
- Record permission-request behavior, including any auto-approval behavior in reused infrastructure.
- Capture basic usage/telemetry when available.

### Go / No-Go gate
Proceed to P0 only if all of the following are true:
1. Codex ACP path works.
2. Claude Code ACP path works.
3. OpenCode/MiniMax ACP path works.
4. Subscription authentication can be reused safely.
5. Streaming + cancellation are reliable enough for supervision.
6. External isolation and deterministic verification can remain authoritative over agent-reported completion.

If any item fails, stop and document the incompatibility before building a production safety kernel around it.

---

## P0 — Safety Kernel

### Objective
Create the host-owned source of truth for task execution.

### Scope
- SQLite + WAL state store.
- Typed `Task`, `Run`, `Approval`, `AuditEvent`, and workspace records.
- Idempotent task submission.
- Host-owned Git worktree lifecycle.
- One active writer per task worktree.
- Process-group supervision and cancellation.
- Fail-closed startup reconciliation between DB, Git, and process state.
- Explicit blocked/error states.
- No worker integration authority over `main`.

### Acceptance gate
Chaos tests must cover:
- worker crash;
- orchestrator crash;
- duplicate submit;
- missing worktree;
- unexpected process exit;
- stale writer lock;
- invalid worker result;
- restart/recovery.

No tested scenario may silently advance to `COMPLETED` when state is uncertain.

---

## P1 — Deterministic Verification

### Objective
Separate worker completion from task completion.

### Scope
- Trusted verifier profiles stored outside natural-language prompts.
- argv-based commands rather than arbitrary shell strings.
- targeted tests;
- build checks;
- `git diff --check`;
- allowed-path / changed-file policy;
- structured evidence JSON;
- `VERIFIED` state distinct from worker `FINISHED`.

### Acceptance gate
Injected failures in tests, build, evidence, and file-scope checks must prevent verification.

---

## P2 — Multi-worker orchestration

### Objective
Add provider plurality without turning the core into provider-specific middleware.

### Scope
- thin Worker Registry: command, transport, capabilities;
- routing policy separated from transport registry;
- primary worker;
- reviewer from a different provider by default for selected risk classes;
- single writer preserved at all times;
- separate worktrees for truly parallel engineering tasks.

### Non-goal
Do not build a five-agent swarm by default. The baseline flow is:

```text
Primary -> Verifier -> Reviewer -> Verifier -> Final gate
```

---

## P3 — Quota Governor / Harvest

### Objective
Use paid subscription capacity intelligently without allowing quota logic to weaken safety.

### Scope
- provider-specific collectors;
- normalized states: `AVAILABLE`, `LIMITED`, `EXHAUSTED`, `UNKNOWN`;
- remaining quota when observable;
- reset time;
- confidence;
- reserve policy;
- reset-soon harvest policy;
- conservative fallback behavior;
- no automatic paid overage without explicit approval.

ACP session usage may be telemetry input, but account-level weekly quota is treated as a separate concern.

---

## P4 — Clients

### Objective
Expose the same orchestrator state safely to multiple front ends.

### Scope
- typed local API / Unix Domain Socket;
- Telegram client;
- DeskPet client/tool;
- submit/status/cancel/approve/report;
- idempotent request IDs;
- user/chat allowlists;
- proactive notification of important state changes.

### Safety rule
Natural-language client messages never become direct shell commands.

---

## Deferred until evidence justifies them

- CAID-style dependency-graph multi-agent decomposition;
- distributed execution;
- Kubernetes;
- public SaaS API;
- PostgreSQL/Redis/Kafka;
- autonomous merge to protected branches;
- generalized plugin marketplace.
