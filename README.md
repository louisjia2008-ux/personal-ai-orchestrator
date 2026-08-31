# Personal AI Orchestrator

An open-source, safety-first **resource scheduler for coding agents and multi-model AI workflows**.

> Status: **Pre-alpha / architecture & feasibility stage**

## What problem this project solves

Modern coding-agent users increasingly have access to multiple providers, model SKUs, subscription coding plans, API wallets, and local models at the same time.

The difficult problem is no longer only *which model is strongest?*

It is:

> **Use the right execution target for the right task, at the required quality, latency, quota burn, and cost — without weakening repository safety or deterministic verification.**

Personal AI Orchestrator combines two layers:

1. a host-owned **Safety Kernel** for task state, worktree isolation, verification, approvals, recovery, and auditability;
2. an explainable **Model Resource Scheduler** for model/target selection, shared quota pools, admission, routing, escalation, telemetry, and Cost-to-Green optimization.

## Core architecture

```text
OpenCode / CLI / macOS / DeskPet / future clients
                     |
                     v
        Personal AI Orchestrator Core
+------------------------------------------------+
| Safety Kernel                                  |
| - Task / Run state                             |
| - Git worktree isolation                      |
| - Single-writer ownership                     |
| - Deterministic verifier                      |
| - Approval / recovery / audit                 |
|                                                |
| Model Resource Scheduler                      |
| - Provider / Account / Plan                   |
| - Shared Quota Pools                          |
| - Model SKUs + Execution Targets              |
| - Capability profiles                         |
| - Hard eligibility + quota admission          |
| - Deterministic ranking / explanation         |
| - Cost-to-Green telemetry                     |
+------------------------------------------------+
                     |
             worker/gateway adapters
                     |
        +------------+-------------+
        |            |             |
      Codex       Claude Code    OpenCode
                                   |
                       GLM / MiniMax / DeepSeek /
                       other API or local models
```

## Important invariant

A worker saying `COMPLETE` is **not** task completion.

A task may only advance after host-owned deterministic verification passes. Model routing, quota optimization, fallback, or UI controls can never bypass this rule.

## Resource identities

The scheduler does not collapse vendor, model, commercial plan, and execution path into one identity.

```text
Provider != Account != Plan != QuotaPool != ModelSKU != ExecutionTarget
```

A `ModelSKU` is the logical model/capability identity. An `ExecutionTarget` is the concrete account/runtime/commercial path used to run it.

For example:

```text
GLM-5.3
  -> Z.AI Coding Plan via OpenCode
  -> Z.AI PAYG API
```

Those two targets may share model capability while having different quota, payment, availability, and credential boundaries.

`RuntimeVariant` is not a required first-class MVP entity; lightweight runtime/variant metadata may live on `ExecutionTarget` until evidence justifies more complexity.

See [`docs/MODEL_RESOURCE_ORCHESTRATION.md`](docs/MODEL_RESOURCE_ORCHESTRATION.md).

## Constraint-first scheduling

The first production scheduler does **not** run one global weighted score across every model.

It uses three stages:

```text
1. hard eligibility
   -> capability/risk/runtime/payment/quota-truth gates

2. quota/task admission
   -> predicted task burn must fit usable headroom

3. deterministic ranking
   -> quality, success prior, time/cost-to-green, latency,
      temporal surplus/conservation, pool priority
```

A strong capability score cannot compensate for a failed reserve, payment, runtime, or quota-truth constraint.

## Quota truth matters

Quota state is normalized and confidence-labelled:

```text
AVAILABLE / LIMITED / CRITICAL / EXHAUSTED / UNKNOWN

EXACT      provider-reported truth with clear semantics
ESTIMATED  derived from useful but incomplete signals
UNKNOWN    no reliable precise value
```

Measurement/source method is separate from confidence. `LOCALLY_MEASURED` describes how a value was obtained; it does not by itself mean the value is `EXACT`.

The project never displays shared-plan truth as fake exact per-model quota.

### Multiple reset windows

For a reliable active window:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

For simultaneous binding windows, an unknown window makes the **routing** pace unknown rather than being silently discarded. A healthy 5-hour window cannot hide an unknown weekly limit.

### Pace is not admission

A near-reset `HARVEST` signal only says capacity is temporally surplus relative to time. It does not prove there is enough absolute quota for the next task.

For metered subscription traffic the scheduler uses a conservative admission check:

```text
usable_headroom = minimum_remaining_fraction
                - reserve_fraction
                - uncertainty_margin

predicted_burn = task_burn_p90
               * consumption_multiplier
```

If predicted burn does not fit usable headroom, the task is not admitted even when pace is `HARVEST`.

## Replayability

Routing explanations must be based on the facts known at decision time.

The registry therefore keeps quota membership and burn semantics as append-only temporal facts, and quota observations have stable immutable snapshot IDs. Routing decisions can record:

```text
catalog_snapshot_id
policy_snapshot_id
quota_snapshot_ids[]
```

Later provider corrections cannot silently rewrite why an old decision was made.

## What the scheduler should optimize

Public benchmarks are useful as priors, but real ranking should increasingly use observed local outcomes:

- Pass@1;
- attempts-to-green;
- time-to-green;
- tokens-to-green;
- quota-to-green;
- monetary cost-to-green;
- verifier failure rate;
- regression rate;
- context/runtime fit;
- task-family-specific reliability.

Adaptive routing comes only after enough real telemetry exists, and may tune ranking only among candidates that already passed hard constraints.

## ACTIVE routing gate

Resource-scheduler code and Shadow Mode can be developed before the full execution stack is complete, but **production ACTIVE routing is not authorized merely because the scheduler can produce a recommendation**.

Before production ACTIVE switching, the project requires:

1. P0 Safety Kernel authority for the execution path;
2. P1 deterministic verification authority;
3. fail-closed adapter decision validation;
4. safe BYPASS;
5. real Shadow Mode evidence over multiple relevant quota reset cycles;
6. no unacceptable verified-quality or regression penalty.

Disposable integration spikes may exercise session-scoped ACTIVE switching without satisfying this production gate.

## macOS experience

The orchestrator core remains headless and cross-client.

A first-party macOS client is being built with three complementary surfaces:

- **Menu Bar** — live status, quick safe submission, bounded cancellation, and an
  `Open Dashboard` command;
- **Full Dashboard** — native `NavigationSplitView` sections for overview, tasks,
  execution targets, providers, quota, routing, verification, history, and settings;
- **Desktop / Notification Center Widgets** — planned read-only snapshot surface.

P4.2 introduces a local app-bundle builder:

```bash
cd macos/PAOMenuBar
bash scripts/build_app_bundle.sh
open "dist/Personal AI Orchestrator.app"
```

The dashboard is still a client. It reads typed `/v1` daemon views and never reads
Safety Kernel SQLite, credentials, provider auth files, or browser/session stores directly.

DeskPet is an optional client, not a dependency of the project.

See [`docs/MACOS_CONTROL_PLANE.md`](docs/MACOS_CONTROL_PLANE.md).

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
10. Dynamic routing cannot weaken verification, approval, or isolation policy.
11. Automatic paid overage is forbidden unless explicitly enabled by user policy.
12. Multi-agent execution is justified by task complexity; it is not the default merely because several models are available.
13. Unknown binding quota windows cannot be ignored because another window looks healthy.
14. Temporal `HARVEST` cannot override absolute task headroom.
15. Overlapping unsuperseded resource facts fail closed as ambiguous.

## Planned phases

| Phase | Goal |
|---|---|
| Spike / PoC | Prove worker lifecycle and safety boundaries on disposable workspaces. |
| P0 Safety Kernel | Durable task/run/audit state, worktree management, writer locking, supervision, fail-closed recovery. |
| P1 Verification | Trusted deterministic build/test/diff verification and explicit `VERIFIED` gate. |
| P2 Multi-worker | Provider plurality, reviewer separation, strict task ownership. |
| P2.5 Model Resource Registry | Model SKUs, execution targets, shared quota pools, temporal facts, explainable routing inputs. |
| P3 Quota Governor | Provider collectors, immutable quota observations, reserves, temporal scarcity and admission inputs. |
| P3.5 Shadow Validation | Compare scheduler recommendations with real manual choices across reset cycles before production ACTIVE. |
| P4 Clients / macOS | Local API, CLI, Menu Bar, WidgetKit, dashboard, DeskPet integration. |
| P5 Cost-to-Green | Real task analytics by model SKU, execution target, language/framework, role and risk. |
| P6 Adaptive Scheduler | Evidence-driven ranking improvement with deterministic rollback and hard constraints. |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for acceptance gates and [`docs/SCHEDULER_CORRECTNESS.md`](docs/SCHEDULER_CORRECTNESS.md) for the current routing-safety contract.

## Initial technology direction

- Python 3.12+
- `asyncio`
- Pydantic typed models
- SQLite + WAL
- ACP v1 where appropriate
- Git worktrees
- pytest + disposable Git repositories + fake workers
- structured JSON audit logs first; OpenTelemetry later
- Unix Domain Socket / typed local API for clients
- macOS Keychain for orchestrator-owned secrets
- SwiftUI + WidgetKit for the optional macOS client

The MVP intentionally avoids PostgreSQL, Redis, Kafka, Kubernetes, public SaaS APIs, vector databases, opaque ML routers, and distributed scheduling.

## Development policy

Changes should be small, auditable, and milestone-oriented. Prefer one focused branch/PR per engineering milestone. Every milestone should leave the repository in a deterministic, testable state.

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Open-source status

The project is intended to be open source, but **no license has been selected yet**. Until a license is added, the repository should be treated as all rights reserved even if the source becomes publicly visible.

Selecting an explicit license is a release blocker before the first public open-source release.
