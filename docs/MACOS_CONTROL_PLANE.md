# macOS Control Plane

## Goal

The macOS experience is an optional control plane for Personal AI Orchestrator. The core scheduler remains headless and usable without the macOS app, DeskPet, or any specific UI.

The macOS client should provide three complementary surfaces:

1. **Menu Bar** — live status and quick bounded controls.
2. **Desktop / Notification Center Widgets** — glanceable quota, routing, and current-run state.
3. **Full App** — complete model-resource, routing, analytics, and configuration UI.

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

The macOS client is never the source of truth for task state, quota truth, activation authority, or routing history.

## Production ACTIVE authority

The UI may display routing modes, but an `ACTIVE` button is only a request to the daemon. The daemon must reject production ACTIVE routing until the Safety Kernel / deterministic verification / Shadow-validation gate is satisfied.

A UI state toggle cannot bypass that gate.

## Menu Bar

Recommended information:

- orchestrator running/paused state;
- current task and selected model SKU / execution target;
- current worker role;
- plan/quota health summary;
- active routing mode;
- protected quota pools;
- warnings and unavailable/exhausted targets.

Recommended quick actions:

- Balanced;
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
Shadow / Balanced

MiniMax M3    Healthy
GLM-5.3       Quota Unknown

Running: M3 / Builder
```

### Medium widget

Suggested content:

```text
PLAN HEALTH
MiniMax    Healthy / EXACT
Z.AI       Unknown / ESTIMATED

CURRENT TASK
Swift debugging -> M3 / MiniMax plan

Mode: Shadow / Balanced
```

### Large widget

May additionally show:

- task/risk class;
- selected model + execution target + role;
- admission/selection explanation;
- protected reserve state;
- recent target utilization;
- warning conditions.

Example explanation:

```text
Selected M3 / MiniMax subscription target
+ capability floor passed
+ predicted burn fits usable headroom
+ quota snapshot fresh
- GLM target excluded: required quota window unknown
```

## Quota evidence language

The UI must keep **confidence** separate from **measurement/source method**.

Confidence:

```text
EXACT
ESTIMATED
UNKNOWN
```

Measurement/source examples:

```text
PROVIDER_REPORTED
LOCALLY_MEASURED
INFERRED
MANUAL
```

Do not create one mixed enum such as `EXACT / LOCALLY_MEASURED / UNKNOWN`. Those are different dimensions.

Examples:

```text
MiniMax plan
  Confidence: EXACT
  Source: PROVIDER_REPORTED

Local spend estimate
  Confidence: ESTIMATED
  Method: LOCALLY_MEASURED

Provider X quota
  Confidence: UNKNOWN
```

The UI must never imply precision the backend does not possess.

## Widget interactions

Only bounded, reversible actions belong in widgets.

Good candidates:

- request a safe routing mode;
- enable/disable quota protection;
- pause/resume new task dispatch;
- open the relevant dashboard page.

Not appropriate for widgets:

- arbitrary routing expressions;
- credential entry;
- repository integration/merge actions;
- complex approval workflows;
- free-form shell or prompt execution.

## Full App

The full app owns configuration UX while the daemon owns configuration state and validates every write.

### Dashboard

- current run;
- current model SKU / execution target / role;
- provider and plan health;
- scheduler objective + activation state;
- alerts;
- recent routing decisions.

### Providers, Plans, Quota Pools, and Execution Targets

The UI must visually preserve both commercial scarcity and logical-model relationships.

Commercial hierarchy:

```text
Provider
  Account
    Plan
      Quota Pool
```

Model/runtime hierarchy:

```text
Model SKU
  Execution Target
    Account / runtime path
    Quota binding
    Cost / payment policy
```

This is more accurate than forcing `QuotaPool -> ModelSKU -> RuntimeVariant`. A single logical model may have subscription and PAYG execution targets, and several model SKUs may consume one shared plan pool.

### Models

Per-ModelSKU view should include:

- enabled/disabled;
- capability prior/local posterior;
- catalog snapshot provenance;
- supported candidate pools;
- available execution targets.

Per-ExecutionTarget view should include:

- account/runtime path;
- current availability;
- quota-pool binding;
- consumption rule/multiplier;
- cost/payment policy;
- observed latency;
- target-specific local performance;
- routing restrictions.

### Pools

Users can add/remove/reorder ModelSKUs or explicit ExecutionTargets in:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

Pools only create candidate sets. Hard eligibility and admission still apply.

### Routing

Expose deterministic rules first.

The UI should make the three-stage scheduler visible:

```text
Eligibility
  -> capability/risk/runtime/payment/quota-truth gates

Admission
  -> predicted burn vs usable headroom

Ranking
  -> quality / success / time / cost / latency / temporal scarcity
```

Hard gates are not sliders in a weighted score. A user can configure policy, but the UI must not imply that a large quality weight can override reserve, payment, verification, or unknown hard quota.

### Runs

Show a timeline such as:

```text
Task classified
-> candidate targets evaluated
-> GLM target excluded: quota unknown
-> MiniMax target admitted
-> M3 selected
-> worker launched
-> worker finished
-> deterministic verifier failed
-> repair attempt
-> verifier passed
-> review gate
```

### Routing decision detail

A decision page should show immutable references:

- task-state version;
- catalog snapshot ID;
- policy snapshot ID;
- quota snapshot IDs;
- selected execution target;
- excluded candidates and reasons;
- admission headroom / predicted burn where available.

Historical screens must render the old decision from its referenced snapshots, not silently recompute it from current quota/provider policy.

### Analytics

Primary metrics:

- Pass@1;
- attempts-to-green;
- time-to-green;
- quota-to-green;
- cost-to-green;
- regression rate;
- model and execution-target utilization by role/task family;
- quota consumed by task class;
- avoidable escalation / low-value premium calls;
- handoff penalty.

## Local API contract

The macOS app consumes typed daemon endpoints/events rather than reading scheduler database/cache files directly.

Expected read surfaces:

- orchestrator/safety status;
- production ACTIVE eligibility;
- plans/quota pools and immutable observation references;
- model registry + execution targets;
- current runs;
- routing decisions/explanations;
- analytics summaries;
- alerts.

Expected write surfaces:

- requested routing mode/objective;
- pool membership/order;
- quota reserve/protection policy;
- model/target enable/disable;
- pause/resume dispatch;
- bounded approval actions defined by the Safety Kernel.

Writes are validated and audited by the core.

## Local browser bridge rule

Any development dashboard bridge using loopback HTTP must still defend against browser-based confused-deputy requests. At minimum:

- bind to loopback only;
- validate Host and Origin for API calls;
- require JSON for state-changing requests;
- rate-limit billable connection tests;
- allowlist provider-login actions;
- never expose credential values.

A production macOS client should prefer typed local IPC / Unix Domain Socket when practical.

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

The exact physical layout can evolve, but dependency direction remains:

`clients -> core API`, never `core -> macOS UI`.
