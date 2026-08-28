# Model Resource Orchestration

## Purpose

Personal AI Orchestrator is not another generic LLM gateway or black-box "best model" router.

Its product thesis is narrower:

> **Schedule expiring, non-fungible AI subscription capacity across coding-agent workloads without wasting paid quota, exhausting premium capacity too early, or weakening verified task quality.**

The three core product capabilities are:

1. **Quota Governor** — understand shared subscription scarcity, resets, reserves, confidence, and provider reconciliation.
2. **Temporal Scarcity Scheduler** — ration capacity according to how far ahead/behind consumption is relative to reset windows.
3. **Stateful Cross-model Handoff** — switch owners without losing verified state, invariants, failed approaches, or auditability.

**Explainability is not a fourth product pillar. It is a system invariant.** Every scheduling, reserve, escalation, and ownership decision must be reconstructable from the exact machine-readable inputs and scoring trace that produced it.

This resource layer remains subordinate to the host-owned Safety Kernel.

---

## MVP boundary

The first falsifiable deployment target is intentionally narrow:

```text
OpenCode
  + Z.AI / GLM
  + MiniMax
  + Python headless daemon
  + CLI
  + quota collectors
  + deterministic scheduler
  + Safety Kernel read-only task view
  + structured handoff
```

Not required for MVP:

- ACP;
- LiteLLM / universal gateway execution;
- Claude Code or Codex execution adapters;
- macOS / WidgetKit / DeskPet;
- adaptive or learned routing;
- generalized local GPU/thermal scheduling;
- a hand-maintained global model catalog.

The orchestrator returns routing decisions. OpenCode remains responsible for executing model completions in the MVP.

---

## Reuse model metadata

Static public model facts should be imported from an upstream catalog where practical, initially `models.dev` because it aligns with OpenCode's model ecosystem.

Use **local versioned snapshots**, not a live network dependency on every route:

```text
models.dev
   -> background/periodic refresh
   -> validate snapshot
   -> persist last-known-good snapshot
   -> scheduler reads local snapshot only
```

Each snapshot should carry:

- `catalog_snapshot_id`;
- `as_of` / fetched time;
- upstream source/version/hash where available;
- validation result.

A routing decision records the exact `catalog_snapshot_id` it used. Stale snapshots remain usable but should be identified as stale in decision evidence.

The orchestrator owns user-specific facts upstream catalogs cannot know:

- accounts and entitlements;
- quota pools and reset windows;
- quota observations and confidence;
- quota bindings and consumption rules;
- reserves/protection;
- local task outcomes;
- task ownership/handoff;
- routing policy.

---

## Commercial scarcity domain

Keep the identity boundary explicit while keeping Account/Plan implementation lightweight in the MVP:

```text
Provider
  -> Account
      -> Plan
          -> QuotaPool

ModelSKU <- upstream model metadata
   |
   +-> QuotaBinding -> QuotaPool
   +-> ConsumptionRule
```

`Account` and `Plan` remain representable so multi-account/multi-entitlement support does not require a breaking schema migration later. The MVP does not need rich Account/Plan lifecycle UI or complex CRUD machinery.

### Provider

Vendor/execution family such as Z.AI or MiniMax.

### Account

Authentication identity boundary. Credentials are references only; plaintext secrets do not belong in registry/task records.

### Plan

Commercial entitlement such as a coding subscription, prepaid wallet, or pay-as-you-go account.

### QuotaPool

The actual scarcity boundary shared by one or more models/resources.

A pool may expose multiple simultaneous quota windows, for example a rolling five-hour window plus a weekly limit.

Quota state is normalized separately from observation confidence:

```text
AVAILABLE / LIMITED / CRITICAL / EXHAUSTED / UNKNOWN

EXACT       directly provider-reported for the relevant scope
ESTIMATED   inferred from incomplete provider signals/local telemetry
UNKNOWN     no defensible estimate
```

Unknown/shared observations must never be rendered as fake precise per-model quota.

Reserve/protection policy belongs to the QuotaPool/Safety policy boundary, not to a particular model name.

### ModelSKU

Concrete schedulable model identity. Generic static metadata should reference a catalog snapshot rather than being manually duplicated wherever possible.

### Runtime/model variants

`RuntimeVariant` is not a required first-class MVP entity. Reasoning/high-speed variants should initially reuse OpenCode/upstream identifiers unless a real scarcity/routing requirement proves a dedicated local entity is necessary.

---

## Evidence provenance

Evidence source must be structured rather than a free-form string.

Suggested source types:

```text
PROVIDER_API
PROVIDER_DOCUMENTATION
PROVIDER_ANNOUNCEMENT
LOCAL_OBSERVATION
INFERRED
MANUAL_OVERRIDE
COMMUNITY_REPORT
```

Evidence records should include source type, reference/URI when safe, observation/record time, and confidence.

Provider API/doc evidence and inferred/community evidence are not equivalent even when they currently imply the same value.

---

## QuotaBinding and ConsumptionRule are append-only temporal facts

Provider policy can change independently along two axes:

1. which QuotaPool a model consumes;
2. how quickly activity consumes that pool.

Therefore keep these as separate facts.

### QuotaBinding

Represents which pool a model consumes for an effective period.

Suggested fields:

```text
binding_id
model_id
quota_pool_id
effective_from
effective_until?       # only when known at record creation
recorded_at
supersedes_binding_id?
confidence
source
```

### ConsumptionRule

Represents the burn semantics for a model/pool relationship.

Suggested fields:

```text
rule_id
model_id
quota_pool_id
multiplier / native burn semantics
effective_from
effective_until?       # only when known at record creation
recorded_at
supersedes_rule_id?
confidence
source
```

Provider-specific peak/off-peak, promotional, point/request/token, cache, or modality semantics can later extend the rule without hard-coding them into scheduler logic.

### Append-only rule

Persisted `QuotaBinding` and `ConsumptionRule` facts are immutable. Do not mutate an old fact merely because new information arrives.

If a provider changes policy, append a new fact with a later effective time and/or a `supersedes_*` reference. If an end time was already known when the original fact was created, it may be recorded at creation; otherwise do not retroactively edit the old row just to close it.

### Two times matter

The scheduler must distinguish:

- **valid/effective time** — when the provider rule applies;
- **recorded/knowledge time** — when this orchestrator learned the fact.

This prevents a later backdated provider correction from silently changing the answer to:

> Why did the scheduler choose model X two weeks ago?

Replay uses only facts that were known at the historical decision time.

---

## Quota windows and MVP temporal scarcity primitive

Do not start with a six-variable forecasting model.

For each active quota window with defensible remaining capacity, compute:

```text
pace = remaining_quota_fraction / remaining_time_fraction
```

where `remaining_time_fraction` is the fraction of that reset window still remaining at the observation time.

Interpretation:

- `pace << 1` — quota is being consumed too quickly; conserve;
- `pace ~= 1` — consumption is approximately on pace;
- `pace >> 1` — quota is underused relative to time remaining; harvesting may be appropriate.

Initial policy bands may be simple and configurable, for example:

```text
pace < 0.5      CRITICAL_SCARCITY
0.5 - 0.8       CONSERVE
0.8 - 1.2       ON_PACE
1.2 - 1.5       SURPLUS
> 1.5           HARVEST_CANDIDATE
```

These thresholds are hypotheses, not truths; dogfooding data should calibrate them.

### Multiple simultaneous windows

A provider may constrain usage by more than one window. Compute pace per window:

```text
pace_5h
pace_week
pace_month
```

The MVP effective scarcity signal should be conservative and explainable:

```text
effective_pace = min(valid_window_paces)
```

A short window being abundant must not hide a nearly exhausted weekly constraint.

Future burn velocity/workload forecasting may be added only after real failures of the simple pace model are observed. Fallback model quality remains a separate capability/utility signal rather than being hidden inside the scarcity metric.

---

## Decision replay versus actual invocation accounting

These are different concerns and must not be conflated.

### RoutingDecision snapshot

Every model-selection decision records the inputs the scheduler knew at that moment, including at least:

```text
task_state_version
routing_policy_snapshot_id
quota_snapshot_id / observation ids
consumption_rule_id(s)
quota_binding_id
catalog_snapshot_id
selected model
candidate scoring trace
```

This makes the **decision** replayable.

### Invocation accounting

Actual model calls are accounted using the rule that applies to each invocation when it occurs/provider-reported consumption when available.

A long task may therefore legitimately contain:

```text
call 1 -> ConsumptionRule A
call 2 -> ConsumptionRule A
call 3 -> ConsumptionRule B
```

Task Cost-to-Green aggregates actual invocation consumption. Do not lock all accounting to the rule that happened to be active at task start.

This distinction preserves both historical explanation and truthful cost accounting when provider policy/time bands change during a task.

---

## Quota reconciliation and ToS gate

Local request accounting is insufficient when a shared plan can be consumed by other clients.

Collectors should record where available:

- scope (account/plan/pool/window);
- remaining capacity;
- reset/window data;
- provider usage/rate-limit signals;
- observation time;
- structured evidence source;
- confidence.

If provider visibility degrades, confidence may move `EXACT -> ESTIMATED -> UNKNOWN`.

Before implementing automated quota collectors for a provider, complete a terms/account-policy preflight:

```text
official usage API?
automation permitted?
authentication path permitted?
rate limits?
third-party client restrictions?
scraping required?
account-ban risk?
```

Prefer official APIs. Undocumented internal endpoints require explicit review. Web scraping is disabled by default and must never be represented as exact provider-supported telemetry.

---

## Safety Kernel -> Scheduler read-only contract

The Safety Kernel remains authoritative.

A minimal scheduler view should expose only derived task-health signals, for example:

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

Do not expose credentials, raw permission internals, full audit logs, or full worktree contents merely to choose a model.

The Scheduler may recommend model selection, escalation, reserve use, fallback, or ownership transfer. Safety policy may deny the recommendation.

Quota state never changes repository permissions by itself.

---

## OpenCode integration: thin in-process adapter, out-of-process policy

OpenCode is the first execution client.

Preferred shape:

```text
OpenCode session/agent
        |
        v
thin OpenCode plugin/adapter
        |
        v
local Orchestrator daemon
  - Quota Governor
  - Scheduler
  - Safety task view
        |
        v
RoutingDecision
        |
        v
thin adapter applies session/run/agent-scoped model selection
```

The plugin is a translator, not the policy engine. Quota logic, scoring, ownership policy, and durable state remain out-of-process in the daemon.

Requirements:

- avoid repeated shared-global-config mutation as the normal path;
- concurrent sessions must not overwrite one another;
- provider-native authentication remains OpenCode/provider owned where possible;
- daemon/plugin failure has an explicit safe bypass/fallback path;
- routing decisions/provider errors are audited;
- host deterministic verification remains authoritative.

An experimental OpenCode compaction/context hook may be used to inject structured task state as an optimization, but cross-model handoff must not depend on an experimental API existing.

ACP remains an optional future interoperability adapter. LiteLLM/generic gateways remain optional future execution adapters for non-OpenCode clients.

---

## Candidate pools and deterministic scheduling

Pools are candidate sets, not vendor-role bindings:

- Worker;
- Reasoning;
- Review;
- Escalation;
- Fallback.

The MVP scheduler is a deterministic rule/weighted-score engine.

Every candidate produces one machine-readable scoring trace:

1. raw inputs;
2. normalized values where applicable;
3. weights;
4. hard exclusions;
5. weighted contributions;
6. final score.

Human-readable explanation is generated from that same trace, never from a separately maintained prose path.

Conceptually:

```text
score =
    capability_fit
  + observed_reliability
  + context_fit
  + availability
  + quota_pace_utility
  - direct_cost
  - expected_latency
  - retry_risk
```

Reserve policy may impose hard eligibility constraints rather than merely adjusting score.

Identical authoritative task view, catalog snapshot, quota observations, temporal facts, policy snapshot, and telemetry must produce identical routing output.

---

## Ownership hysteresis and cross-model handoff

Resource-driven transfer and failure-driven transfer are different signals and must not consume the same transfer budget.

Track at least:

```text
failure_transfer_count
resource_transfer_count
manual_transfer_count
```

Minimum-attempt gates are the main hysteresis mechanism. A short time cooldown may additionally prevent second-scale transfer loops, but should not be the primary scale-independent rule.

When transfer limits are exhausted and the task is still unresolved, enter an explicit terminal/escalated state such as `HUMAN_REVIEW_REQUIRED`; do not silently continue thrashing.

### Minimal TaskHandoff

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

Suggested handoff reasons:

```text
REPEATED_FAILURE
QUOTA_EXHAUSTION
RISK_ESCALATION
EXPLICIT_REVIEW
PROVIDER_UNAVAILABLE
MANUAL_REROUTE
```

Reason matters: quota-driven transfer usually means the prior work may be sound and should continue; failure-driven transfer should make the new owner more skeptical of the previous hypothesis/diff.

Large diffs belong in referenced artifacts/evidence, not inline in the handoff payload.

Structured handoff is daemon-owned. OpenCode compaction/context injection is only an optimization path for delivering it.

---

## MVP rollout and falsifiable experiments

Before active auto-routing, prove safe bypass behavior:

```text
daemon unavailable -> OpenCode can continue in configured/manual mode
scheduler disabled -> OpenCode works normally
quota collector unavailable -> conservative/manual fallback, no fake exact quota
```

### Phase 1: Shadow mode

The scheduler recommends but does not switch models.

Record:

```text
human/manual choice
scheduler recommendation
input snapshots
final verified outcome
time / attempts / quota / regression
```

Use matched/similar task families and record risk/previous-failure covariates so manual-vs-scheduler comparisons are not treated as perfectly randomized.

### Phase 2: Active mode

Only after shadow/bypass checks pass, allow automatic model assignment/switching.

### Experiment A — Quota survival/utilization

Measure time-to-exhaustion/reserve breach, unused quota at reset, utilization across paid pools, and premium calls avoided/deferred.

### Experiment B — Routing quality

Measure verified success, Pass@1, attempts-to-green, time-to-green, native quota-to-green, direct monetary cost, and regression/verifier failures versus manual routing on comparable workloads.

### Experiment C — Handoff penalty

Measure verified success, rework after transfer, time/context/request overhead, and regression versus appropriate single-model baselines.

Each production conclusion should span at least two complete relevant reset cycles when practical; short shadow/replay data may be used to debug the mechanism but not to claim the thesis proven.

---

## Cost-to-Green

A task is green only after host-owned verification succeeds.

Keep these separately observable:

- direct API money;
- provider/native quota units actually consumed;
- attempts-to-green;
- time-to-green;
- requests/tokens-to-green;
- verifier/regression failures;
- handoff rework.

Temporal pace affects scheduling opportunity cost. Actual task accounting remains grounded in per-invocation/provider-reconciled consumption.

---

## Deferred until evidence justifies them

- adaptive/contextual-bandit scheduling;
- opaque neural routing;
- macOS Menu Bar/dashboard/WidgetKit implementation;
- ACP editor integration;
- LiteLLM/universal gateway in the critical path;
- Claude/Codex execution adapters;
- generalized local-compute resource scheduling;
- distributed execution;
- public SaaS control plane;
- automatic paid overage;
- autonomous architecture migration between agents.
