# P3.9.2 Observed Exhaustion Recovery Governor - 2026-08-31

Status: `P3_9_2_OBSERVED_EXHAUSTION_RECOVERY_GOVERNOR_PARTIAL_ACCEPTED`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## Scope

P3.9.2 turns real Codex usage-limit behavior from P3.9.1 into host-owned
admission/backoff state. It does not add Claude as a provider, does not infer exact remaining
quota, and does not fabricate provider-exact reset metadata.

## Readiness Terminology

P3.9.2 separates these counters:

```text
REAL_ATTEMPT_COUNT
QUALITY_ELIGIBLE_ATTEMPT_COUNT
VERIFIED_OUTCOME_COUNT
POLICY_BLOCK_COUNT
MODEL_TASK_FAILURE_COUNT
OPERATIONAL_FAILURE_COUNT
```

Shadow review readiness now uses `quality_eligible_observations`, not total operational
observations. A usage-limit rejection before meaningful model execution does not satisfy the
quality-evidence threshold.

The P3.9.1 evidence is therefore:

```text
REAL_ATTEMPT_COUNT: 20
QUALITY_ELIGIBLE_ATTEMPT_COUNT: 8
VERIFIED_OUTCOME_COUNT: 8
POLICY_BLOCK_USAGE_LIMIT: 12
MODEL_TASK_FAILURE_COUNT: 0
REAL_PROVIDER_EXACT_RESET_CYCLES: 0
QUALITY_READINESS: not accepted; need 20 quality-eligible observations, have 8
```

## Observed Availability Model

P3.9.2 adds a host-owned observed availability state that is distinct from exact quota amount:

```text
UNKNOWN
AVAILABLE_OBSERVED
EXHAUSTED_OBSERVED
COOLDOWN
RECOVERY_PROBE_DUE
RECOVERED_OBSERVED
```

Availability evidence records only local event truth:

```text
measurement_source: LOCALLY_MEASURED
confidence: ESTIMATED or UNKNOWN
reset_cycle_source: LOCALLY_INFERRED or UNKNOWN
remaining_fraction: null
reset_at: null
```

This evidence may remove an execution target from automated admission while exhausted. It cannot
prove future capacity and cannot satisfy production reset-cycle acceptance.

## Usage-Limit Classification

Known usage-limit worker output is classified as:

```text
failure_class: POLICY_BLOCK
quality_outcome: POLICY_BLOCKED
availability_state: COOLDOWN
sanitized_reason_code: USAGE_LIMIT
```

It is not classified as `MODEL_TASK_FAILURE`, `VERIFIER_FAILURE` or `UNKNOWN_FAILURE`. Raw provider
output is not required for persisted availability state; only sanitized reason codes are retained.

## Circuit Breaker

The real campaign runner now owns a resumable queue:

```text
PENDING
RUNNING
VERIFIED
FAILED
DEFERRED_QUOTA
CANCELLED
```

When the first real usage-limit block is observed for an execution target:

- that one attempted case records a real operational Shadow observation;
- the target availability enters cooldown;
- remaining unfinished selected cases become `DEFERRED_QUOTA`;
- deferred cases do not create fake `ShadowObservation` records;
- deferred cases do not count as quality attempts or model failures;
- deferred cases remain resumable after observed recovery.

## Recovery Semantics

Because provider-exact `reset_at` remains unknown, P3.9.2 does not schedule exact resets. Recovery
requires real provider behavior, either from a bounded recovery probe or a legitimate later task.

P3.9.2 seeded sanitized local availability from the P3.9.1 usage-limit report without rewriting
historical observations:

```text
P3.9.1_LAST_USAGE_LIMIT_AT_UTC: 2026-08-30T14:08:24.526784Z
P3.9.1_LAST_USAGE_LIMIT_AT_ASIA_SHANGHAI: 2026-08-30 22:08:24
SANITIZED_REASON_CODE: USAGE_LIMIT
DERIVED_AVAILABILITY_STATE_BEFORE_PROBE: RECOVERY_PROBE_DUE
```

Then a bounded Codex-only recovery probe was run with at most 2 real cases:

```text
P3.9.2_CAMPAIGN_ROOT: .personal-ai-orchestrator/p392-codex-governor
P3.9.2_REPORT: .personal-ai-orchestrator/p392-codex-governor/p39-real-shadow-quality-campaign-report.json
REPORT_GENERATED_AT_UTC: 2026-08-31T01:01:27.455200+00:00
RECOVERY_OBSERVED_AT_UTC: 2026-08-31T01:00:28.841398Z
RECOVERY_OBSERVED_AT_ASIA_SHANGHAI: 2026-08-31 09:00:28
EXHAUSTION_TO_RECOVERY_SECONDS: 39124.314614
REAL_ATTEMPT_COUNT: 2
QUALITY_ELIGIBLE_ATTEMPT_COUNT: 2
VERIFIED_OUTCOME_COUNT: 2
POLICY_BLOCK_COUNT: 0
MODEL_TASK_FAILURE_COUNT: 0
REAL_PROVIDER_EXACT_RESET_CYCLES: 0
AVAILABILITY_STATE: RECOVERED_OBSERVED
MEASUREMENT_SOURCE: LOCALLY_MEASURED
CONFIDENCE: ESTIMATED
RESET_CYCLE_SOURCE: LOCALLY_INFERRED
RESET_AT: null
REMAINING_FRACTION: null
```

This proves only:

```text
provider was exhausted at or before 2026-08-30T14:08:24.526784Z
provider was usable again at 2026-08-31T01:00:28.841398Z
```

It does not prove the provider's official reset time.

## Scheduler Asymmetry

P3.9.2 implements the intended asymmetric rule:

```text
weak negative evidence may remove a candidate
weak positive evidence cannot authorize production ACTIVE or exact quota admission
```

`COOLDOWN` and `EXHAUSTED_OBSERVED` block quota-billable launches for the target. In contrast,
`AVAILABLE_OBSERVED` and `RECOVERED_OBSERVED` do not bypass the existing subscription quota gates
when exact remaining quota is still unknown.

## Tests

P3.9.2 adds regression coverage for:

- usage limit -> `POLICY_BLOCK`;
- usage limit -> observed cooldown/exhaustion availability;
- usage limit is not model failure or verifier failure;
- usage limit does not count as quality-eligible evidence;
- circuit breaker defers remaining cases;
- deferred cases do not create fake Shadow observations;
- campaign can resume deferred cases after observed recovery;
- recovery success creates `RECOVERED_OBSERVED`;
- recovery does not create provider-exact reset evidence;
- locally measured timings remain non-exact;
- weak negative evidence removes candidates;
- weak positive evidence does not authorize exact quota admission;
- owner approval remains absent.

## Local Verification

```text
Ruff full repository check: PASS
targeted pytest: 43 passed, 1 warning
pytest full repository: 180 passed, 28 warnings
git diff --check: PASS
OpenCode adapter typecheck + contract tests: PASS, 10/10 in /private/tmp dependency copy
P3.9.2 real Codex recovery probe: 2 attempted, 2 verified, 0 policy blocks
P3.9.2 observed availability: RECOVERED_OBSERVED
P3.9.2 provider-exact reset cycles: 0
P3.9.2 Claude-as-provider support: NOT ADDED
```

## What Was Not Changed

P3.9.2 did not:

- add Claude as a model provider;
- enable production ACTIVE;
- create owner approval;
- fabricate exact remaining quota;
- fabricate `reset_at`;
- treat local inference as `PROVIDER_EXACT`;
- create fake reset cycles;
- rewrite historical P3.9.1 observations;
- read credential files;
- reverse engineer private quota endpoints;
- merge or mark PR #21 ready.
