# Model Resource Orchestration

## Purpose

This document defines the model-resource layer of Personal AI Orchestrator.

The scheduler does **not** route by vendor name alone. The atomic schedulable resource is a concrete model SKU attached to an account/plan/quota pool and a runtime profile.

The core problem is:

> Select the right model SKU for the current task, at the required quality and risk level, while respecting quota scarcity, monetary cost, latency, context requirements, reliability, and prior observed performance.

This layer is intentionally separate from worker transport and from the Safety Kernel.

## Design principle

`Provider != Plan != QuotaPool != ModelSKU != RuntimeVariant != WorkerRole`

A provider may expose multiple plans. A plan may expose one or more quota pools. Multiple models may consume the same quota pool. The same model may exist in multiple runtime variants with different price/latency characteristics.

Example:

```text
MiniMax account
  -> Coding Plan
      -> shared quota pool
          -> M3
          -> M2.7
          -> M2.7 high-speed

Z.AI account
  -> Coding Plan
      -> shared/plan-specific quota pool
          -> GLM-5.3
          -> GLM-5.2
```

The scheduler must never present model-level remaining quota when the observable truth is only plan-level/shared-pool quota.

## Domain model

### Provider

Represents a vendor or local runtime family.

Examples: OpenAI, Anthropic, Z.AI, MiniMax, DeepSeek, Ollama.

### Account

A concrete authenticated account/credential boundary.

Secrets remain in provider-native stores, environment injection, macOS Keychain, or other approved credential providers. They are not stored in model-registry records.

### Plan

Represents the commercial entitlement attached to an account.

Examples:

- subscription coding plan;
- prepaid API wallet;
- pay-as-you-go API;
- local/unmetered runtime.

### QuotaPool

Represents the actual shared scarcity boundary.

Required fields should eventually include:

- quota state;
- measured/estimated usage;
- reset time when known;
- confidence;
- source of truth;
- reserve policy;
- overage policy.

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
EXACT       provider-reported account/plan truth
ESTIMATED   derived from local telemetry or incomplete provider signals
UNKNOWN     no reliable estimate exists
```

Unknown quota must not be rendered as a fake precise percentage.

### ModelSKU

A concrete schedulable model identity.

Examples:

- `glm-5.3`
- `glm-5.2`
- `minimax-m3`
- `minimax-m2.7`
- a specific GPT/Codex model SKU
- a specific DeepSeek model SKU

A model SKU belongs to a provider/account path and references the quota pool it consumes.

### RuntimeVariant

Optional runtime-specific behavior for the same logical model.

Examples:

- standard vs high-speed;
- API vs subscription-backed worker;
- local quantization variant;
- different context or reasoning settings.

Runtime variants may differ in price, latency, context limits, throughput, and reliability.

## Capability profile

A model must be represented by a task-relevant capability vector rather than a single generic coding score.

Candidate dimensions:

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

Scores have two sources:

1. **prior**: public/vendor/third-party benchmark evidence;
2. **local posterior**: observed performance inside this orchestrator.

Public benchmark scores are initialization evidence, not permanent routing truth.

## Local performance telemetry

The long-term scheduler should optimize for observed task completion rather than benchmark prestige.

Important metrics:

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
- latency and time-to-first-useful-action.

Results should be segmented by task family, language/framework, repository, risk class, and runtime variant when sample size is sufficient.

## Task profile

Before routing, the host creates a structured task profile.

Candidate fields:

```yaml
task_type: debugging
language: swift
framework: swiftui
risk: medium
repo_size: large
context_requirement: high
previous_failures: 1
testability: medium
requires_vision: false
requires_long_horizon: true
```

Task classification is advisory. Mechanical safety controls remain authoritative.

## Pools

Pools are candidate sets, not hard-coded vendor-role assignments.

Initial pools:

- Worker
- Reasoning
- Review
- Escalation
- Fallback

A single model SKU may appear in multiple pools with different priority/weight.

Example:

```text
Worker
  1. MiniMax M3
  2. MiniMax M2.7
  3. GLM-5.2

Reasoning
  1. GLM-5.3
  2. GPT high-reasoning SKU
  3. MiniMax M3

Escalation
  1. strongest available high-reasoning SKU
  2. GLM-5.3
```

The actual model choice still considers live quota, task profile, historical success, runtime availability, cost, and latency.

## Routing modes

The initial implementation should be deterministic and explainable.

Suggested modes:

### Balanced

Optimize quality subject to quota and cost constraints.

### Max Quality

Strongly prioritize expected task success and low regression risk.

### Save Quota

Protect scarce subscription pools and prefer cheaper/unmetered alternatives.

### Low Latency

Prefer models with low observed time-to-green when task risk permits.

### Provider Protection

Allow a user to protect a specific quota pool or model family.

## Reserve policy

Quota reserve is a first-class scheduler constraint.

Example:

```text
GLM plan reserve = 15%
```

Below the reserve threshold, normal worker/review traffic should be denied from that pool. Only explicitly allowed high-risk escalation classes may consume the protected reserve.

This prevents routine review traffic from exhausting a scarce high-reasoning plan before a true blocker appears.

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

`GLM -> MiniMax -> GLM -> MiniMax` is not an architectural invariant.

The scheduler should favor inexpensive capable workers until task risk, ambiguity, or repeated failure justifies stronger/scarcer resources.

## Candidate scoring

The first production scheduler should use an auditable rule/weighted-score engine, not machine learning.

Conceptually:

```text
utility(model, task) =
    capability_fit
  + local_success_prior
  + context_fit
  + availability
  + quota_health
  - scarcity_penalty
  - expected_cost
  - expected_latency
  - retry_risk
```

Weights are task-class specific.

Architecture tasks may weight reasoning quality heavily. Routine regression-test generation may weight cost, speed, and quota health more heavily.

Every selection must emit an explanation record describing the decisive factors.

Example:

```text
SELECT M3
- strong local Swift debugging history
- medium-risk task
- MiniMax quota healthy
- GLM reserve protected
- lower expected cost-to-green than escalation models
```

## Task ownership and anti-thrashing

Dynamic routing must not cause agents to rewrite one another's architecture repeatedly.

The orchestrator therefore separates:

- **task owner**: worker responsible for the current implementation attempt;
- **architecture owner**: source of frozen design decisions where required;
- **reviewer**: advisory/verification role with no implicit write ownership.

Important architectural decisions and invariants must be persisted as host-owned task/project state.

Model switching does not erase those decisions.

## Cross-model handoff

When ownership changes, the next model receives a structured handoff rather than an uncontrolled transcript dump.

Suggested handoff fields:

- objective;
- current state;
- relevant files;
- changes already made;
- verifier/test results;
- failed attempts;
- rejected hypotheses;
- frozen invariants;
- unresolved questions;
- relevant diff/evidence references.

This reduces context duplication and supports quota-aware switching.

## Gateway relationship

The orchestrator should reuse mature gateway/provider infrastructure where practical instead of reimplementing provider compatibility, retries, or transport normalization from scratch.

The resource scheduler remains above that gateway layer.

```text
OpenCode / DeskPet / CLI / other clients
                  |
                  v
        Personal AI Orchestrator
        - safety kernel
        - task state
        - model registry
        - quota governor
        - routing scheduler
        - telemetry
                  |
                  v
        provider/gateway adapters
                  |
        model APIs / coding plans
```

## Safety invariants

Resource optimization must never weaken the existing Safety Kernel.

In particular:

1. model selection cannot bypass worktree isolation;
2. model selection cannot redefine verifier commands;
3. quota exhaustion cannot silently mark work complete;
4. routing uncertainty fails conservatively;
5. automatic paid overage requires explicit policy/approval;
6. model-reported completion is never host verification;
7. dynamic fallback preserves task ownership and audit history.

## Implementation sequence

1. Model Registry and quota-pool schema.
2. Provider/runtime telemetry normalization.
3. Static pool configuration and manual routing.
4. Explainable rule-based automatic routing.
5. OpenCode adapter integration.
6. macOS control plane and widgets.
7. Cost-to-Green analytics.
8. Adaptive routing only after sufficient real task data exists.

## Explicit non-goals for the first production version

- reinforcement learning router;
- opaque model-selection neural network;
- distributed cluster scheduler;
- autonomous paid overage;
- automatic architecture migration between agents;
- provider scraping presented as exact quota truth;
- ranking models by one public benchmark score.
