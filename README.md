# Personal AI Orchestrator

An open-source, safety-first **resource scheduler for coding agents and multi-model AI workflows**.

> Status: **Pre-alpha / architecture & feasibility stage**

## What problem this project solves

Modern coding-agent users increasingly have access to several model providers, several concrete model SKUs per provider, subscription coding plans, API wallets, and local models at the same time.

The difficult problem is no longer only *which model is strongest?*

It is:

> **Use the right model SKU for the right task, at the right quality, latency, quota burn, and cost — without weakening repository safety or deterministic verification.**

Personal AI Orchestrator combines two layers:

1. a host-owned **Safety Kernel** for task state, worktree isolation, verification, approvals, recovery, and auditability;
2. an explainable **Model Resource Scheduler** for model-SKU selection, shared quota pools, routing, escalation, telemetry, and Cost-to-Green optimization.

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
| - Concrete Model SKUs                         |
| - Capability profiles                         |
| - Worker / Reasoning / Review / Escalation    |
| - Quota-aware routing                         |
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

A task may only advance after host-owned deterministic verification passes.

Model routing, quota optimization, or fallback logic can never bypass this rule.

## Model-SKU-aware scheduling

The scheduler does not treat a vendor as a model.

```text
Provider != Account != Plan != QuotaPool != ModelSKU != RuntimeVariant
```

For example, two models from the same provider may have different coding quality, latency, context limits, price, and task-specific performance while still consuming the same subscription quota pool.

The registry therefore models concrete SKUs and the scarcity boundary they actually consume.

See [`docs/MODEL_RESOURCE_ORCHESTRATION.md`](docs/MODEL_RESOURCE_ORCHESTRATION.md).

## What the scheduler should optimize

Public benchmarks are useful as priors, but real routing should increasingly use observed local outcomes:

- Pass@1
- attempts-to-green
- time-to-green
- tokens-to-green
- quota-to-green
- monetary cost-to-green
- verifier failure rate
- regression rate
- context/runtime fit
- task-family-specific reliability

The first scheduler is deliberately deterministic and explainable. Adaptive routing comes only after enough real telemetry exists.

## Quota truth matters

Quota state is normalized and confidence-labelled:

```text
AVAILABLE / LIMITED / CRITICAL / EXHAUSTED / UNKNOWN

EXACT      provider-reported truth
ESTIMATED  derived from local telemetry/signals
UNKNOWN    no reliable estimate
```

The project must never display a guessed shared-plan value as a fake exact per-model percentage.

Quota reserves allow scarce premium capacity to be protected for architecture, blockers, release gates, or repeated-failure escalation instead of being exhausted by routine review work.

## macOS experience

The orchestrator core remains headless and cross-client.

A first-party macOS client is planned with three complementary surfaces:

- **Menu Bar** — live status and quick routing controls;
- **Desktop / Notification Center Widgets** — glanceable quota health, current model, and current run;
- **Full App** — providers, plans, model SKUs, pools, routing rules, runs, and analytics.

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
10. Dynamic model routing cannot weaken verification, approval, or isolation policy.
11. Automatic paid overage is forbidden unless explicitly enabled by user policy.
12. Multi-agent execution is justified by task complexity; it is not the default merely because several models are available.

## Planned phases

| Phase | Goal |
|---|---|
| Spike / PoC | Prove Codex, Claude Code, and OpenCode can be driven safely through a common supervised path. |
| P0 Safety Kernel | Durable task/run/audit state, worktree management, writer locking, supervision, fail-closed recovery. |
| P1 Verification | Trusted deterministic build/test/diff verification and explicit `VERIFIED` gate. |
| P2 Multi-worker | Provider plurality, reviewer separation, strict task ownership. |
| P2.5 Model Resource Registry | Concrete model SKUs, shared quota pools, pools, task profiles, explainable routing. |
| P3 Quota Governor | Provider/plan collectors, confidence-labelled quota, reserves, fallback and harvest policy. |
| P4 Clients / macOS | Local API, CLI, Menu Bar, WidgetKit widgets, dashboard, DeskPet integration. |
| P5 Cost-to-Green | Real task analytics by model SKU, runtime, language/framework, role and risk. |
| P6 Adaptive Scheduler | Evidence-driven routing improvement with deterministic rollback and hard constraints. |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for acceptance gates.

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
