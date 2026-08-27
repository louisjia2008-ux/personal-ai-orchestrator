# Model Resource Orchestration

## Purpose

Personal AI Orchestrator is not intended to be another generic LLM gateway or a black-box "best model" router.

Its primary scheduling problem is narrower:

> **Ration scarce, non-fungible AI subscription capacity across long-running coding work without wasting paid quota, exhausting premium models too early, or weakening deterministic verification.**

The project may consider model quality, latency, API price, and observed task performance, but its distinctive resource is **time-bounded subscription capacity**: coding-plan quota that is shared across model SKUs, may have changing burn rules, may reset on rolling or billing windows, and may not be convertible into dollars at a fixed rate.

This layer remains separate from the host-owned Safety Kernel.

---

## Product boundary

The project should not compete primarily on:

- universal provider normalization;
- generic token-price routing;
- a learned meta-router that predicts the strongest model for every prompt;
- editor-to-agent interoperability protocols;
- a model metadata catalog maintained by hand.

Those concerns already have mature or fast-moving ecosystems.

The initial differentiators are:

1. **subscription-aware Quota Governor**;
2. **deterministic, explainable model scheduling**;
3. **stateful cross-model handoff with anti-thrashing ownership rules**;
4. **temporal resource economics**: remaining quota, time-to-reset, burn velocity, and expected future workload all affect current scarcity value.

The first real deployment target is OpenCode with a small number of providers, initially Z.AI/GLM and MiniMax.

---

## Model metadata: reuse, do not rebuild

Static public model facts should come from an upstream model catalog where practical, with `models.dev` as the preferred initial source for OpenCode-aligned metadata.

Examples of upstream facts:

- model identifier and provider;
- context window;
- output limits;
- reasoning/tool/structured-output capabilities;
- public API pricing;
- release/update metadata.

The orchestrator should own only information that a public model catalog cannot know about the local user:

- authenticated accounts;
- subscriptions/plans;
- shared quota pools;
- quota observability and confidence;
- quota bindings and burn rules;
- reserve/protection policy;
- local task performance;
- ownership/handoff state;
- routing policy.

Public benchmark data is an initialization prior, not permanent routing truth.

---

## Domain model

The MVP should keep the commercial scarcity boundary explicit without adding unnecessary runtime ceremony.

```text
Provider
  -> Account
      -> Plan
          -> QuotaPool

ModelSKU  <- upstream model metadata
   |
   +-> QuotaBinding -> QuotaPool
   +-> ConsumptionRule(s)
```

### Provider

Vendor or execution family, for example Z.AI, MiniMax, OpenAI, Anthropic, DeepSeek, or a local runtime family.

### Account

A concrete authentication boundary. Multiple accounts for one provider must be representable.

Credentials are references only; plaintext secrets do not belong in registry records.

### Plan

Commercial entitlement attached to an account, for example:

- coding subscription;
- prepaid API wallet;
- pay-as-you-go API;
- other provider-defined entitlement.

### QuotaPool

The actual scarcity boundary shared by one or more resources.

A pool owns:

- current normalized state;
- measured/estimated remaining capacity when observable;
- reset/window information;
- observation source and confidence;
- reserve/protection policy;
- overage policy;
- last reconciliation time.

Normalized state:

```text
AVAILABLE
LIMITED
CRITICAL
EXHAUSTED
UNKNOWN
```

Evidence confidence:

```text
EXACT       directly reported for the relevant account/plan/pool
ESTIMATED   inferred from incomplete provider signals and local telemetry
UNKNOWN     no defensible remaining-capacity estimate
```

An `UNKNOWN` or shared-pool observation must never be presented as a fake precise per-model remaining percentage.

### ModelSKU

Concrete schedulable model identity.

Model metadata may be imported from an upstream catalog, while local scheduler metadata references the canonical model ID.

### QuotaBinding

Time-aware relation between a model/resource and the quota pool it consumes.

It must support provider policy changes without a database-schema migration.

Suggested fields:

```text
model_id
quota_pool_id
effective_from
effective_until
confidence
source
last_verified_at
```

A model may move to a different pool when provider policy changes.

### ConsumptionRule

Time-aware rule describing how activity consumes a quota pool.

Examples of rule dimensions:

- model multiplier;
- request/point/token unit mapping;
- peak/off-peak windows;
- temporary promotional multipliers;
- cached-input treatment;
- modality-specific consumption;
- effective date range;
- confidence and evidence source.

The scheduler must not hard-code provider burn multipliers into routing code.

### Runtime/model variants

`RuntimeVariant` is **not a required first-class MVP entity**.

Reasoning effort, high-speed modes, or other variants should initially reuse the upstream/OpenCode model-variant representation when possible. Promote variants into a dedicated local entity only when a real quota or routing requirement cannot be expressed otherwise.

---

## Local models are a different scarcity class

Local models should not be forced into subscription quota semantics.

Their scarce resources may instead be:

- RAM / VRAM;
- thermal or power budget;
- device occupancy;
- latency / throughput;
- concurrent inference capacity.

The MVP may treat local models as an unmetered fallback with simple availability metadata. A generalized compute-resource model is deferred until real use justifies it.

---

## Quota observability and reconciliation

Provider quota truth is unstable and may become less observable over time.

Collectors therefore record both **what was observed** and **how trustworthy it was**.

A provider collector should capture, where available:

- account/plan/pool scope;
- remaining capacity or percentage;
- rolling-window and billing reset times;
- provider-reported usage;
- rate-limit headers/signals;
- token/request accounting;
- shared-pool membership;
- observation timestamp;
- source and confidence.

Local request accounting alone is insufficient when the same pool can be consumed outside this orchestrator. Provider reconciliation should be performed whenever a reliable provider usage endpoint exists.

If provider visibility degrades, confidence may move from `EXACT` to `ESTIMATED` or `UNKNOWN`; this is a normal state transition, not an error to hide.

Terms-of-service and account-policy compatibility are release-critical concerns. The orchestrator must not depend on prohibited scraping or automation to claim exact quota truth.

---

## Safety Kernel boundary

The Scheduler recommends resource allocation. The Safety Kernel retains authority.

### Safety Kernel owns

- durable task/run state;
- worktree isolation and writer locks;
- deterministic verification;
- approval and risk gates;
- cancellation/recovery;
- authoritative attempt/verification outcomes;
- whether a high-risk or irreversible action may proceed.

### Scheduler reads

A documented read-only task-health contract, for example:

```text
TaskState
RiskClass
AttemptCount
VerificationResult
OwnershipState
ApprovedCapabilities
```

The Scheduler must not create an independent shadow copy of task health.

### Scheduler may propose

- model selection;
- escalation;
- fallback;
- quota reserve consumption;
- ownership transfer.

Safety policy may deny the proposal.

Quota exhaustion never grants or removes repository permissions by itself.

---

## OpenCode integration

OpenCode is the first execution client because it already supports model/provider plurality and model selection at agent/run/session boundaries.

The preferred integration is **not** repeated mutation of one shared global configuration file.

Preferred shape:

```text
OpenCode task/session
       |
       v
Orchestrator scheduling API
       |
       v
selected concrete model ID
       |
       v
OpenCode-supported model selection/switch surface
```

If per-session switching is supported reliably, use it. Otherwise use launch-level assignment of a concrete model to a child agent.

Requirements:

- concurrent sessions must not overwrite one another's model selection;
- provider-native authentication should remain provider/OpenCode owned where possible;
- cancellation must remain supervised;
- all routing decisions and provider errors must be auditable;
- OpenCode cannot bypass host deterministic verification.

### ACP

ACP is an optional future interoperability adapter, not an MVP dependency. It solves editor/agent interoperability rather than subscription resource scheduling.

### LiteLLM / generic gateways

A generic gateway may be integrated later for non-OpenCode clients or provider normalization. It is deliberately not an MVP dependency while OpenCode already provides the required provider execution surface.

---

## Task profiles and candidate pools

Pools remain useful as candidate sets, not fixed vendor-role assignments.

Initial pools:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

Example task profile:

```yaml
task_type: debugging
language: swift
framework: swiftui
risk: medium
context_requirement: high
previous_failures: 1
requires_long_horizon: true
```

A model may appear in several pools. Selection still depends on task fit, quota scarcity, observed reliability, and policy.

---

## Deterministic explainable scheduling

The first production scheduler is a transparent scoring/rule engine, not an ML router or another LLM prompt.

For each candidate, produce a scoring trace with:

1. raw input value;
2. normalized `[0,1]` value where applicable;
3. task/pool-specific weight;
4. weighted contribution;
5. exclusions and hard constraints;
6. final score.

The human-readable explanation must be generated from this same trace. There must not be a second, separately maintained prose explanation path.

Conceptually:

```text
score =
    capability_fit
  + observed_reliability
  + context_fit
  + availability
  + quota_health
  - temporal_scarcity_cost
  - monetary_cost
  - expected_latency
  - retry_risk
```

Identical task state, registry state, quota snapshot, policy, and telemetry must produce identical routing output.

---

## Temporal scarcity economics

A subscription quota percentage has no fixed dollar exchange rate.

Ten percent of a pool has different opportunity cost depending on:

- remaining quota;
- time until reset;
- recent burn velocity;
- forecast workload before reset;
- quality of available fallback models;
- whether unused capacity expires.

The scheduler should therefore model a time-dependent scarcity penalty rather than treating subscription quota as ordinary API dollars.

Conceptually:

```text
temporal_scarcity_cost = f(
    remaining_capacity,
    time_to_reset,
    burn_velocity,
    forecast_workload,
    fallback_quality
)
```

Important behavior:

- low remaining quota with a distant reset should become expensive to consume;
- abundant quota close to expiry may become cheap enough to "harvest" rather than waste;
- protected reserve is a hard or near-hard constraint, not merely a cosmetic score.

The exact function should remain simple and inspectable in the MVP.

---

## Cost-to-Green

A task reaches green only after host-owned verification succeeds.

Cost-to-Green aggregates all attempts required to reach that state.

Tracked components should include:

- direct API monetary spend;
- subscription quota consumption in native units;
- temporal scarcity/opportunity cost;
- attempts-to-green;
- time-to-green;
- tokens/requests-to-green;
- regression/verifier failures.

Native quota usage and monetary spend should remain separately observable even if a combined effective-cost score is computed.

---

## Ownership, hysteresis, and versioned handoff

Dynamic routing must not create a new source of thrashing.

Persist:

- current implementation owner;
- architecture/frozen decision owner where relevant;
- reviewer role;
- ownership-transfer history;
- versioned structured task state.

Ownership transfer requires hysteresis, for example:

- minimum attempts before escalation;
- maximum transfers per task;
- cooldown/minimum work window after transfer;
- explicit reason code for every transfer.

The same model repeatedly changing its own hypothesis must also produce new task-state versions; anti-thrashing is not limited to cross-model changes.

Structured handoff should contain at least:

- objective;
- current hypothesis/state;
- frozen architectural decisions/invariants;
- files inspected;
- changes made;
- verifier/test results;
- failed attempts;
- rejected hypotheses;
- relevant diff/evidence references;
- unresolved questions;
- next recommended action.

---

## MVP scope

The MVP should be intentionally narrow:

```text
OpenCode
  + Z.AI/GLM
  + MiniMax
  + headless Python daemon
  + CLI
  + quota collectors
  + deterministic scheduler
  + Safety Kernel integration
```

Do **not** make the MVP depend on:

- ACP;
- LiteLLM;
- macOS/WidgetKit;
- DeskPet;
- Claude/Codex adapters;
- a learned/adaptive router;
- a generalized local-GPU resource scheduler;
- a hand-maintained global model catalog.

---

## MVP falsifiable experiments

### A. Quota survival / utilization

Test whether the orchestrator prevents a scarce premium plan from exhausting prematurely while reducing unused capacity in another paid plan.

Measure at least:

- time-to-exhaustion / reserve breach;
- unused quota at reset;
- utilization by plan/pool;
- number of premium calls avoided or deferred.

### B. Routing quality vs manual choice

Replay or compare similar real coding tasks under manual routing and orchestrator routing.

Measure:

- verified success;
- Pass@1;
- attempts-to-green;
- time-to-green;
- native quota-to-green;
- direct monetary cost;
- regression/verifier failure rate.

The scheduler is not successful if quota savings require a material quality collapse.

### C. Handoff penalty

Compare single-model completion against a structured escalation/handoff path on appropriate tasks.

Measure:

- verified success rate;
- rework after transfer;
- time-to-green;
- context/request overhead;
- regression rate.

The project should prove these three claims before broadening into a general multi-agent platform.

---

## Deferred until evidence justifies them

- adaptive / contextual-bandit scheduling;
- opaque neural routing;
- macOS Menu Bar, dashboard, and WidgetKit implementation;
- ACP editor integration;
- LiteLLM or another universal gateway in the critical path;
- generalized local-compute resource scheduling;
- distributed execution;
- public SaaS control plane;
- automatic paid overage;
- autonomous architecture migration between agents.
