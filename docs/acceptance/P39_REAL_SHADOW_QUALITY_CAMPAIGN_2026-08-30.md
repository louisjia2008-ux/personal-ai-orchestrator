# P3.9 Real Shadow Quality Campaign - 2026-08-30

Status: `P3_9_1_CODEX_ONLY_REAL_SHADOW_QUALITY_CAMPAIGN_RUNTIME_BLOCKED_PARTIAL_ACCEPTED`

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

## P3.9.1 Expansion

P3.9.1 expands the Codex-only case catalog to 20 variants across four task families:

```text
BUG_FIX: 5 variants
TEST_ADD: 5 variants
REFACTOR: 5 variants
MULTI_FILE_CHANGE: 5 variants
```

No Claude provider, Claude worker or Claude execution target was added.

## Task Matrix

The campaign matrix covers four bounded offline task families. P3.9 initially ran one variant per
family; P3.9.1 expanded this to five variants per family:

```text
BUG_FIX:
  bug_fix_rounding
  bug_fix_discount
  bug_fix_parse_bool
  bug_fix_median
  bug_fix_date_label
TEST_ADD:
  test_add_clamp
  test_add_slug_whitespace
  test_add_percent_bounds
  test_add_default_none
  test_add_dedupe_order
REFACTOR:
  refactor_text_stats
  refactor_average
  refactor_names
  refactor_query
  refactor_inventory
MULTI_FILE_CHANGE:
  multi_file_labels
  multi_file_checkout_tax
  multi_file_config_title
  multi_file_routes
  multi_file_metrics_report
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
P3.9_ROOT: .personal-ai-orchestrator/p39-shadow
P3.9_QUALITY_OBSERVATIONS: 6
P3.9_VERIFIED_COUNT: 6
P3.9_FAILED_OR_BLOCKED_COUNT: 0

P3.9.1_ROOT: .personal-ai-orchestrator/p391-codex-shadow
P3.9.1_REPORT: .personal-ai-orchestrator/p391-codex-shadow/p39-real-shadow-quality-campaign-report.json
P3.9.1_GENERATED_AT_UTC: 2026-08-30T14:08:24.531893+00:00
P3.9.1_GENERATED_AT_ASIA_SHANGHAI: 2026-08-30 22:08:24
P3.9.1_SELECTED_VARIANTS: 20
P3.9.1_QUALITY_OBSERVATIONS: 20
P3.9.1_QUALITY_ELIGIBLE_OBSERVATIONS: 8
P3.9.1_VERIFIED_COUNT: 8
P3.9.1_FAILED_OR_BLOCKED_COUNT: 12
TOTAL_REAL_PROVIDERS: 1
TOTAL_REAL_EXECUTION_TARGETS: 1
TOTAL_TASK_FAMILIES: 4
REAL_RESET_CYCLES: 0
SHADOW_REVIEW_ELIGIBLE: false
OWNER_APPROVAL: ABSENT
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
```

The P3.9 campaign was run, stopped and re-run. Later invocations appended observations 5 through 6
instead of replacing the first four observations. P3.9.1 used a clean campaign root to avoid
mixing the corrected Codex-only acceptance dataset with earlier ignored local scratch evidence.

## Negative Observation

The P3.9.1 Codex-only run produced 12 real negative worker outcomes after the first 8 verified
observations. The raw Codex CLI stderr for each blocked case reported usage limit exhaustion and
the next retry time. These are operational/policy-limit blocks, not model-quality failures.

The runner now distinguishes these failure classes:

```text
MODEL_TASK_FAILURE
VERIFIER_FAILURE
WORKER_INVOCATION_FAILURE
AUTH_FAILURE
TIMEOUT
INFRA_FAILURE
POLICY_BLOCK
CANCELLED
UNKNOWN_FAILURE
```

It also preserves `WORKER_PROCESS_FAILURE` for generic nonzero worker exits that do not match a
more specific class.

```text
REAL_NEGATIVE_OBSERVATION_COLLECTED: true
REAL_USAGE_LIMIT_BLOCKS_COLLECTED: 12
STRUCTURAL_NEGATIVE_FINALIZATION_TESTED: true
FUTURE_USAGE_LIMIT_CLASSIFICATION: POLICY_BLOCK / POLICY_BLOCKED
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

P3.9.1 quality metrics:

```text
TIME_TO_GREEN_P50_SECONDS: 24.954862833488733
TIME_TO_GREEN_P90_SECONDS: null
FAILURE_CLASS_COUNTS_IN_REPORT: NONE=8, WORKER_PROCESS_FAILURE=12
RAW_FAILURE_ROOT_CAUSE: Codex CLI usage limit
```

The report was generated before the final policy-block classifier hardening in this branch, so the
existing local report preserves the generic append-only `WORKER_PROCESS_FAILURE` classification for
the 12 usage-limit exits. The raw stderr establishes the root cause, and future matching exits are
classified as `POLICY_BLOCK`. P3.9.2 adds the observed exhaustion/recovery governor and circuit
breaker for this case; see
[`P392_OBSERVED_EXHAUSTION_RECOVERY_GOVERNOR_2026-08-31.md`](P392_OBSERVED_EXHAUSTION_RECOVERY_GOVERNOR_2026-08-31.md).

Blocking reasons remain:

```text
need at least 2 real reset cycles; have 0
not every Shadow observation has a verified outcome
```

P3.9 does not satisfy longitudinal reset-cycle acceptance and does not authorize production ACTIVE.

## Local Verification

```text
Ruff full repository check: PASS
pytest full repository: 170 passed, 28 warnings
git diff --check: PASS
OpenCode adapter typecheck + contract tests: PASS, 10/10 in /tmp dependency copy
P3.9 real Codex observations: 6 collected, 6 verified
P3.9.1 real Codex observations: 20 collected, 8 verified, 12 usage-limit blocked
P3.9.1 target tests: 41 passed, 1 warning
P3.9 Claude-as-provider support: NOT ADDED
Restart append evidence: PASS, observations appended to 6 accepted Codex-only total
Unsupported quota policy analysis: COMPLETE
```

No production ACTIVE routing, owner approval, PR merge, ready-for-review transition, force push,
credential scraping, fake provider runs or fake reset-cycle evidence was created.
