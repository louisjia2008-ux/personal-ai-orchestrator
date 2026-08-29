# Model Resource Orchestration

## Purpose

This document defines the model-resource layer of Personal AI Orchestrator.

The scheduler does **not** route by vendor name alone. It reasons about a logical model SKU and the concrete execution path through which that model will run.

The core problem is:

> Select an eligible execution target for the current task, at the required quality and risk level, while respecting quota truth, absolute headroom, reset timing, monetary cost, latency, reliability, and prior observed performance.

This layer is intentionally separate from worker transport and from the Safety Kernel.

## Design principle

The MVP keeps these identities distinct:

```text
Provider != Account != Plan != QuotaPool != ModelSKU != ExecutionTarget != WorkerRole
```

A provider may expose multiple accounts or plans. A plan may expose one or more quota pools. Multiple models may consume the same quota pool. The same logical model may be reachable through more than one commercial/runtime path.

Example:

```text
GLM-5.3 ModelSKU
  -> ExecutionTarget: Z.AI Coding Plan via OpenCode
       -> Z.AI subscription QuotaPool
  -> ExecutionTarget: Z.AI PAYG API
       -> Z.AI API wallet / cost policy

MiniMax M3 ModelSKU
  -> ExecutionTarget: MiniMax Coding Plan via OpenCode
       -> MiniMax shared Token Plan QuotaPool
```

The scheduler must never present plan/shared-pool truth as independent per-model quota.

`RuntimeVariant` is not a required first-class MVP entity. Variant-like runtime metadata may live on `ExecutionTarget` until real evidence justifies a richer abstraction.

## Domain model

### Provider

Vendor or local runtime family, for example OpenAI, Anthropic, Z.AI, MiniMax, DeepSeek, or a local runtime family.

### Account

Concrete authenticated account/credential boundary. Secrets remain in provider-native stores, environment injection, macOS Keychain, or other approved credential providers. Registry records contain references/identity only, never plaintext credentials.

### Plan

Commercial entitlement attached to an account, for example:

- subscription coding plan;
- prepaid API wallet;
- pay-as-you-go API;
- local/unmetered runtime.

### QuotaPool

The actual shared scarcity boundary.

Important state includes:

- normalized quota state;
- immutable latest observation reference;
- simultaneous quota/reset windows;
- confidence and provenance;
- reserve policy;
- required/binding window kinds;
- overage/payment policy.

Normalized quota states:

```text
AVAILABLE
LIMITED
CRITICAL
EXHAUSTED
UNKNOWN
```

Quota confidence:

```text
EXACT       provider-reported truth with clear semantics
ESTIMATED   derived from incomplete but useful signals
UNKNOWN     no reliable precise value exists
```

Measurement/source method is separate from confidence. A locally measured value is not automatically `EXACT`, and provider-reported data is not automatically exact if the provider field semantics are ambiguous.

### ModelSKU

Logical model identity and capability profile, for example:

- `glm-5.3`;
- `glm-5.2`;
- `minimax-m3`;
- a concrete Codex/GPT model SKU;
- a concrete local model SKU.

Model capability belongs here. Commercial/runtime path does not.

### ExecutionTarget

Concrete path used to execute a ModelSKU.

Minimal identity includes:

- `model_sku_id`;
- `account_id`;
- runtime/adapter identity;
- optional provider/runtime variant metadata;
- enabled/availability state.

`QuotaBinding` and `ConsumptionRule` may be target-specific. This makes subscription-to-PAYG fallback representable without duplicating the logical model identity.

### QuotaBinding

Append-only temporal fact describing which QuotaPool an execution target/model consumes.

It has two time axes:

- provider/business `effective_from` / `effective_until`;
- orchestrator knowledge time `recorded_at`.

Historical replay considers only facts known at the decision's knowledge time.

### ConsumptionRule

Append-only temporal burn fact for a model/target/quota-pool relationship, including an optional multiplier and native provider unit.

Later corrections use explicit `supersedes_rule_id` rather than rewriting old history.

## Immutable quota observations

Every `QuotaSnapshot` has a stable identity and timestamp/provenance. Successful normalized observations may be appended to an immutable quota journal. The mutable last-known-good cache is only a current-state convenience.

A routing decision records the exact quota snapshot IDs it used. This is required for truthful replay:

```text
RoutingDecision
  -> catalog_snapshot_id
  -> policy_snapshot_id
  -> quota_snapshot_ids[]
```

A current registry snapshot is never sufficient evidence for explaining a historical decision.

## Simultaneous quota windows

For a window with reliable remaining quota and time-to-reset:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

For multiple active binding windows, routing is conservative:

```text
known_min_pace = min(all known active-window paces)   # diagnostic only

effective_routing_pace =
  UNKNOWN                         if any binding window is unknown
  min(all binding-window paces)  otherwise
```

Therefore a healthy 5-hour window cannot hide an unknown weekly window.

Pace answers whether capacity is being consumed faster or slower than the reset clock. It does **not** answer whether enough quota remains to finish the next task.

## Capability profile

A model is represented by task-relevant capability dimensions rather than one generic coding score.

Candidate dimensions include:

- architecture;
- repository understanding;
- implementation;
- debugging;
- test repair;
- code review;
- long-horizon execution;
- tool use;
- context handling;
- Swift / SwiftUI;
- Python;
- frontend;
- shell / systems work;
- research;
- vision when relevant.

Capability evidence has two broad sources:

1. **prior** — public/vendor/third-party benchmark evidence;
2. **local posterior** — observed performance inside this orchestrator.

Public benchmarks initialize beliefs; they are not permanent routing truth.

## Local performance telemetry

Long-term routing should optimize verified task outcomes rather than benchmark prestige.

Important metrics include:

- Pass@1;
- attempts-to-green;
- time-to-green;
- tokens-to-green;
- quota-to-green;
- monetary cost-to-green;
- regression rate;
- verifier failure rate;
- cancellation/failure rate;
- context size at success;
- time-to-first-useful-action.

Results should be segmented by task family, language/framework, repository, risk class, model SKU, and execution target when sample size is sufficient.

## Task profile

Before routing, the host creates a structured task profile.

Example:

```yaml
task_type: debugging
language: swift
framework: swiftui
risk: medium
repo_size: large
context_requirement: high
previous_failures: 1
requires_long_horizon: true
required_capabilities:
  debugging: 0.80
predicted_quota_fraction_p90: 0.06
```

Task classification is advisory. Mechanical safety controls remain authoritative.

## Pools

Pools are candidate sets, not hard-coded provider-role assignments.

Initial pools:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

A ModelSKU or explicit ExecutionTarget may appear in multiple pools with different priority/weight.

Pool membership only creates a candidate set. It does not bypass eligibility or quota admission.

## Routing objectives

The initial scheduler is deterministic and explainable.

### Balanced

Optimize expected verified usefulness after hard constraints and quota admission.

### Max Quality

Place more ranking weight on expected success/reliability while still respecting all hard constraints.

### Save Quota

Prefer cheaper/unmetered or healthier capacity after minimum quality/risk floors are met.

### Low Latency

Prefer low observed time-to-green when risk permits.

### Provider Protection

Protection/reserve is a hard constraint, not a negative score that a strong model can overcome.

## Reserve policy

Quota reserve is a first-class admission constraint.

Example:

```text
GLM plan reserve = 15%
```

Routine traffic may not consume protected reserve. Only policy-authorized escalation classes may do so.

## Escalation policy

The baseline loop is escalation-based rather than fixed model alternation.

Example:

```text
low risk:
  worker attempts <= 3 before escalation

medium risk:
  worker attempts <= 2 before escalation

high risk:
  reasoning/escalation pool may be consulted up front
```

`GLM -> MiniMax -> GLM -> MiniMax` is not an invariant. Repeated switching carries ownership/handoff cost and must be justified.

## Constraint-first scheduling pipeline

The production scheduler must **not** run one weighted score over every model. The pipeline is:

```text
TaskProfile
  + immutable registry/catalog facts
  + fresh quota snapshots
  + runtime health
  + routing policy
        |
        v
1. HARD ELIGIBILITY
        |
        v
2. QUOTA / TASK ADMISSION
        |
        v
3. DETERMINISTIC RANKING
        |
        v
RoutingDecision + exclusion reasons + snapshot references
```

### Stage 1 — hard eligibility

Examples of hard exclusion reasons:

- model/target disabled;
- runtime unavailable;
- context/tool/capability floor not met;
- risk-policy floor not met;
- missing or ambiguous temporal binding;
- stale quota snapshot;
- exhausted quota;
- unknown required quota window;
- protected reserve policy;
- paid usage not explicitly allowed.

A high capability score cannot compensate for a failed hard constraint.

### Stage 2 — quota/task admission

For metered subscription capacity:

```text
minimum_remaining_fraction = min(binding-window remaining fractions)

usable_headroom = minimum_remaining_fraction
                - reserve_fraction
                - uncertainty_margin

predicted_burn = task_burn_p90
               * applicable_consumption_multiplier

admit only if predicted_burn <= usable_headroom
```

If a required binding window has unknown remaining quota, admission is unknown and fails conservatively for normal automated traffic.

This prevents the classic false-HARVEST case:

```text
1% quota left
0.1% of reset time left
pace = 10  -> temporal HARVEST signal

but a task predicted to burn 5% must still be rejected
```

### Stage 3 — deterministic ranking

Only admitted candidates are scored/ranked.

Ranking may consider:

- capability fit;
- local success prior;
- pool priority/weight;
- temporal surplus/conservation signal;
- expected time-to-green;
- expected cost-to-green;
- latency;
- retry/handoff risk.

Weights may vary by routing objective/task class, but ranking never overrides the preceding hard gates.

Every selection emits machine-readable reasons for both the selected target and rejected candidates.

Example:

```text
SELECT minimax-sub/M3
- hard capability and runtime gates passed
- predicted p90 burn fits usable subscription headroom
- MiniMax quota observation is fresh and binding windows are known
- GLM subscription target excluded because required quota truth is UNKNOWN
- no ownership transfer required
```

## Task ownership and anti-thrashing

Dynamic routing must not cause agents to rewrite one another's architecture repeatedly.

The orchestrator separates:

- **task owner** — worker responsible for the current implementation attempt;
- **architecture owner** — source of frozen design decisions where required;
- **reviewer** — advisory/verification role with no implicit write ownership.

Important architectural decisions and invariants are host-owned state. Model switching does not erase them.

## Cross-model handoff

When ownership changes, the next worker receives structured state rather than an uncontrolled transcript dump.

Suggested fields:

- objective;
- current state;
- relevant files;
- changes already made;
- verifier/test results;
- failed attempts;
- rejected hypotheses;
- frozen invariants;
- unresolved questions;
- diff/evidence references.

## Gateway relationship

The orchestrator reuses mature provider/gateway infrastructure where practical, but scheduling policy stays above that layer.

```text
OpenCode / DeskPet / CLI / other clients
                  |
                  v
        Personal AI Orchestrator
        - safety kernel
        - task state
        - model registry
        - quota governor
        - admission + routing scheduler
        - telemetry
                  |
                  v
        provider/gateway adapters
                  |
        model APIs / coding plans
```

## Safety invariants

Resource optimization never weakens the Safety Kernel.

1. model selection cannot bypass worktree isolation;
2. model selection cannot redefine verifier commands;
3. quota exhaustion cannot silently mark work complete;
4. routing uncertainty fails conservatively;
5. automatic paid overage requires explicit policy/approval;
6. model-reported completion is never host verification;
7. dynamic fallback preserves task ownership and audit history;
8. a temporal HARVEST signal cannot override absolute quota headroom;
9. an unknown binding quota window cannot be ignored merely because another window is healthy;
10. overlapping unsuperseded temporal facts are ambiguous and fail closed.

## Activation sequence

1. Model Registry + temporal bindings/rules.
2. Provider quota observability + immutable snapshots.
3. OpenCode thin adapter + safe BYPASS/SHADOW contract.
4. Constraint-first recommendation scheduler.
5. Shadow Mode over real tasks and multiple relevant reset cycles.
6. Compare manual choice vs recommendation using verified outcomes and quota before/after.
7. Only then consider ACTIVE routing, and only when P0/P1 Safety Kernel / verification authority is present.
8. Cost-to-Green analytics.
9. Adaptive routing only after sufficient real task data exists.

## Explicit non-goals for the first production version

- reinforcement-learning router;
- opaque model-selection neural network;
- distributed cluster scheduler;
- autonomous paid overage;
- automatic architecture migration between agents;
- provider scraping presented as exact quota truth;
- ranking models by one public benchmark score;
- treating `pace` as a complete admission decision;
- enabling ACTIVE routing before deterministic safety/verification gates exist.

See also [`SCHEDULER_CORRECTNESS.md`](SCHEDULER_CORRECTNESS.md).
