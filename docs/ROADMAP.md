# Roadmap

This roadmap is intentionally conservative. The project should earn autonomy by passing explicit engineering gates rather than accumulating agent features first.

## Spike / PoC — ACP feasibility

### Objective
Prove that Codex, Claude Code, and OpenCode/MiniMax can be driven through a common supervised control path on a disposable repository/worktree.

### Required experiments
- Launch each worker through a pinned compatible command.
- Reuse provider-native subscription authentication without copying credentials into project files.
- Capture streaming events and terminal/tool activity.
- Verify cancellation works and does not leave uncontrolled child processes.
- Run read-only tasks for all workers.
- Run one small edit + test task through OpenCode/MiniMax.
- Verify the main repository remains unchanged while workers operate only in disposable worktrees.
- Record permission-request behavior, including any auto-approval behavior in reused infrastructure.
- Capture basic usage/telemetry when available.

### Go / No-Go gate
Proceed to production P0 integration only if the worker lifecycle is reliable enough and the external safety boundary remains authoritative over agent-reported completion.

A resource-observability or Shadow Mode spike may be developed independently, but it must not be treated as authorization for autonomous repository execution.

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
Chaos tests must cover worker crash, orchestrator crash/restart, duplicate submit, missing worktree, unexpected process exit, stale writer lock, and invalid worker result.

No uncertain scenario may silently advance to `COMPLETED`.

---

## P1 — Deterministic Verification

### Objective
Separate worker completion from task completion.

### Scope
- Trusted verifier profiles outside natural-language prompts.
- argv-based commands rather than arbitrary shell strings.
- targeted tests and builds;
- `git diff --check`;
- allowed-path / changed-file policy;
- structured evidence JSON;
- `VERIFIED` distinct from worker `FINISHED`.

### Acceptance gate
Injected failures in tests, build, evidence, or file-scope checks must prevent verification.

---

## P2 — Multi-worker orchestration

### Objective
Add provider plurality without turning the core into provider-specific middleware.

### Scope
- thin Worker Registry: command, transport, capabilities, health;
- routing policy separated from transport registry;
- primary worker;
- reviewer from a different provider by default for selected risk classes;
- single writer preserved at all times;
- separate worktrees for genuinely parallel engineering tasks.

### Baseline flow

```text
Primary -> Verifier -> Reviewer -> Verifier -> Final gate
```

Do not build a default five-agent swarm.

---

## P2.5 — Model Resource Registry / Explainable Routing

### Objective
Make logical model SKUs, concrete execution targets, shared quota pools, and task-specific capability profiles first-class scheduling resources.

### Scope
- `Provider -> Account -> Plan -> QuotaPool` commercial/scarcity hierarchy;
- `ModelSKU -> ExecutionTarget` logical-model vs concrete runtime/account path separation;
- append-only temporal `QuotaBinding` and `ConsumptionRule` facts;
- immutable catalog/quota snapshot references for replay;
- Worker / Reasoning / Review / Escalation / Fallback candidate pools;
- task profiles including risk, capability floors, context needs, failure count, and predicted quota burn where available;
- deterministic **hard eligibility -> admission -> ranking** pipeline;
- machine-readable selection and exclusion explanations;
- task ownership / architecture ownership semantics;
- structured cross-model handoff state.

### Acceptance gate
Given fixed registry/catalog facts, task profile, quota observations, runtime state, policy, and telemetry:

1. hard-ineligible candidates are removed before scoring;
2. subscription tasks that do not fit usable quota headroom are rejected even if pace says `HARVEST`;
3. an unknown binding quota window cannot be ignored because another window is healthy;
4. the scheduler produces a deterministic recommendation and machine-readable explanation;
5. the decision records immutable catalog/policy/quota references for replay.

Routing decisions never weaken P0/P1 safety or verification.

See [`MODEL_RESOURCE_ORCHESTRATION.md`](MODEL_RESOURCE_ORCHESTRATION.md) and [`SCHEDULER_CORRECTNESS.md`](SCHEDULER_CORRECTNESS.md).

---

## P3 — Quota Governor / Temporal Scarcity

### Objective
Use paid subscription and API capacity intelligently without allowing quota logic to weaken safety.

### Scope
- provider/plan-specific collectors;
- shared quota-pool representation;
- normalized states: `AVAILABLE`, `LIMITED`, `CRITICAL`, `EXHAUSTED`, `UNKNOWN`;
- confidence: `EXACT`, `ESTIMATED`, `UNKNOWN`;
- measurement/source method represented separately from confidence;
- simultaneous quota/reset windows;
- immutable quota observation IDs and last-known-good behavior;
- reserve policy;
- provider/model protection modes;
- reset-soon temporal surplus/harvest signal;
- absolute usable-headroom admission;
- conservative fallback behavior;
- no automatic paid overage without explicit approval.

ACP/session usage may be telemetry input, but account/plan-level quota truth is a separate concern.

### Acceptance gate
- UI/API never render an estimated/shared-pool value as exact per-model remaining quota.
- Collector failure never becomes fake zero quota.
- Reserve policy deterministically prevents routine traffic from consuming protected capacity.
- `HARVEST` is only a temporal ranking signal; it never overrides absolute headroom.
- Missing required quota truth fails conservatively.

---

## P3.5 — Shadow Routing Validation

### Objective
Validate the quota-aware routing thesis before enabling production ACTIVE switching.

### Required evidence
For real tasks across multiple relevant reset cycles, record:

- manual model/target choice;
- scheduler recommendation;
- immutable registry/catalog/policy/quota snapshot references;
- quota before/after;
- predicted vs observed burn where measurable;
- verified outcome;
- attempts/time-to-green;
- ownership transfers / handoffs;
- scheduler exclusion reasons.

### ACTIVE gate
Production ACTIVE routing remains disabled until:

1. P0 Safety Kernel authority is present for the execution path;
2. P1 deterministic verification is authoritative;
3. OpenCode/other adapter responses fail closed on stale/malformed decisions;
4. Shadow evidence shows no unacceptable quality/regression penalty;
5. quota survival/utilization improves or at minimum does not regress materially;
6. safe BYPASS remains available.

A spike may exercise session-scoped `ACTIVE` switching in disposable fixtures; that is not production authorization.

---

## P4 — Clients / macOS Control Plane

Status: `P4_0_TYPED_LOCAL_API_AND_CLI_IMPLEMENTED / ACCEPTANCE_RECORDED` (2026-08-31);
menu bar, dashboard and DeskPet/Telegram clients remain future work.

### Objective
Expose the same orchestrator state safely to multiple front ends while keeping the core headless.

### Scope
- typed local API / Unix Domain Socket;
- CLI client;
- macOS dashboard/menu bar/WidgetKit;
- DeskPet client/tool;
- optional Telegram client;
- submit/status/cancel/approve/report;
- model pool and routing configuration;
- quota health, confidence, and source/method display;
- idempotent request IDs;
- proactive notifications.

### Safety rule
Natural-language client messages never become direct shell commands. Client writes are audited by the core. A UI control labelled ACTIVE cannot bypass the daemon's production activation gate.

---

## P5 — Cost-to-Green Analytics

### Objective
Measure actual usefulness of each model SKU **and execution target** in real coding-agent workloads rather than relying on public benchmark prestige alone.

### Scope
- Pass@1;
- attempts-to-green;
- time-to-green;
- tokens-to-green;
- quota-to-green;
- monetary cost-to-green;
- regression rate;
- verifier failure rate;
- model/target utilization by task family and role;
- public benchmark priors stored separately from local observed performance.

### Acceptance gate
Analytics are reproducible from immutable/auditable run telemetry and separate measured values from estimates.

---

## P6 — Adaptive Scheduler

### Objective
Allow ranking weights to improve from real historical performance while preserving explainability and hard safety/admission constraints.

### Preconditions
P6 does not begin until enough P5 data exists to make local performance estimates meaningful.

### Scope
- local posterior capability scores;
- expected time/cost/quota-to-green estimates;
- task-specific admitted-candidate ranking;
- Pareto-frontier analysis;
- bounded online adaptation;
- rollback to deterministic static policy;
- immutable hard floors for risk, reserve, payment, safety, and verification.

### Non-goal
Adaptive logic may tune ranking among admitted candidates; it may not learn around hard constraints.

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
