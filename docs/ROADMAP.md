# Roadmap

This roadmap is intentionally evidence-driven. The project should first prove that subscription-aware scheduling creates measurable value before expanding into a general multi-agent platform or polished desktop product.

## MVP integration spike — OpenCode + quota observability

### Objective
Prove the minimum execution path needed for the product hypothesis:

```text
OpenCode
  -> orchestrator scheduling decision
  -> concrete GLM or MiniMax model selection
  -> supervised coding task
  -> deterministic verification
```

### Required experiments
- confirm a reliable OpenCode model-selection/switch surface that does not require unsafe global config mutation;
- preserve provider-native authentication where possible;
- run one disposable-repository task with MiniMax;
- run one disposable-repository task with Z.AI/GLM;
- capture cancellation and provider/model errors;
- identify observable quota/usage signals for both providers;
- prove concurrent sessions cannot overwrite each other's model assignment;
- keep deterministic host verification authoritative.

### Go / No-Go gate
Proceed only if OpenCode can be supervised and routed between at least two concrete model families without creating a credential, concurrency, or verification bypass.

ACP is not required for this gate.

---

## P0 — Safety Kernel

### Objective
Maintain the host-owned source of truth for task execution.

### Scope
- SQLite + WAL state store;
- typed `Task`, `Run`, `Approval`, `AuditEvent`, and workspace records;
- idempotent task submission;
- host-owned Git worktree lifecycle;
- one active writer per task worktree;
- process supervision and cancellation;
- fail-closed startup reconciliation;
- explicit blocked/error states;
- no worker integration authority over `main`.

### Acceptance gate
Chaos tests cover worker/orchestrator crash, duplicate submit, missing worktree, unexpected process exit, stale writer ownership, invalid worker result, and restart/recovery. Uncertain state must never silently advance to completion.

---

## P1 — Deterministic Verification

### Objective
Keep worker completion separate from verified task completion.

### Scope
- trusted verifier profiles outside natural-language prompts;
- argv-based build/test/hygiene commands;
- targeted tests;
- build checks;
- `git diff --check`;
- changed-file/allowed-path policy;
- structured evidence;
- explicit `VERIFIED` state.

### Acceptance gate
Injected build/test/evidence/file-scope failures must block verification.

---

## P2 — Subscription Resource Domain

### Objective
Represent the real commercial scarcity boundary without rebuilding a global model catalog.

### Scope
- `Provider -> Account -> Plan -> QuotaPool` hierarchy;
- concrete `ModelSKU` references sourced from upstream metadata such as `models.dev` where practical;
- time-aware `QuotaBinding`;
- time-aware `ConsumptionRule`;
- quota observations with `EXACT / ESTIMATED / UNKNOWN` confidence;
- reserve/protection policy on quota pools;
- provider-policy changes represented through effective dates rather than schema changes;
- local models treated as a separate/deferred resource class rather than forced into subscription semantics.

### Acceptance gate
The same quota pool can be shared by multiple models, provider policy can change over time without losing history, and no shared/estimated observation is exposed as exact per-model quota.

See [`MODEL_RESOURCE_ORCHESTRATION.md`](MODEL_RESOURCE_ORCHESTRATION.md).

---

## P3 — Quota Observability / Reconciliation

### Objective
Know what quota truth is actually observable and degrade confidence honestly when it is not.

### Initial providers
- Z.AI / GLM coding plan;
- MiniMax coding plan.

### Scope
- provider/plan collectors;
- remaining capacity when genuinely observable;
- rolling/billing reset data;
- rate-limit and usage signals;
- shared-pool semantics;
- external-consumption reconciliation where provider usage APIs permit it;
- observation timestamps/source/confidence;
- ToS/account-policy audit for any automated quota access;
- no scraping presented as exact truth.

### Acceptance gate
Every quota value returned by the daemon includes scope, source, observation time, and confidence. Loss of provider visibility can explicitly downgrade `EXACT -> ESTIMATED -> UNKNOWN`.

---

## P4 — Deterministic Explainable Scheduler

### Objective
Make auditable model-selection decisions that ration subscription capacity over time.

### Scope
- Worker / Reasoning / Review / Escalation / Fallback candidate pools;
- structured task profile;
- hard eligibility filters;
- quota reserve/protection rules;
- failure-count escalation;
- task/pool-specific weighted scoring;
- time-dependent scarcity penalty using remaining capacity, time-to-reset, burn velocity, expected workload, and fallback quality;
- generated explanation from the exact scoring trace;
- no opaque routing prompt or ML router.

### Acceptance gate
Given identical authoritative task state, model metadata, quota observations, telemetry, and policy, the scheduler must return the identical concrete model decision and identical machine-readable scoring trace.

---

## P5 — OpenCode Dogfooding / Ownership / Handoff

### Objective
Use the scheduler on real coding work and measure whether model switching remains coherent.

### Scope
- OpenCode adapter/session model assignment;
- no shared-global-config race as the normal switching path;
- authoritative Safety Kernel read-only task-health interface;
- implementation ownership;
- architecture/frozen-decision ownership where needed;
- ownership-transfer hysteresis and limits;
- versioned structured task state;
- structured cross-model handoff;
- routing/provider/error audit trail.

### Acceptance gate
A forced escalation between MiniMax and GLM preserves task/worktree ownership, frozen decisions, verification history, and cancellation semantics. Transfer history is versioned and auditable.

---

## P6 — MVP Evidence / Cost-to-Green

### Objective
Test the product hypothesis with falsifiable real-workload evidence.

### Experiment A — Quota survival
Compare scheduled vs unscheduled periods/tasks for:
- time to premium-plan exhaustion/reserve breach;
- unused quota at reset;
- utilization across paid plans;
- premium calls avoided/deferred.

### Experiment B — Routing quality
Compare manual and orchestrator routing for:
- verified success;
- Pass@1;
- attempts-to-green;
- time-to-green;
- native quota-to-green;
- direct monetary cost;
- regression/verifier failure rate.

### Experiment C — Handoff penalty
Compare single-model completion vs structured handoff/escalation for:
- verified success;
- rework after transfer;
- time-to-green;
- context/request overhead;
- regression rate.

### Acceptance gate
The project should show improved quota survival/utilization without material verified-quality degradation, and cross-model handoff must not introduce an unacceptable success/rework penalty.

---

## P7 — Clients / macOS Control Plane

### Objective
Build user-facing controls only after the daemon API and state schemas have survived real dogfooding.

### Sequence
1. CLI remains the MVP interface.
2. Freeze/version the local daemon API and JSON/event schemas.
3. Add macOS Menu Bar status/control.
4. Add full SwiftUI dashboard.
5. Add WidgetKit surfaces for glanceable state and bounded actions.
6. Keep DeskPet as an optional independent client.

### Product boundary
The headless orchestrator remains usable without macOS or DeskPet.

See [`MACOS_CONTROL_PLANE.md`](MACOS_CONTROL_PLANE.md).

---

## P8 — Adaptive Scheduling (deferred)

### Preconditions
Do not begin until P6 produces enough task-specific evidence to estimate local performance meaningfully.

### Possible scope
- local posterior capability estimates;
- expected time/quota/cost-to-green;
- bounded online adaptation;
- Pareto analysis;
- deterministic rollback;
- hard policy floors for safety, reserve, cost, and risk.

### Non-goal
Do not deploy an opaque reinforcement-learning/neural router as the first scheduling implementation.

---

## Explicitly deferred until evidence justifies them

- ACP as a core dependency;
- LiteLLM or another universal gateway in the MVP critical path;
- Claude/Codex execution adapters;
- generalized local GPU/thermal resource scheduling;
- macOS UI before daemon-state stabilization;
- distributed execution;
- Kubernetes;
- public SaaS control plane;
- PostgreSQL/Redis/Kafka;
- generalized plugin marketplace;
- autonomous paid overage;
- autonomous architecture migration;
- opaque learned model routing.
