# Roadmap

This roadmap is evidence-driven. The project should first prove that subscription-aware scheduling creates measurable value before expanding into a general multi-agent platform or polished desktop product.

## Preflight — Provider policy / safe bypass

### Objective
Prove that the MVP can be tested without putting provider accounts or the user's normal OpenCode workflow at unnecessary risk.

### Scope
- audit Z.AI and MiniMax terms/account policies for automated quota/usage access;
- prefer official usage APIs;
- document authentication/rate-limit/third-party restrictions;
- keep undocumented endpoints blocked pending explicit review;
- keep scraping disabled by default;
- define safe bypass behavior when daemon/scheduler/collector is unavailable.

### Acceptance gate
- daemon unavailable -> OpenCode can continue in configured/manual mode;
- scheduler disabled -> OpenCode works normally;
- quota collector unavailable -> conservative/manual fallback with no fake exact quota;
- no MVP collector depends on a prohibited access path.

---

## MVP integration spike — OpenCode + two model families

### Objective
Prove the minimum execution path:

```text
OpenCode
  -> thin adapter/plugin
  -> local orchestrator decision
  -> concrete GLM or MiniMax model assignment/switch
  -> coding task
  -> deterministic host verification
```

### Required experiments
- confirm a reliable session/run/agent-scoped OpenCode model-selection surface;
- avoid shared global config mutation as the normal switching path;
- keep scheduling policy out-of-process in the daemon;
- preserve provider-native authentication where possible;
- run disposable-repository tasks with MiniMax and Z.AI/GLM;
- capture cancellation/provider/model errors;
- prove concurrent sessions cannot overwrite one another;
- treat any experimental OpenCode compaction hook as optional handoff delivery optimization only.

### Go / No-Go gate
Proceed only if OpenCode can be supervised and routed between at least two concrete model families without a credential, concurrency, or verification bypass.

ACP and LiteLLM are not required for this gate.

---

## P0 — Safety Kernel

### Objective
Maintain the host-owned source of truth for task execution.

### Scope
- durable task/run/audit state;
- host-owned worktree/workspace lifecycle;
- one active implementation owner per writable task workspace;
- deterministic verification;
- approvals/risk gates;
- process supervision/cancellation/recovery;
- fail-closed reconciliation.

### Scheduler read-only view
Expose only the derived fields required for model allocation, initially:

```text
task_id
task_state_version
current_owner
risk_class
attempt_count
consecutive_failure_count
last_verification_result
last_verification_at
approval_state
reserve_eligibility / reserve_class
```

The Scheduler must not maintain a shadow safety state.

---

## P1 — Subscription Resource Domain

### Objective
Represent the real commercial scarcity boundary without rebuilding a global model catalog.

### Scope
- lightweight but explicit `Provider -> Account -> Plan -> QuotaPool` identity chain;
- ModelSKU references aligned with upstream metadata such as `models.dev`;
- local versioned catalog snapshots with `catalog_snapshot_id` and last-known-good fallback;
- append-only `QuotaBinding` temporal facts;
- append-only `ConsumptionRule` temporal facts;
- structured evidence provenance;
- valid/effective time distinct from recorded/knowledge time;
- quota observations with `EXACT / ESTIMATED / UNKNOWN` confidence;
- quota pools with multiple simultaneous windows;
- reserve/protection policy;
- RuntimeVariant deferred as a first-class local entity unless real requirements prove it necessary.

### Acceptance gate
- multiple models can share one pool;
- a model can change pool/rate over time without mutating historical facts;
- a later backdated provider correction cannot silently rewrite a past routing replay;
- no shared/estimated observation is exposed as exact per-model quota.

---

## P2 — Quota Observability / Reconciliation

### Initial providers
- Z.AI / GLM coding plan;
- MiniMax coding plan.

### Scope
- provider/plan/pool/window collectors;
- remaining capacity/reset data when genuinely observable;
- external-consumption reconciliation where official provider signals permit it;
- observation time/source/confidence;
- confidence degradation `EXACT -> ESTIMATED -> UNKNOWN` when visibility worsens;
- append-only evidence/history for rule/binding changes.

### Acceptance gate
Every quota value returned by the daemon includes scope, source, observation time, and confidence.

---

## P3 — Temporal Scarcity Scheduler

### Objective
Build a deterministic, auditable scheduler around expiring subscription capacity.

### MVP scarcity primitive
For each active window:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

For multiple simultaneous windows:

```text
effective_pace = min(valid_window_paces)
```

Initial configurable bands may classify critical scarcity / conserve / on-pace / surplus / harvest-candidate states.

Do not add burn-velocity/workload forecasting until real pace-model failures justify it.

### Scope
- Worker / Reasoning / Review / Escalation / Fallback candidate pools;
- hard capability/context/availability filters;
- reserve constraints;
- task/pool weighted scoring;
- deterministic output;
- one scoring trace used for both machine decision and human explanation.

### Decision replay
Every RoutingDecision records at least:

```text
task_state_version
routing_policy_snapshot_id
quota observation ids
quota_binding_id
consumption_rule_id(s)
catalog_snapshot_id
selected model
candidate scoring trace
```

### Acceptance gate
Identical authoritative inputs and snapshots produce identical decisions and explanations.

---

## P4 — Ownership / Handoff

### Objective
Make resource-driven and failure-driven model switching coherent and auditable.

### Scope
- versioned structured task state;
- separate failure/resource/manual transfer counters;
- minimum-attempt hysteresis;
- short cooldown only as a rapid-loop safety backstop;
- explicit `HUMAN_REVIEW_REQUIRED` when transfer limits are exhausted;
- minimal handoff payload:

```text
objective
handoff_reason
invariants
rejected_approaches
current_diff_ref
verification_summary
next_action
from_owner
to_owner
task_state_version
```

### Handoff reasons
`REPEATED_FAILURE`, `QUOTA_EXHAUSTION`, `RISK_ESCALATION`, `EXPLICIT_REVIEW`, `PROVIDER_UNAVAILABLE`, `MANUAL_REROUTE`.

### Acceptance gate
A forced transfer preserves authoritative verification/invariant state and does not consume the wrong transfer budget merely because quota/resource state changed.

---

## P5 — Shadow-mode Dogfooding

### Objective
Collect real routing evidence before allowing the scheduler to change models automatically.

### Behavior
The scheduler recommends but does not switch.

Record:

```text
manual choice
scheduler recommendation
all decision snapshot ids
risk / previous-failure covariates
verified outcome
time / attempts / quota / regression
```

Use matched/similar task families where practical rather than assuming manual and scheduled task distributions are randomized.

### Acceptance gate
Shadow mode is stable enough to identify decision mistakes without interrupting normal OpenCode work.

---

## P6 — Active Dogfooding / MVP Evidence

### Objective
Test the three falsifiable product claims after safe bypass and shadow-mode gates pass.

### Experiment A — Quota survival/utilization
Measure time-to-exhaustion/reserve breach, unused quota at reset, utilization across paid pools, and premium calls avoided/deferred.

### Experiment B — Routing quality
Measure verified success, Pass@1, attempts-to-green, time-to-green, native quota-to-green, direct monetary cost, and regression/verifier failures versus manual routing on comparable workloads.

### Experiment C — Handoff penalty
Measure verified success, rework after transfer, time/context/request overhead, and regression versus appropriate single-model baselines.

### Observation window
Production conclusions should span at least two complete relevant reset cycles when practical. Short replay/shadow data may debug the mechanism but must not be presented as proof of the thesis.

---

## P7 — Cost-to-Green / Invocation Accounting

### Objective
Keep decision economics and actual provider consumption truthful when rules change during a task.

### Rule
**Decision snapshots are locked; invocation accounting is not locked to task-start policy.**

Each model invocation records the binding/rule actually applicable to that call and provider-reported consumption when available.

A long task may legitimately contain calls charged under multiple ConsumptionRules. Task Cost-to-Green sums actual invocation consumption plus time/retry/verification/handoff outcomes.

---

## P8 — Clients / macOS Control Plane

Only after daemon API/state schemas survive real dogfooding:

1. freeze/version local daemon API/event schemas;
2. add macOS Menu Bar;
3. add full SwiftUI dashboard;
4. add WidgetKit glanceable surfaces;
5. keep DeskPet as an optional independent client.

CLI remains the MVP control surface.

---

## P9 — Adaptive Scheduling (deferred)

Begin only after enough real P5-P7 evidence exists.

Possible scope:
- local posterior capability estimates;
- expected time/quota/cost-to-green;
- bounded adaptation;
- Pareto analysis;
- deterministic rollback;
- hard safety/reserve/cost floors.

Explainability/auditability remains an invariant even if selection eventually becomes adaptive.

---

## Explicitly deferred

- ACP as a core dependency;
- LiteLLM/universal gateway in the MVP critical path;
- Claude/Codex execution adapters;
- generalized local GPU/thermal scheduling;
- macOS UI before daemon-state stabilization;
- distributed execution / Kubernetes;
- public SaaS control plane;
- PostgreSQL/Redis/Kafka;
- generalized plugin marketplace;
- autonomous paid overage;
- autonomous architecture migration;
- opaque learned routing without replayable evidence.
