# P3.9 Real Shadow Quality Campaign - 2026-08-30

Status: `P3_9_CODEX_ONLY_REAL_SHADOW_QUALITY_CAMPAIGN_COLLECTING_PARTIAL_ACCEPTED`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## Baseline

```text
BRANCH: integration/end-to-end-shadow-safety
BASELINE_HEAD: 2997d8f390b42e01836d4ad88e5020cc878e064c
PR: #21
PR_STATE: OPEN / DRAFT
CI_AT_BASELINE: test SUCCESS, opencode-adapter SUCCESS
```

## Runner

P3.9 adds a reusable declarative Shadow campaign runner rather than another one-off
first-observation script. Each case declares:

```text
case_id
task_family
difficulty_class
required_capabilities
fixture files
expected changed-file scope
trusted verifier profile
worker prompt/profile
actual execution target
```

For each observation the runner:

- creates a fresh disposable Git repository;
- commits a deterministic baseline;
- submits durable Safety Kernel task/workspace/run state;
- obtains a real SHADOW `RoutingDecision`;
- records scheduler `WOULD_SELECT_TARGET` separately from `ACTUAL_RETAINED_TARGET`;
- starts the real worker under a recorded Safety Kernel run;
- runs the host-owned deterministic verifier when the worker exits successfully;
- finalizes the pending Shadow correlation into an immutable `ShadowObservation`;
- appends to the existing campaign journal after restart.

## Task Matrix

The first campaign matrix covers four bounded offline task families:

```text
BUG_FIX: deterministic rounding bug in src/invoice.py
TEST_ADD: missing upper-bound clamp regression in tests/test_ranges.py
REFACTOR: behavior-preserving shared word parser in src/text_stats.py
MULTI_FILE_CHANGE: report slug prefix split across src/labels.py and src/report.py
```

Every case uses a trusted deterministic verifier and changed-file scope checking.

## Provider And Worker Results

Credential-safe probes and executions were performed without reading, copying or displaying
credential material. P3.9 intentionally does not add Claude as a model provider.

```text
CODEX_CLI_AVAILABLE: true
CODEX_REAL_EXECUTION_TARGET: codex-cli-gpt-5.5
MINIMAX_OPENCODE_CLI_AVAILABLE: true
MINIMAX_CATALOG_PROBE: succeeded
MINIMAX_CREDENTIAL_REPAIR_ATTEMPTED: false
```

OpenCode/MiniMax credential repair was not attempted and no secret file was read to bypass the
P3.8 authentication boundary.

## Campaign Evidence

Durable local campaign state:

```text
CAMPAIGN_ROOT: .personal-ai-orchestrator/p39-shadow
REPORT: .personal-ai-orchestrator/p39-shadow/p39-real-shadow-quality-campaign-report.json
QUALITY_OBSERVATIONS: 6
VERIFIED_COUNT: 6
FAILED_OR_BLOCKED_COUNT: 0
TOTAL_REAL_PROVIDERS: 1
TOTAL_REAL_EXECUTION_TARGETS: 1
TOTAL_TASK_FAMILIES: 4
REAL_RESET_CYCLES: 0
SHADOW_REVIEW_ELIGIBLE: false
OWNER_APPROVAL: ABSENT
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
```

The campaign was run, stopped and re-run. Later invocations appended observations 5 through 6
instead of replacing the first four observations.

## Negative Observation

The accepted Codex-only P3.9 dataset does not yet include a real negative model/worker outcome.
The runner and tests prove that worker-process failures and verifier failures finalize as
`verified=false` Shadow observations instead of being rewritten as successes.

```text
REAL_NEGATIVE_OBSERVATION_COLLECTED: false
STRUCTURAL_NEGATIVE_FINALIZATION_TESTED: true
```

## WOULD_SELECT Vs Actual

All observations preserve:

```text
WOULD_SELECT_TARGET: none
ACTUAL_RETAINED_TARGET: codex-cli-gpt-5.5
SCHEDULER_REASON: no candidate passed hard eligibility and quota admission gates
QUOTA_CONFIDENCE: UNKNOWN
```

This is intentional fail-closed quota behavior. `WOULD_SELECT_TARGET = none` is not treated as a
scheduler failure while quota confidence remains `UNKNOWN`; actual retained targets still provide
quality evidence.

## Metrics Boundary

Current cohort metrics include observation counts, verified counts, blocked/failed counts,
attempts-to-green, time-to-green median, handoff count, regression count, quota confidence and
WOULD_SELECT agreement fields. `p90` time-to-green is intentionally `null` for these cohorts
because each cohort has too few samples for a useful percentile.

Blocking reasons remain:

```text
need at least 20 observations; have 6
need at least 2 real reset cycles; have 0
```

P3.9 does not satisfy longitudinal reset-cycle acceptance and does not authorize production ACTIVE.

## Local Verification

```text
Ruff full repository check: PASS
pytest full repository: 162 passed
git diff --check: PASS
OpenCode adapter typecheck + contract tests: PASS, 10/10 in /tmp dependency copy
P3.9 real Codex observations: 6 collected, 6 verified
P3.9 Claude-as-provider support: NOT ADDED
Restart append evidence: PASS, observations appended to 6 accepted Codex-only total
```

No production ACTIVE routing, owner approval, PR merge, ready-for-review transition, force push,
credential scraping, fake provider runs or fake reset-cycle evidence was created.
