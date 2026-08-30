# P3.9 Real Shadow Quality Campaign - 2026-08-30

Status: `P3_9_REAL_SHADOW_QUALITY_CAMPAIGN_COLLECTING_PARTIAL_ACCEPTED`

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
credential material.

```text
CODEX_CLI_AVAILABLE: true
CODEX_REAL_EXECUTION_TARGET: codex-cli-gpt-5.5
CLAUDE_CLI_AVAILABLE: true
CLAUDE_EXISTING_LOGIN_USABLE: true
CLAUDE_REAL_EXECUTION_SAFE: true
CLAUDE_ACTUAL_EXECUTION_TARGET_IDENTIFIABLE: true
CLAUDE_REAL_EXECUTION_TARGET: claude-code-sonnet
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
QUALITY_OBSERVATIONS: 8
VERIFIED_COUNT: 7
FAILED_OR_BLOCKED_COUNT: 1
TOTAL_REAL_PROVIDERS: 2
TOTAL_REAL_EXECUTION_TARGETS: 2
TOTAL_TASK_FAMILIES: 4
REAL_RESET_CYCLES: 0
SHADOW_REVIEW_ELIGIBLE: false
OWNER_APPROVAL: ABSENT
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
```

The campaign was run, stopped and re-run. Later invocations appended observations 5 through 8
instead of replacing the first four observations.

## Negative Observation

The dataset is not success-only. Observation `shadow-332e1595a9a66a5f1f7278d4` records a real
Claude worker-process failure caused by a CLI invocation argument error:

```text
ACTUAL_RETAINED_TARGET: claude-code-sonnet
WORKER_EXIT_CODE: 1
FINAL_TASK_STATE: BLOCKED
VERIFIED: false
```

The failure remains in the journal and contributes to `FAILED_OR_BLOCKED_COUNT`. It was not
rewritten as `VERIFIED`. A subsequent corrected Claude run produced verified observation
`shadow-469afc89dc83bdae7d186380`.

## WOULD_SELECT Vs Actual

All observations preserve:

```text
WOULD_SELECT_TARGET: none
ACTUAL_RETAINED_TARGET: codex-cli-gpt-5.5 or claude-code-sonnet
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
need at least 20 observations; have 8
need at least 2 real reset cycles; have 0
not every Shadow observation has a verified outcome
```

P3.9 does not satisfy longitudinal reset-cycle acceptance and does not authorize production ACTIVE.

## Local Verification

```text
Ruff full repository check: PASS
pytest full repository: 162 passed
git diff --check: PASS
OpenCode adapter typecheck + contract tests: PASS, 10/10 in /tmp dependency copy
P3.9 real Codex observations: 6 collected, 6 verified
P3.9 real Claude observations: 2 collected, 1 verified, 1 blocked
Restart append evidence: PASS, observations appended to 8 total
```

No production ACTIVE routing, owner approval, PR merge, ready-for-review transition, force push,
credential scraping, fake provider runs or fake reset-cycle evidence was created.
