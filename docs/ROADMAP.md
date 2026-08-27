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

## P2.5 — Model Resource Registry / Explainable Routing

### Objective
Make concrete model SKUs, plans, quota pools, runtime variants, and task-specific capability profiles first-class scheduling resources.

### Scope
- `Provider -> Account -> Plan -> QuotaPool -> ModelSKU -> RuntimeVariant` hierarchy;
- concrete model-SKU registry rather than vendor-only routing;
- Worker / Reasoning / Review / Escalation / Fallback candidate pools;
- task profiles including risk, language/framework, context needs, failure count, and long-horizon requirements;
- deterministic, auditable routing rules;
- per-selection explanation records;
- task ownership and architecture-ownership semantics to prevent cross-model thrashing;
- structured cross-model handoff state;
- manual and static routing before adaptive routing.

### Acceptance gate
Given a fixed registry, task profile, quota snapshot, and routing configuration, the scheduler must make a deterministic model selection and emit a machine-readable explanation of why that SKU was chosen and why excluded candidates were rejected.

Routing decisions must never weaken P0/P1 safety and verification invariants.

See [`MODEL_RESOURCE_ORCHESTRATION.md`](MODEL_RESOURCE_ORCHESTRATION.md).

---

## P3 — Quota Governor / Harvest

### Objective
Use paid subscription and API capacity intelligently without allowing quota logic to weaken safety.

### Scope
- provider/plan-specific collectors;
- shared quota-pool representation;
- normalized states: `AVAILABLE`, `LIMITED`, `CRITICAL`, `EXHAUSTED`, `UNKNOWN`;
- remaining quota when genuinely observable;
- reset time;
- source-of-truth/confidence: `EXACT`, `ESTIMATED`, `UNKNOWN`;
- reserve policy;
- provider/model protection modes;
- reset-soon harvest policy;
- conservative fallback behavior;
- no automatic paid overage without explicit approval.

ACP session usage may be telemetry input, but account-level weekly quota is treated as a separate concern.

### Acceptance gate
The UI/API must never render an estimated or shared-pool value as an exact per-model remaining quota. Reserve policy must deterministically prevent routine traffic from consuming protected capacity.

---

## P4 — Clients / macOS Control Plane

### Objective
Expose the same orchestrator state safely to multiple front ends while keeping the core headless.

### Scope
- typed local API / Unix Domain Socket;
- CLI client;
- macOS full dashboard;
- macOS menu-bar surface;
- WidgetKit desktop / Notification Center widgets;
- DeskPet client/tool;
- optional Telegram client;
- submit/status/cancel/approve/report;
- model pool and routing configuration;
- quota-plan health and confidence display;
- idempotent request IDs;
- proactive notification of important state changes.

### Safety rule
Natural-language client messages never become direct shell commands. Client writes are audited by the core.

### Product boundary
The macOS app and DeskPet are optional clients. The open-source scheduler must remain usable headlessly without either UI.

See [`MACOS_CONTROL_PLANE.md`](MACOS_CONTROL_PLANE.md).

---

## P5 — Cost-to-Green Analytics

### Objective
Measure actual usefulness of each model SKU in real coding-agent workloads rather than relying on public benchmark prestige alone.

### Scope
- Pass@1;
- attempts-to-green;
- time-to-green;
- tokens-to-green;
- quota-to-green;
- monetary cost-to-green;
- regression rate;
- verifier failure rate;
- model utilization by task family and role;
- public benchmark priors stored separately from local observed performance;
- task-family segmentation where sample size is sufficient.

### Acceptance gate
Analytics must be reproducible from immutable/auditable run telemetry and must separate measured values from estimates.

---

## P6 — Adaptive Scheduler

### Objective
Allow routing weights to improve from real historical performance while preserving explainability and hard safety constraints.

### Preconditions
P6 does not begin until enough P5 data exists to make local performance estimates meaningful.

### Scope
- local posterior capability scores;
- expected time/cost/quota-to-green estimates;
- task-specific candidate ranking;
- Pareto-frontier analysis;
- bounded online adaptation;
- rollback to deterministic static policy;
- explicit policy floors for risk, reserve, safety, and provider cost.

### Non-goal
Do not deploy an opaque reinforcement-learning or neural router as the first implementation.

---

## Deferred until evidence justifies them

- CAID-style dependency-graph multi-agent decomposition;
- distributed execution;
- Kubernetes;
- public SaaS API;
- PostgreSQL/Redis/Kafka;
- autonomous merge to protected branches;
- generalized plugin marketplace;
- opaque model-selection neural networks;
- autonomous paid overage.
