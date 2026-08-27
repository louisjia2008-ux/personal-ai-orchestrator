# Personal AI Orchestrator

An open-source, safety-first **quota-aware resource scheduler for coding agents**.

> Status: **Pre-alpha / architecture & feasibility stage**

## What problem this project solves

Advanced coding-agent users may hold several paid AI subscriptions at the same time. Those plans are not interchangeable:

- different model SKUs share different quota pools;
- burn rates may change by model, time window, or provider policy;
- quota resets on rolling or billing windows;
- unused subscription capacity may expire;
- a premium reasoning plan can be exhausted early while another paid plan remains mostly idle.

The difficult question is therefore not only:

> Which model is strongest?

It is:

> **How should scarce, time-bounded AI subscription capacity be rationed across a long-running coding workload without wasting paid quota or reducing verified quality?**

Personal AI Orchestrator combines:

1. a host-owned **Safety Kernel** for task state, worktree isolation, deterministic verification, approvals, recovery, and auditability;
2. a deterministic **Quota-Aware Scheduler** for model selection, subscription reserves, escalation, task ownership, and measured Cost-to-Green.

## Core architecture

```text
                    OpenCode
                       |
                       v
        Personal AI Orchestrator Core
+------------------------------------------------+
| Safety Kernel                                  |
| - authoritative Task / Run state               |
| - Git worktree isolation                       |
| - single-writer ownership                      |
| - deterministic verifier                       |
| - approval / recovery / audit                  |
|                                                |
| Quota-Aware Scheduler                          |
| - Account / Plan / QuotaPool                   |
| - QuotaBinding / ConsumptionRule               |
| - candidate pools                              |
| - reserve / escalation policy                  |
| - temporal scarcity scoring                    |
| - ownership / structured handoff               |
+------------------------------------------------+
           |                           |
           v                           v
    models.dev metadata          quota collectors
                                      |
                              Z.AI / MiniMax first
```

The MVP deliberately targets **OpenCode + Z.AI/GLM + MiniMax** before broadening to more execution clients or providers.

## Important invariant

A worker saying `COMPLETE` is **not** task completion.

A task becomes green only after host-owned deterministic verification succeeds.

Model routing, quota optimization, fallback, or escalation can never bypass the Safety Kernel.

## Why this is not another generic model router

The project is not primarily trying to outperform learned meta-routers at predicting which model is smartest for a prompt.

Its distinctive resource is **subscription scarcity**.

Ordinary API routing often treats cost as dollars per token. Subscription capacity behaves differently:

```text
remaining quota
+ time until reset
+ recent burn velocity
+ expected workload
+ fallback quality
= current scarcity value
```

Ten percent of a plan may be extremely valuable when only 8% remains and reset is days away, but nearly disposable when abundant quota expires in a few hours.

The scheduler should make that tradeoff explicit and auditable.

## Domain model

The commercial boundary remains explicit:

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

This distinction matters because:

- one provider may have multiple accounts;
- one account may have multiple plans;
- multiple model SKUs may share one pool;
- provider policy can move models between pools or change burn multipliers over time.

`QuotaBinding` and `ConsumptionRule` are time-aware so provider policy changes do not require schema redesign or corrupt historical analytics.

The project must never display a guessed shared-plan value as a fake exact per-model percentage.

See [`docs/MODEL_RESOURCE_ORCHESTRATION.md`](docs/MODEL_RESOURCE_ORCHESTRATION.md).

## Reuse model catalogs instead of rebuilding them

Static public model facts should come from upstream metadata where practical, with `models.dev` as the preferred initial source for OpenCode-aligned data.

The orchestrator should focus on information upstream catalogs cannot know about the user:

- accounts and subscriptions;
- shared quota pools;
- quota observations and confidence;
- provider-specific consumption rules;
- reserve/protection policy;
- local coding-task outcomes;
- ownership and handoff history.

## Quota truth matters

Quota observations are confidence-labelled:

```text
EXACT       directly provider-reported for the relevant pool
ESTIMATED   inferred from incomplete signals/local telemetry
UNKNOWN     no defensible estimate exists
```

Availability is normalized separately:

```text
AVAILABLE / LIMITED / CRITICAL / EXHAUSTED / UNKNOWN
```

Provider visibility may get worse over time. An observation is allowed to degrade from `EXACT` to `ESTIMATED` or `UNKNOWN`; the UI/API must expose that honestly.

Reserve policies protect scarce premium capacity for genuine blockers rather than routine work.

## Deterministic and explainable routing first

The MVP scheduler is a transparent rule/weighted-score engine, not an opaque model-selection prompt or learned router.

For every candidate it should record the exact scoring trace:

- raw values;
- normalized values;
- weights;
- hard exclusions;
- quota/reserve effects;
- temporal scarcity contribution;
- final score.

Human-readable explanations must be generated from that same trace.

Identical authoritative inputs must produce identical routing output.

## Task ownership and cross-model handoff

Dynamic model selection must not create agent thrashing.

The orchestrator persists:

- current implementation owner;
- frozen architecture decisions/invariants;
- reviewer role;
- ownership-transfer history;
- versioned structured task state.

Ownership transfer uses hysteresis: minimum attempts, transfer limits/cooldowns, and explicit reason codes.

When a task escalates from one model to another, the new model receives a structured handoff rather than an uncontrolled transcript dump.

## What the MVP must prove

The project should earn expansion by passing three falsifiable experiments.

### 1. Quota survival / utilization

Does scheduling prevent a scarce premium plan from exhausting prematurely while reducing unused quota in another paid plan?

### 2. Routing quality

Does deterministic routing match or beat manual routing on verified quality, attempts-to-green, time-to-green, quota-to-green, and regression rate?

### 3. Handoff penalty

Can a structured escalation between models preserve success rate without unacceptable rework or context overhead?

If these three claims fail, adding more providers, UI, or adaptive routing does not fix the product thesis.

## MVP scope

Initial critical path:

```text
OpenCode
+ Z.AI/GLM
+ MiniMax
+ Python headless daemon
+ CLI
+ Safety Kernel
+ quota collectors
+ deterministic scheduler
+ structured handoff
```

Explicitly **not required for MVP**:

- ACP;
- LiteLLM;
- Claude/Codex adapters;
- macOS UI or WidgetKit;
- DeskPet;
- adaptive/ML routing;
- generalized local-GPU scheduling;
- a hand-maintained global model catalog.

ACP and generic gateways may become useful later, but they do not solve the core subscription-scarcity problem.

## macOS experience — planned after dogfooding

The long-term product still includes a first-party macOS control plane:

- Menu Bar status and bounded controls;
- full SwiftUI dashboard;
- WidgetKit desktop/Notification Center widgets;
- DeskPet as an optional independent client.

Implementation is intentionally deferred until the headless daemon API and state schemas survive real OpenCode dogfooding. CLI is the MVP interface.

See [`docs/MACOS_CONTROL_PLANE.md`](docs/MACOS_CONTROL_PLANE.md).

## Cost-to-Green

Public benchmarks are useful priors, but the scheduler should increasingly care about real verified outcomes:

- Pass@1;
- attempts-to-green;
- time-to-green;
- native quota-to-green;
- direct API spend;
- temporal scarcity/opportunity cost;
- verifier failure rate;
- regression rate;
- rework after model handoff.

Subscription quota should remain visible in its native units even if a combined effective-cost score is computed.

## Planned phases

| Phase | Goal |
|---|---|
| MVP integration spike | Prove safe OpenCode model assignment with Z.AI/GLM and MiniMax plus quota observability. |
| P0 Safety Kernel | Durable task/run/audit state, worktree management, writer locking, supervision, fail-closed recovery. |
| P1 Verification | Trusted deterministic build/test/diff verification and explicit `VERIFIED` gate. |
| P2 Subscription Resource Domain | Account/Plan/QuotaPool, QuotaBinding, ConsumptionRule, upstream model metadata. |
| P3 Quota Observability | Provider reconciliation, confidence-labelled quota, reset/window data and ToS audit. |
| P4 Deterministic Scheduler | Candidate pools, reserve/escalation rules, scoring trace, temporal scarcity. |
| P5 OpenCode Dogfooding | Real task routing, ownership hysteresis, versioned handoff and audit. |
| P6 MVP Evidence | Quota-survival, routing-quality and handoff-penalty experiments. |
| P7 Clients / macOS | Freeze daemon contract, then Menu Bar, dashboard, widgets and optional DeskPet client. |
| P8 Adaptive Scheduler | Only after enough real evidence exists; bounded and reversible. |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for acceptance gates.

## Initial technology direction

- Python 3.12+
- `asyncio`
- Pydantic typed models
- SQLite + WAL
- Git worktrees
- pytest + disposable Git repositories + fake workers
- structured JSON audit logs first; OpenTelemetry later
- typed local daemon API / IPC
- `models.dev`-aligned upstream model metadata where practical
- SwiftUI + WidgetKit only after daemon/API stabilization

The MVP intentionally avoids public SaaS infrastructure, distributed scheduling, opaque ML routers, generalized gateway replacement, and premature desktop UI work.

## Safety principles

1. Workers do not write directly to the protected main repository.
2. Implementation tasks use host-controlled workspaces/worktrees.
3. A writable task workspace has at most one active implementation owner.
4. Agents do not create or destroy their own safety boundaries.
5. Natural-language prompts cannot define executable verifier commands.
6. Worker completion and host verification are separate states.
7. Unknown/inconsistent authoritative task state fails closed.
8. Credentials remain provider-native or in approved secret stores.
9. Scheduler decisions cannot weaken verification, approval, or isolation policy.
10. Automatic paid overage is forbidden unless explicitly enabled.
11. High-risk/irreversible permission remains a Safety Kernel decision, not a quota decision.

## Open-source status

The project is intended to be open source, but **no license has been selected yet**. Until a license is added, the repository should be treated as all rights reserved even if the source becomes publicly visible.

Selecting an explicit license remains a blocker before the first public open-source release.
