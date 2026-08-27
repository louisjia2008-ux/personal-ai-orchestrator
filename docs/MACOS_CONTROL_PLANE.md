# macOS Control Plane

> **Status: deferred until after OpenCode dogfooding and daemon/API stabilization.**

## Goal

The macOS experience remains a planned first-party control plane for Personal AI Orchestrator, but it is **not part of the MVP critical path**.

The first product hypothesis must be proven with a headless Python daemon + CLI + OpenCode integration before SwiftUI or WidgetKit implementation begins.

The eventual macOS client should provide three complementary surfaces:

1. **Menu Bar** — live status and quick, bounded controls.
2. **Full App** — complete subscription/quota/routing/run analytics and configuration UI.
3. **WidgetKit** — glanceable quota health, current model/run, and safe reversible actions.

DeskPet remains a separate optional client of the same local daemon API.

## Why implementation is deferred

The macOS UI depends on state that is still expected to change during MVP dogfooding:

- quota observation schema;
- `QuotaBinding` and `ConsumptionRule` representation;
- routing explanation/scoring trace;
- temporal scarcity state;
- ownership/handoff records;
- provider reconciliation events;
- task/run event schemas.

Implementing SwiftUI and WidgetKit before these contracts stabilize would create avoidable duplicate migrations across Python and Swift.

The sequence should therefore be:

```text
Headless daemon
  -> CLI dogfooding
  -> freeze/version local API + event JSON schemas
  -> Menu Bar
  -> Full Dashboard
  -> WidgetKit
  -> optional DeskPet integration
```

## Architectural boundary

```text
                  Headless Orchestrator Core
                         local API / IPC
                               |
              +----------------+----------------+
              |                |                |
          macOS App           CLI             DeskPet
              |
      +-------+--------+
      |       |        |
   MenuBar  Widget   Dashboard
```

The client is never the source of truth for task state, quota state, routing state, or safety decisions.

Dependency direction remains:

`clients -> core API`, never `core -> macOS UI`.

## Domain hierarchy shown by the UI

The UI must preserve the commercial scarcity boundary:

```text
Provider
  Account
    Plan
      Quota Pool
```

Models are shown as resources bound to those pools:

```text
Model SKU
  -> Quota Binding
  -> active Consumption Rule
```

The UI must not fabricate a per-model remaining percentage when only a shared quota-pool observation exists.

Reasoning/high-speed/model variants should initially display upstream/OpenCode model-variant metadata rather than requiring a separate local `RuntimeVariant` hierarchy.

## Menu Bar

Recommended information:

- orchestrator running/paused state;
- current task and selected model SKU;
- current implementation owner/role;
- plan/quota health summary;
- active routing mode;
- protected quota pools;
- quota confidence warnings;
- exhausted/unavailable providers.

Recommended bounded actions:

- Balanced / Save Quota / Max Quality / Low Latency mode;
- pause/resume new dispatch;
- protect/unprotect a configured quota pool;
- open the relevant dashboard view.

The menu bar should not expose destructive repository actions directly.

## Full App

### Dashboard

- current run;
- current selected model/owner;
- provider/plan/quota-pool health;
- current routing mode;
- alerts;
- recent routing decisions and explanations.

### Providers, Accounts, Plans & Quota Pools

Show:

- provider/account identity;
- commercial plan;
- shared quota pools;
- remaining/reset state when observable;
- source/confidence;
- reserve/protection policy;
- last provider reconciliation;
- active time-aware quota bindings and consumption rules.

### Models

Static public metadata should be sourced upstream where practical (for example models.dev aligned data) and combined with local scheduler state:

- canonical model ID;
- enabled/disabled;
- quota binding;
- active consumption rule;
- public context/capability/pricing metadata;
- observed latency/reliability;
- local task-performance summaries;
- candidate-pool memberships;
- routing restrictions.

### Candidate Pools

Users may configure model membership/priority for:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

Pools are candidate sets. The deterministic scheduler still applies hard eligibility, reserve policy, temporal scarcity, task fit, and observed reliability.

### Routing / Explanation

The UI should render the actual machine-readable scoring trace, not an independently written explanation.

For a decision, show:

```text
Selected: MiniMax M3

Capability fit            +0.24
Observed reliability      +0.18
Quota health              +0.16
Temporal scarcity         -0.03
Latency                   -0.04
--------------------------------
Final score                0.51

GLM-5.3 excluded/protected:
quota reserve policy
```

Exact visual design may evolve, but the explanation must remain trace-derived and auditable.

### Runs / Ownership

Show a versioned timeline such as:

```text
Task classified
-> M3 selected / ownership v1
-> verifier failed
-> M3 attempt 2 / state v2
-> escalation threshold reached
-> ownership transfer approved
-> GLM selected / handoff v3
-> verifier passed
```

Ownership-transfer cooldown/limits and transfer reasons should be visible.

### Analytics

Primary metrics after sufficient real data exists:

- Pass@1;
- attempts-to-green;
- time-to-green;
- native quota-to-green;
- direct monetary spend;
- temporal scarcity/effective cost;
- regression/verifier failure rate;
- unused quota at reset;
- quota survival / reserve breaches;
- rework after handoff;
- model utilization by task family.

## WidgetKit

Widgets are deliberately the last macOS surface to implement because they should consume a stable read model rather than mirror mutable internal entities.

### Small

```text
ORCHESTRATOR
Balanced

MiniMax   Healthy
Z.AI      Conserve

M3 / Builder
```

### Medium

May show:

- plan/quota health;
- confidence (`provider reported`, `estimated`, `unknown`);
- current task/model;
- routing mode;
- protected reserve state.

### Large

May additionally show:

- current selection scoring summary;
- recent quota utilization;
- current ownership/handoff state;
- warnings.

Only bounded reversible actions belong in widgets, such as mode changes, quota protection, pause/resume dispatch, or opening a dashboard page.

Do not put credential entry, arbitrary routing expressions, repository merge actions, free-form prompts, or complex approvals in widgets.

## Local API contract

The client consumes typed/versioned daemon endpoints/events rather than reading SQLite or scheduler files directly.

Expected reads:

- daemon status;
- providers/accounts/plans/quota pools;
- quota observations and confidence;
- active quota bindings/consumption rules;
- models and candidate pools;
- current runs/ownership state;
- routing scoring traces;
- analytics summaries;
- alerts.

Expected audited writes:

- routing mode;
- candidate-pool configuration;
- quota reserve/protection policy;
- model enable/disable;
- pause/resume dispatch;
- bounded approval actions explicitly defined by the Safety Kernel.

## Open-source requirement

A user who never installs the macOS client or DeskPet must still be able to run and use the headless orchestrator and CLI.

The macOS client is a convenience and observability layer, not the scheduler itself and not the project's technical moat.
