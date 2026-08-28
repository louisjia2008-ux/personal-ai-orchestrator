# Personal AI Orchestrator

An open-source, safety-first **quota-aware resource scheduler for coding agents**.

> Status: **Pre-alpha / architecture & feasibility stage**

## One-sentence thesis

> **Schedule expiring, non-fungible AI subscription capacity across coding-agent workloads.**

Advanced coding-agent users may hold several paid AI subscriptions at once. Those plans are not interchangeable:

- different model SKUs may share one quota pool;
- providers may change pool membership or burn multipliers;
- several reset windows can constrain the same plan simultaneously;
- unused subscription capacity may expire;
- a premium plan can be exhausted early while another paid plan remains mostly idle.

The project is not primarily trying to predict the globally "best" model for every prompt. It is trying to ration scarce subscription capacity over time without sacrificing verified task quality.

## Three core capabilities

1. **Quota Governor** — shared pools, reset windows, reserves, confidence-labelled observations, and provider reconciliation.
2. **Temporal Scarcity Scheduler** — deterministic allocation based initially on simple quota pace rather than opaque learned routing.
3. **Stateful Cross-model Handoff** — preserve invariants, failed approaches, verification state, and ownership history when a task changes models.

**Explainability, auditability, replayability, and safety are invariants across all three.** They are not a separate product layer.

## Core architecture

```text
                     OpenCode
                        |
                 thin adapter/plugin
                        |
                        v
          Personal AI Orchestrator daemon
+------------------------------------------------+
| Safety Kernel read-only task view               |
| - authoritative task/risk/verification state    |
|                                                |
| Quota Governor                                 |
| - Account / Plan / QuotaPool                   |
| - QuotaBinding / ConsumptionRule               |
| - quota windows / observations / reserves       |
|                                                |
| Temporal Scarcity Scheduler                    |
| - candidate pools                              |
| - deterministic scoring trace                  |
| - ownership / handoff                          |
+------------------------------------------------+
       |                              |
       v                              v
models.dev local snapshot      quota collectors
                                   |
                           Z.AI + MiniMax first
```

The orchestrator returns routing decisions. OpenCode remains responsible for actual model completion execution in the MVP.

## Important invariant

A worker saying `COMPLETE` is **not** task completion.

A task becomes green only after host-owned deterministic verification succeeds. Quota state, model selection, fallback, or escalation never grants repository permission or bypasses the Safety Kernel.

## Commercial scarcity domain

The identity boundary remains representable without overbuilding Account/Plan lifecycle machinery in the MVP:

```text
Provider
  -> Account
      -> Plan
          -> QuotaPool

ModelSKU <- upstream metadata
   |
   +-> QuotaBinding
   +-> ConsumptionRule
```

`QuotaBinding` answers **which pool does this model consume?**

`ConsumptionRule` answers **how does activity consume that pool?**

Both are append-only temporal facts because provider policy can change either axis independently.

## Historical replay: two times matter

A provider rule has:

- **effective time** — when the rule applies;
- **recorded time** — when this orchestrator learned it.

Later backdated corrections must not silently rewrite an old routing explanation. Routing decisions therefore record exact task/policy/quota/binding/rule/catalog snapshot IDs.

## Reuse model catalogs instead of rebuilding them

Static public model metadata should come from upstream sources such as `models.dev` where practical.

The daemon keeps a **local versioned last-known-good snapshot** and never requires a live models.dev request in the routing critical path.

The orchestrator focuses on local/user-specific facts upstream catalogs cannot know:

- account/plan identity;
- quota pools and reset windows;
- quota observations/confidence;
- quota bindings and burn rules;
- reserve policy;
- local task outcomes;
- ownership/handoff history.

## MVP temporal scarcity metric

Do not start with a complex forecast model.

For each active quota window:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

Interpretation:

- `pace << 1`: consuming too quickly -> conserve;
- `pace ~= 1`: roughly on pace;
- `pace >> 1`: underused capacity -> possible harvest before expiry.

If several windows apply at once:

```text
effective_pace = min(valid_window_paces)
```

A healthy five-hour window must not hide an almost exhausted weekly limit.

Future burn-velocity/workload forecasting comes only after real dogfooding proves where this simple model fails.

## Quota truth matters

Quota evidence is confidence-labelled:

```text
EXACT       directly provider-reported for the relevant scope
ESTIMATED   inferred from incomplete signals/local telemetry
UNKNOWN     no defensible estimate exists
```

Evidence provenance is structured (`PROVIDER_API`, `PROVIDER_DOCUMENTATION`, `LOCAL_OBSERVATION`, `INFERRED`, `MANUAL_OVERRIDE`, etc.).

The daemon must never display guessed shared-plan capacity as fake exact per-model quota.

## Decision replay != actual accounting

A routing decision freezes the snapshots and facts used to choose a model.

Actual Cost-to-Green is accounted per invocation using the rule/provider consumption that applies to each real call. A task may span multiple provider pricing/quota-rule periods; historical explanation remains frozen while accounting stays truthful.

## OpenCode-first integration

The preferred MVP integration is:

```text
OpenCode session/agent
   -> thin in-process adapter/plugin
   -> out-of-process local daemon
   -> RoutingDecision
   -> session/run/agent-scoped model selection
```

The plugin is only a translator. Quota policy, scoring, durable state, and ownership logic stay in the daemon.

Any experimental OpenCode compaction/context hook may improve delivery of Structured Task State but must not be required for handoff correctness.

## Safety Kernel -> Scheduler contract

The Scheduler should receive a small read-only task-health view such as:

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

It should not need raw credentials, complete audit logs, or full worktree contents to choose a model.

## Ownership and handoff

Resource-driven transfers and failure-driven transfers have different semantics and separate budgets.

Track at least:

```text
failure_transfer_count
resource_transfer_count
manual_transfer_count
```

If transfer limits are exhausted while a task remains unresolved, enter an explicit state such as `HUMAN_REVIEW_REQUIRED` rather than continuing to thrash.

Minimal handoff state:

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

## MVP rollout

### Preflight
- audit provider ToS/account policy for quota collectors;
- prove daemon/scheduler/collector failure has a safe manual bypass.

### Shadow mode
The scheduler recommends models but does not switch them. Record manual choice, scheduler recommendation, decision snapshots, task covariates, and verified outcomes.

### Active mode
Enable automatic model assignment only after shadow-mode and bypass checks pass.

Production conclusions should span at least two complete relevant quota-reset cycles when practical.

## What the MVP must prove

### 1. Quota survival / utilization
Does scheduling delay premature premium-plan exhaustion while reducing paid quota left unused at reset?

### 2. Routing quality
Does scheduler routing preserve verified quality while improving quota/time/cost efficiency compared with manual routing on comparable tasks?

### 3. Handoff penalty
Can structured escalation preserve success rate without unacceptable rework/context/regression penalty?

If these claims fail, more providers, UI, or ML routing do not rescue the product thesis.

## MVP scope

```text
OpenCode
+ Z.AI/GLM
+ MiniMax
+ Python headless daemon
+ CLI
+ existing Safety Kernel
+ quota collectors
+ temporal quota scheduler
+ structured handoff
```

Explicitly not required:

- ACP;
- LiteLLM;
- Claude/Codex execution adapters;
- macOS UI / WidgetKit / DeskPet;
- adaptive/ML routing;
- generalized local-GPU resource scheduling;
- a hand-maintained global model catalog.

## macOS direction — after dogfooding

The long-term product still includes Menu Bar status/control, a SwiftUI dashboard, WidgetKit surfaces, and optional DeskPet integration. Implementation waits until daemon/API/state schemas survive real OpenCode dogfooding. CLI is the MVP interface.

See [`docs/MACOS_CONTROL_PLANE.md`](docs/MACOS_CONTROL_PLANE.md).

## Roadmap

| Phase | Goal |
|---|---|
| Preflight | Provider-policy audit and safe bypass/failure modes. |
| Integration spike | Thin OpenCode adapter + GLM/MiniMax model assignment. |
| P0 Safety Kernel | Authoritative task state and deterministic verification boundary. |
| P1 Resource Domain | QuotaPool, append-only QuotaBinding/ConsumptionRule, catalog snapshots. |
| P2 Observability | Z.AI/MiniMax quota collectors and reconciliation. |
| P3 Temporal Scheduler | Pace-based deterministic routing and replayable scoring traces. |
| P4 Ownership/Handoff | Hysteresis, reason-aware transfer budgets, minimal structured handoff. |
| P5 Shadow Mode | Recommend-only dogfooding and matched outcome capture. |
| P6 Active Evidence | Quota survival, routing quality, handoff penalty across reset cycles. |
| P7 Accounting | Per-invocation Cost-to-Green and provider-rule transitions. |
| P8 macOS Clients | Menu Bar, dashboard, WidgetKit after daemon stability. |
| P9 Adaptive Scheduler | Only after sufficient real evidence exists. |

See [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Initial technology direction

- Python 3.12+
- `asyncio`
- Pydantic v2
- SQLite + WAL
- Git worktrees
- pytest + disposable repositories/fake workers
- structured audit/event records
- typed local daemon API / IPC
- local versioned model-catalog snapshots
- SwiftUI/WidgetKit only after backend schema stabilization

## Open-source status

The project is intended to be open source, but **no license has been selected yet**. Until a license is added, the repository remains all rights reserved even if source visibility changes. License selection remains a release blocker.
