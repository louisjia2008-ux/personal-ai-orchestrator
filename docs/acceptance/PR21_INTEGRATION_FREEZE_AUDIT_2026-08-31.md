# PR21 Integration Freeze Audit - 2026-08-31

Status: `PR21_MERGE_READY_WITH_PRODUCTION_ACTIVE_DISABLED`

## Scope

This audit freezes and reviews the PR #21 integration line only. The audited diff is:

```text
REPOSITORY: louisjia2008-ux/personal-ai-orchestrator
WORKTREE: /Users/louisjia/Developer/pao-integration-acceptance
BRANCH: integration/end-to-end-shadow-safety
PR: #21
BASE: facd25d5634814e442eea9292f1dff21101b1a22
AUDITED_HEAD: c407a77b68382db2594b6d05b38f96838caee210
EXPECTED_STARTING_HEAD: 55a7a07866b52473b245632459ff1cd31fe854b7
```

During the audit, PR #21 was externally merged and the integration branch advanced from
`55a7a07866b52473b245632459ff1cd31fe854b7` to
`c407a77b68382db2594b6d05b38f96838caee210` with a documentation-only MiniMax CN provider-surface
clarification. That follow-up did not change code, Shadow evidence, exact quota/reset authority,
owner approval, or production ACTIVE state.

`origin/main` later advanced beyond PR #21 through P4 work. That later P4 mainline state is outside
this audit.

## Baseline Reconciliation

```text
LOCAL_WORKTREE_CLEAN: true
REMOTE_BRANCH_HEAD: c407a77b68382db2594b6d05b38f96838caee210
PR_STATE_AT_AUDIT_CLOSE: MERGED
PR_DRAFT_AT_AUDIT_CLOSE: false
PR_MERGEABILITY_AT_AUDIT_CLOSE: UNKNOWN_AFTER_MERGE
GITHUB_ACTIONS_AT_HEAD: test PASS, opencode-adapter PASS
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
OWNER_APPROVAL: ABSENT
REAL_PROVIDER_EXACT_RESET_CYCLES: 0
OBSERVED_LOCAL_RECOVERY: RECOVERED_OBSERVED
```

The PR state differs from the requested expected starting state (`OPEN / DRAFT / MERGEABLE`)
because it was merged externally during this audit. No merge, ready-for-review transition, or force
push was performed by this audit.

## CI And Local Verification

```text
ruff check .: PASS
pytest -p no:cacheprovider: 180 passed, 28 warnings
git diff --check: PASS
OpenCode adapter npm run typecheck: PASS, 10/10 contract tests
target Mac acceptance: PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED
GitHub Actions test: PASS
GitHub Actions opencode-adapter: PASS
```

Sandbox note: the first in-sandbox full pytest and target Mac acceptance runs failed only where
tests attempted to bind `127.0.0.1` temporary loopback ports. The same commands were rerun outside
the sandbox and passed.

## P0 Safety Kernel Audit

Result: `PASS_NO_MERGE_BLOCKING_DEFECT_FOUND`

Verified from `safety_kernel.py`, `execution_controller.py`, `switch_lease.py`, tests, and target
Mac acceptance:

- worker start requires RUNNING task state plus exact writer lock ownership;
- DB enforces one active RUNNING run per task;
- worker exit updates run/task/audit in one transaction;
- nonzero worker exit and malformed worker result move the task to BLOCKED, not VERIFIED;
- startup reconciliation blocks uncertain RUNNING, WORKER_FINISHED and VERIFYING tasks;
- missing worktree reconciliation blocks still-routable tasks and releases stale locks only after
  blocked or terminal state;
- cancellation checks task, run, PID and writer token before signalling;
- cancellation aborts active switch leases before task-state mutation;
- no worker path writes integration authority over main.

No fail-open exception handling or partial durable authority transition was found in the audited
P0 path.

## P1 Verifier Audit

Result: `PASS_NO_MERGE_BLOCKING_DEFECT_FOUND`

Verified from `verifier.py`, `verification_evidence.py`, `execution_controller.py`, tests, and
target Mac acceptance:

- verifier commands are host-defined argv tuples, not natural-language shell strings;
- changed-file scope is checked before running verifier commands;
- `git diff --check` is part of host verification when required by profile;
- verifier evidence IDs are content-addressed and append-only;
- duplicate evidence ID with changed content is rejected;
- `apply_verification_result` advances to VERIFIED only when the exact passing
  `VerificationResult` is already present in the host journal;
- missing, malformed, forged or mismatched evidence fails closed to BLOCKED;
- Shadow campaign helpers cannot fabricate VERIFIED task state.

## ACTIVE Default-Off Proof

Result: `PASS_ACTIVE_DEFAULT_OFF`

Default state:

```text
ActiveRoutingGate(): authorized == false
RuntimeConfig: extra activation_gate input rejected
daemon build_service(): always installs ActiveRoutingGate()
OpenCode plugin default mode: SHADOW
BYPASS: KEEP_CURRENT
SHADOW: RECORD_ONLY
ACTIVE without complete gate: switch_requested == false
```

Expected negative gates:

```text
DEFAULT_ACTIVE_STATE: OFF
NO_OWNER_APPROVAL: ACTIVE_DENIED
NO_REAL_RESET_ACCEPTANCE: ACTIVE_DENIED
UNKNOWN_QUOTA: ACTIVE_DENIED
RECOVERED_OBSERVED_ONLY: ACTIVE_DENIED
```

No audited path creates production ACTIVE merely because the scheduler recommends a model, Shadow
becomes review eligible, quota availability is `RECOVERED_OBSERVED`, local estimates look healthy,
or a campaign reaches an observation threshold.

## Switch Lease Audit

Result: `PASS_NO_MERGE_BLOCKING_DEFECT_FOUND`

The OpenCode adapter cannot switch from a routing decision alone. A switch requires:

- request mode ACTIVE;
- `switch_requested=true`;
- selected model present;
- exact task state version present and matching the request;
- durable routing decision persisted;
- switch authorization endpoint success;
- `SwitchLeaseAuthority` reloading the persisted decision;
- task still READY or RUNNING at the same state version;
- one live authorized lease per task;
- bounded lease TTL;
- SQLite task-state freeze until lease completion, abort or expiry.

Malformed, stale, mismatched, non-switch, expired or already-resolved leases fail closed.

## Quota Authority Audit

Result: `PASS_CONSERVATIVE_QUOTA_SEMANTICS`

- `UNKNOWN` quota confidence cannot carry precise quota values.
- `ESTIMATED` evidence is distinct from `EXACT`.
- `LOCALLY_MEASURED` does not become `PROVIDER_EXACT`.
- unsupported quota policy simulation cannot create real reset evidence or owner approval.
- `QuotaAvailabilityEvidence` rejects `EXACT`, `PROVIDER_EXACT`, `reset_at` and
  `remaining_fraction`.
- weak negative observed availability may remove candidates.
- weak positive observed availability cannot bypass exact quota admission or production ACTIVE.

Non-blocking implementation note: `route_task()` accepts observed availability and tests cover the
weak-negative/weak-positive asymmetry. The headless `RoutingService` does not yet auto-load the
availability journal into normal daemon routing. This is not a merge blocker while production
ACTIVE is disabled and exact quota gates remain conservative, but it is the next integration point
before relying on observed availability for always-on daemon scheduling.

## Shadow Evidence Audit

Result: `PASS_CONSERVATIVE_SHADOW_EVIDENCE`

- `ShadowObservation` is append-only and content/ID conflicts are rejected.
- reset cycle references must exist before an observation can count them.
- real reset cycles count only when source is `PROVIDER_EXACT`, confidence is `EXACT`, and
  `reset_at` is known.
- synthetic, unknown, local-inference and estimated reset references do not count for real
  longitudinal acceptance.
- `real_attempt_count` is separate from `quality_eligible_observations`.
- `POLICY_BLOCK` is not a model-task failure.
- `DEFERRED_QUOTA` queue entries do not create fake Shadow observations.
- `RECOVERED_OBSERVED` creates no exact reset evidence.
- campaign summary keeps `production_active_authorized=false`.
- campaign review eligibility does not create owner approval.

P3.9.1 historical boundary:

```text
REAL_ATTEMPT_COUNT: 20
QUALITY_ELIGIBLE_ATTEMPT_COUNT: 8
VERIFIED_OUTCOME_COUNT: 8
USAGE_LIMIT_POLICY_BLOCKS_IN_HINDSIGHT: 12
REAL_PROVIDER_EXACT_RESET_CYCLES: 0
```

P3.9.2 observed recovery boundary:

```text
REAL_RECOVERY_ATTEMPTS: 2
VERIFIED_RECOVERY_OUTCOMES: 2
AVAILABILITY_STATE: RECOVERED_OBSERVED
REAL_PROVIDER_EXACT_RESET_CYCLES: 0
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
OWNER_APPROVAL: ABSENT
```

These snapshots are not combined into a new production-acceptance statistic.

## Security And Credential Scan

Result: `PASS_NO_SECRET_OR_RUNTIME_STATE_COMMITTED`

Scans performed:

- tracked-file scan for `node_modules`, `.personal-ai-orchestrator`, `.env`, private keys,
  package-lock/runtime reports and quota-availability runtime state;
- grep for API-key, bearer-token, authorization-header, private-key and token patterns;
- `.gitignore` review for cache/runtime exclusions.

Findings:

- no tracked `node_modules`;
- no tracked `.personal-ai-orchestrator` runtime state;
- no tracked env/private-key files;
- no provider credential contents found;
- sanitizer code intentionally contains forbidden field names such as `access_token`,
  `refresh_token`, `id_token`;
- one test intentionally contains a fake Bearer-shaped string to verify rejection;
- docs mention official provider endpoints and sanitized local evidence paths only.

## Warning Classification

```text
PROJECT_WARNING: 0
DEPENDENCY_WARNING: 1 npm dependency deprecation warning for node-domexception
DEPRECATION_WARNING: 28 pytest-asyncio warnings from Python 3.14 event-loop policy APIs
TEST_INFRA_WARNING: 0 project-owned unresolved warnings
```

The pytest warnings originate from `pytest_asyncio/plugin.py`, not project-owned code. They were
not globally suppressed.

## Dead Code And Temporary Artifact Review

Result: `PASS_NO_SAFE_REMOVAL_REQUIRED`

No tracked generated dependency directories, runtime campaign state, debug breakpoints, or obvious
merge-blocking temporary code were found. Historical P3.6/P3.7/P3.8/P3.9 scripts and acceptance
documents are preserved intentionally as evidence records, not deleted as superseded scratch.

## Documentation Consistency

Result: `PASS_WITH_EXTERNAL_STATE_NOTE`

README, architecture, roadmap, scheduler correctness, integration status and acceptance documents
continue to distinguish:

```text
IMPLEMENTED
LOCALLY_ACCEPTED
SHADOW_EVIDENCE_PARTIAL
PROVIDER_EXACT_RESET_PENDING
PRODUCTION_ACTIVE_DISABLED
```

No reviewed document claims that Codex quota percentage/reset time is known, that P3 is
production-authorized, or that Claude was added as the P3 campaign provider. Current GitHub state
differs from older PR-state references only because PR #21 has already been merged externally.

## Merge-Readiness Result

```text
MERGE_READINESS: PR21_MERGE_READY_WITH_PRODUCTION_ACTIVE_DISABLED
PRODUCTION_ACTIVE_READINESS: NOT_READY
OWNER_APPROVAL: ABSENT
LONGITUDINAL_RESET_EVIDENCE: INCOMPLETE
KNOWN_P0_P1_SAFETY_BLOCKERS: none found
KNOWN_FAIL_OPEN_ACTIVE_PATHS: none found
CREDENTIAL_LEAK_BLOCKERS: none found
EVIDENCE_SEMANTICS_BLOCKERS: none found
```

This result does not authorize production ACTIVE. It only says the PR #21 integration line was safe
to merge with production ACTIVE still disabled. The PR was already externally merged before this
audit document was written.

## Remaining Work

- collect real Shadow evidence across required provider-exact quota reset cycles;
- establish supported credential handoff for provider-native MiniMax/Z.AI quota evidence where
  needed;
- validate production target-host OpenCode switch timing before any ACTIVE rollout;
- keep owner approval as an explicit independent authority;
- integrate observed availability journal loading into daemon scheduling before depending on it for
  long-running non-production campaign throttling.
