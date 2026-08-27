# macOS Control Plane

## Goal

The macOS experience is an optional control plane for Personal AI Orchestrator. The core scheduler must remain headless and usable without the macOS app, DeskPet, or any specific UI.

The macOS client should provide three complementary surfaces:

1. **Menu Bar** — live status and quick controls.
2. **Desktop / Notification Center Widgets** — glanceable quota, routing, and current-run state.
3. **Full App** — complete model registry, pool, routing, analytics, and configuration UI.

DeskPet is a separate optional client of the same local orchestrator API.

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

The macOS client must not become the source of truth for task state, quota state, or routing state.

## Menu Bar

The menu bar is the fastest interactive surface and may update more frequently than widgets.

Recommended information:

- orchestrator running/paused state;
- current task and selected model SKU;
- current worker role;
- plan/quota health summary;
- active routing mode;
- protected quota pools;
- warnings and exhausted providers.

Recommended quick actions:

- Auto / Balanced mode;
- Max Quality;
- Save Quota;
- Low Latency;
- pause scheduler;
- protect/unprotect a configured quota pool;
- open full dashboard.

The menu bar should not expose destructive repository actions directly.

## Desktop Widget

Widgets are for glanceable state and a small number of safe actions. They are not the full scheduler editor.

### Small widget

Suggested content:

```text
ORCHESTRATOR
Balanced

M3       Healthy
GLM      Conserve
GPT      Healthy

Running: M3 / Builder
```

### Medium widget

Suggested content:

```text
PLAN HEALTH
MiniMax    39% used / Healthy
Z.AI       82% used / Conserve
Codex      61% used / Healthy

CURRENT TASK
Swift debugging -> M3

Mode: Balanced
```

Quota percentages must be labelled by confidence/source:

- Provider reported
- Estimated
- Unknown

### Large widget

May additionally show:

- task/risk class;
- selected model and role;
- selection explanation;
- protected reserve state;
- recent model utilization;
- warning conditions.

Example selection explanation:

```text
Selected M3
+ strong local Swift debugging history
+ MiniMax quota healthy
+ medium-risk task
- GLM reserve protected
```

## Widget interactions

Only bounded, reversible actions belong in widgets.

Good candidates:

- switch routing mode;
- enable/disable quota protection;
- pause/resume new task dispatch;
- open the relevant dashboard page.

Not appropriate for widgets:

- editing arbitrary routing expressions;
- reordering large model pools;
- credential entry;
- repository integration/merge actions;
- complex approval workflows;
- free-form prompt entry.

## Full App

The full app owns configuration UX while the daemon owns configuration state.

Recommended sections:

### Dashboard

- current run;
- current model SKU/role;
- provider and plan health;
- scheduler mode;
- alerts;
- recent routing decisions.

### Providers & Plans

Hierarchical view:

```text
Provider
  Account
    Plan
      Quota Pool
        Model SKU
          Runtime Variant
```

This hierarchy is mandatory so shared quotas are represented honestly.

### Models

Per-SKU view should include:

- enabled/disabled;
- provider/account/plan/quota-pool relationship;
- context/runtime properties;
- price data where known;
- observed latency;
- capability prior;
- local performance score;
- supported pools;
- routing restrictions.

### Pools

Users can add/remove/reorder concrete model SKUs in:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

Pools are candidate sets; the scheduler still applies quota, risk, cost, and performance constraints.

### Routing

Expose deterministic rules first.

Examples:

```text
Task risk = high           -> Reasoning pool
Worker failures >= 2       -> Escalation pool
Quota usage >= threshold   -> Conservation policy
Requires vision            -> vision-capable candidates only
Context requirement high   -> exclude insufficient context variants
```

Every active rule should be explainable and testable.

### Runs

Show a timeline such as:

```text
Task classified
-> M3 selected
-> worker launched
-> verifier failed
-> M3 repair attempt
-> verifier failed
-> escalation triggered
-> GLM-5.3 selected
-> verifier passed
-> review gate
```

### Analytics

Primary metrics:

- Pass@1;
- attempts-to-green;
- time-to-green;
- quota-to-green;
- cost-to-green;
- regression rate;
- model utilization by role/task family;
- quota consumed by task class;
- avoidable escalation / low-value premium-model calls.

A useful visualization is a task-specific Pareto frontier of expected quality vs effective cost, with quota health and latency encoded separately.

## UI status language

Quota state should use a small normalized vocabulary:

```text
Healthy
Conserve
Critical
Exhausted
Unknown
```

Do not imply precision the backend does not possess.

Examples:

```text
Z.AI plan: 82% used (provider reported)
MiniMax plan: ~39% used (estimated)
Provider X: remaining quota unknown
```

## Local API contract

The macOS app should consume typed daemon endpoints/events rather than reading scheduler database files directly.

Expected read surfaces:

- orchestrator status;
- plans/quota pools;
- model registry;
- current runs;
- routing decisions/explanations;
- analytics summaries;
- alerts.

Expected write surfaces:

- routing mode;
- pool membership/order;
- quota reserve policy;
- model enable/disable;
- pause/resume dispatch;
- bounded approval actions defined by the Safety Kernel.

Writes must be audited by the core.

## Open-source requirement

The macOS UI is a first-party client, not a dependency of the open-source core.

A user must be able to run the headless daemon and CLI on a supported non-macOS environment without compiling SwiftUI/WidgetKit code.

Suggested repository separation:

```text
core/
providers/
adapters/
clients/
  cli/
  macos/
    app/
    menubar/
    widgets/
benchmarks/
docs/
```

The exact physical layout can evolve, but dependency direction must remain:

`clients -> core API`, never `core -> macOS UI`.
