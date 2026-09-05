# Integration Status

Status: `INTEGRATION_IMPLEMENTED / LOCAL_MAC_ACCEPTED / P4_2_4_B_OWNER_DISPATCH_DELIVERED_WITH_TRANSPARENCY_AND_POLICY_RECOMMENDER`

This document records what the `integration/end-to-end-shadow-safety` line implements and what
still requires evidence that cannot be fabricated on GitHub-hosted runners.

## Integrated execution path

```text
OpenCode thin adapter
  -> POST /v1/opencode/route (loopback only)
  -> RoutingService
  -> deterministic Scheduler
     -> hard capability/risk/context/runtime gates
     -> quota/task admission
     -> deterministic ranking
  -> immutable RoutingDecision refs + exact task_state_version
  -> durable SafetyKernelStore decision ledger
  -> SHADOW record-only
  -> ACTIVE only if every activation gate is satisfied
       -> POST /v1/opencode/authorize-switch
       -> durable short-lived switch lease + task-state freeze
       -> session-scoped switchModel side effect
       -> POST /v1/opencode/resolve-switch
```

The loopback API validates Host and Origin by parsed hostname rather than string prefix matching,
so origins such as `http://localhost.evil.invalid` are rejected instead of being treated as local.
OpenCode routing requests may carry a host-owned task ID and state version. A configured
`TaskProfile` is only routable when it is backed by durable Safety Kernel state; READY/RUNNING are
the only routable states, stale supplied versions fail closed, and ACTIVE requires an exact state
version. An ACTIVE decision that requests a switch carries that same version as part of its
immutable contract.

## ACTIVE switch authorization boundary

The prior distributed TOCTOU boundary has been tightened at the durable task-state layer. A routing
decision alone is not authority to mutate the OpenCode session. Immediately before the side effect,
the adapter must request a short-lived switch lease from the orchestrator.

The switch authority reloads the persisted decision, requires ACTIVE plus `switch_requested=true`,
binds decision/request/task/version/session identity, revalidates the current durable task version,
allows at most one live switch lease per task, freezes task state/version transitions through a
SQLite trigger, resolves COMPLETED or ABORTED after the local switch attempt, and bounds a crashed
adapter's freeze with a 10-second default lease and 30-second hard maximum.

This closes the scheduler-to-switch state race for authoritative task transitions inside the
Safety Kernel. Target-Mac acceptance is still required to validate real OpenCode session-switch
behavior and timing before production ACTIVE is enabled.

## P0 Safety Kernel foundation

Implemented:

- SQLite/WAL task, run, workspace, approval, audit and routing-decision state;
- typed versioned task state machine and idempotent submission;
- host-owned Git worktrees and one writer token per task worktree;
- DB-level one-RUNNING-run-per-task constraint;
- atomic worker admission with exact writer ownership;
- atomic worker exit with run/task/audit updates;
- transactional authoritative mutation plus audit for critical state writes;
- task/run identity checking before mutation;
- conservative persisted worktree adoption after path/registration/branch/base validation;
- exact process-group supervision and cancellation;
- cancellation requires persisted PID == exact supervised process PID, aborts switch leases, then
  atomically marks run/task CANCELLED, releases writer ownership and writes audit evidence;
- PID mismatch fails before signalling;
- daemon startup fail-closed execution and workspace reconciliation;
- no worker transition directly to VERIFIED/COMPLETED.

Injected failure tests prove rollback for worker start, worker exit, routing-decision writes and
startup reconciliation. Linux CI does not replace target-Mac process/filesystem acceptance.

## P1 deterministic verification foundation

Implemented host-defined argv-only verifier profiles, changed-file scope checks, `git diff --check`,
bounded deterministic stages, immutable evidence identity and append-only journal. VERIFIED requires
the exact passing `VerificationResult` already present in that journal; forged, missing or mismatched
evidence fails closed to BLOCKED.

## P2.5 scheduler corrections

Implemented separate logical `ModelSKU` and concrete `ExecutionTarget`, fail-closed simultaneous
quota windows, absolute usable-headroom admission, capability/risk/context/vision/tool gates,
failure-count escalation, deterministic scheduler-to-adapter bridge, immutable policy/quota refs,
durable retry semantics, duplicate-race convergence and request-ID misuse rejection.

## P3 provider audit

Static audit coverage includes MiniMax Token Plan, Z.AI Coding Plan, OpenAI API and Codex/ChatGPT
plan allowance, Anthropic API and Claude subscription allowance, DeepSeek PAYG balance, and
local/unmetered runtimes. `UNKNOWN` remains intentional where no stable supported remaining-plan
quota surface exists.

Live provider blockers remain local authentication/reset evidence only; credential files are not
read or copied merely to manufacture acceptance evidence.

## P3.5 Shadow evidence

Implemented append-only observations, stable identity, manual-vs-scheduler targets, immutable
catalog/policy/quota refs, before/after quota refs, predicted/observed burn, verified outcomes,
regressions, attempts/time-to-green/handoffs, reset-cycle accounting and conservative
`review_eligible` summary. `review_eligible` is not ACTIVE authorization.

## P3.7 authoritative Shadow evidence hardening

P3.6 bootstrapped provider-surface discovery and local Shadow campaign state, but it did not
automatically collect verified task observations. P3.7 repairs the evidence authority before any
real longitudinal Shadow time can count toward production ACTIVE.

Implemented:

- append-only persisted `ResetCycleReference` records under the Shadow journal;
- observation append validation requiring every referenced reset cycle to exist and match the
  observation provider/quota pool;
- explicit reset-cycle source semantics: synthetic tests, exact provider metadata, estimated
  provider metadata, local inference and unknown;
- real longitudinal reset counting only for provider-exact, exact-confidence reset references with
  a known reset time;
- quality Shadow observations can still be collected when quota/reset evidence is unknown, but
  unknown or synthetic reset evidence cannot satisfy the quota-reset production gate;
- durable pending Shadow correlations written after SHADOW routing decisions when the host knows the
  retained actual execution target;
- verifier-result finalization hooks that create immutable observations only from host-owned
  Safety Kernel and deterministic verifier truth;
- cohort-scoped review eligibility by provider, quota pool, actual execution target and task
  family;
- campaign acceptance policy snapshots persisted with the campaign, so later threshold edits do not
  rewrite historical acceptance.

Campaign-level `review_eligible` is a summary of scoped cohort evidence, not production ACTIVE
authorization and not proof that every target/provider path is accepted.

## P3.8 first real end-to-end Shadow observation

P3.8 records the first credential-safe real worker execution through the authoritative Shadow
observation pipeline. OpenCode/MiniMax was probed first but existing authentication was not usable
(`401 invalid api key`), so the run used the already-authenticated Codex CLI path without reading,
copying or displaying credentials.

The accepted observation used a disposable Git repository and a host-owned deterministic verifier:

```text
SELECTED_REAL_WORKER: codex-cli
PROVIDER: openai
ACTUAL_EXECUTION_TARGET: codex-cli-gpt-5.5
AUTH_REUSED_WITHOUT_SECRET_READ: YES
DISPOSABLE_REPO: /var/folders/pt/tn46s3216rg366nxx7g7ng940000gn/T/pao-p38-real-shadow-4bvwb7kp
TASK_ID: p38-real-shadow-7287b7e34d22
ROUTING_REQUEST_ID: route-9b542f213c1d
ROUTING_DECISION_ID: route-06cca2f09f28ec0601352846
WOULD_SELECT_TARGET: none, because quota confidence remained UNKNOWN
ACTUAL_RETAINED_TARGET: codex-cli-gpt-5.5
REAL_WORKER_EXECUTION: exit 0
VERIFIER_RESULT: VERIFIED / verify-2ac646548adbfea20e176981
CHANGED_FILES: src/tiny_math.py
FIRST_REAL_SHADOW_OBSERVATION: shadow-79b324d8b775bb0d2e80e221
QUALITY_OBSERVATIONS: 0 -> 1
CAMPAIGN_STATUS: BOOTSTRAPPED -> COLLECTING
QUOTA_CONFIDENCE: UNKNOWN
RESET_METADATA: UNKNOWN_OR_ABSENT
REAL_RESET_CYCLES_CONTRIBUTED: 0
REAL_RESET_CYCLES_TOTAL: 0
SHADOW_REVIEW_ELIGIBLE: false
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
OWNER_APPROVAL: ABSENT
```

This proves the real collection path, not the longitudinal production gate. Unknown quota/reset
metadata can produce quality Shadow observations, but it still contributes zero real reset cycles
and cannot make a cohort production-ACTIVE eligible.

See
[`acceptance/P38_FIRST_REAL_SHADOW_OBSERVATION_2026-08-30.md`](acceptance/P38_FIRST_REAL_SHADOW_OBSERVATION_2026-08-30.md).

### MiniMax provider-surface follow-up (2026-08-31)

The historical P3.8 record above is unchanged: the MiniMax surface it probed on 2026-08-30 really
did return `401 invalid api key`. A credential-safe follow-up probe on 2026-08-31 (existing
authentication reused, no credential contents read, copied or displayed) clarified that the 401 was
scoped to the international endpoint surface, not to MiniMax as a provider:

```text
minimax-cn / minimax-cn-coding-plan: AUTH_OK / EXECUTABLE (live micro-probe succeeded)
international minimax / minimax-coding-plan: 401 invalid api key
INTERPRETATION: endpoint/credential-surface mismatch, not global MiniMax unavailability
```

This correction updates current provider truth only. It does not rewrite the P3.8 acceptance
report, does not add provider-exact quota/reset truth, and does not change any Shadow campaign
evidence or production gate state.

## P3.9 real Shadow quality campaign

P3.9 turns the single P3.8 pipeline proof into a reusable declarative campaign runner and a small
real Codex-only quality dataset. The runner creates fresh disposable Git repositories, records real SHADOW
routing decisions, preserves `WOULD_SELECT_TARGET` separately from `ACTUAL_RETAINED_TARGET`, starts
real worker processes through Safety Kernel run state, applies host-owned deterministic verifier
profiles and appends immutable Shadow observations to a durable campaign journal.

The first campaign matrix covers four offline task families:

```text
BUG_FIX
TEST_ADD
REFACTOR
MULTI_FILE_CHANGE
```

Current local P3.9/P3.9.1 campaign evidence on 2026-08-30:

```text
P3.9_CAMPAIGN_ROOT: .personal-ai-orchestrator/p39-shadow
P3.9_QUALITY_OBSERVATIONS: 6
P3.9_VERIFIED_COUNT: 6
P3.9_FAILED_OR_BLOCKED_COUNT: 0

P3.9.1_CAMPAIGN_ROOT: .personal-ai-orchestrator/p391-codex-shadow
P3.9.1_REPORT: .personal-ai-orchestrator/p391-codex-shadow/p39-real-shadow-quality-campaign-report.json
P3.9.1_SELECTED_VARIANTS: 20
P3.9.1_QUALITY_OBSERVATIONS: 20
P3.9.1_QUALITY_ELIGIBLE_OBSERVATIONS: 8
P3.9.1_VERIFIED_COUNT: 8
P3.9.1_FAILED_OR_BLOCKED_COUNT: 12
P3.9.1_BLOCKED_ROOT_CAUSE: Codex CLI usage limit
TOTAL_REAL_PROVIDERS: 1
TOTAL_REAL_EXECUTION_TARGETS: 1
TOTAL_TASK_FAMILIES: 4
REAL_RESET_CYCLES: 0
SHADOW_REVIEW_ELIGIBLE: false
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
OWNER_APPROVAL: ABSENT
```

Observed real targets:

```text
codex-cli-gpt-5.5: 20 P3.9.1 observations, 8 verified, 12 usage-limit blocked
```

P3.9 intentionally does not add Claude as a model provider. All P3.9/P3.9.1 observations have
`WOULD_SELECT_TARGET = none` because quota confidence is still `UNKNOWN` and the scheduler
correctly fails closed. Verified observations remain useful quality data but contribute zero real
reset cycles. P3.9.1 also proves that Codex subscription usage-limit exhaustion can stop the
quality campaign before the 20-observation accepted threshold is reached.

Unsupported exact subscription quota/reset surfaces are now analyzed separately. The recommended
policy keeps production ACTIVE exact-only while allowing locally measured or user-declared quota
windows only as explicitly labeled non-production scheduling hints.

P3.9.2 turns observed provider exhaustion into host-owned admission/backoff state:

```text
OBSERVED_AVAILABILITY_STATES: UNKNOWN, AVAILABLE_OBSERVED, EXHAUSTED_OBSERVED, COOLDOWN, RECOVERY_PROBE_DUE, RECOVERED_OBSERVED
WEAK_NEGATIVE_EVIDENCE: may remove an execution target
WEAK_POSITIVE_EVIDENCE: cannot satisfy exact quota admission or production ACTIVE
QUALITY_READINESS_COUNTER: quality_eligible_observations, not total operational observations
```

P3.9.2 seeded sanitized availability from the P3.9.1 usage-limit evidence without rewriting
historical observations, then ran a bounded Codex-only recovery probe:

```text
P3.9.2_CAMPAIGN_ROOT: .personal-ai-orchestrator/p392-codex-governor
P3.9.2_REAL_ATTEMPT_COUNT: 2
P3.9.2_QUALITY_ELIGIBLE_ATTEMPT_COUNT: 2
P3.9.2_VERIFIED_OUTCOME_COUNT: 2
P3.9.2_POLICY_BLOCK_COUNT: 0
P3.9.2_AVAILABILITY_STATE: RECOVERED_OBSERVED
P3.9.2_EXHAUSTION_OBSERVED_AT_UTC: 2026-08-30T14:08:24.526784Z
P3.9.2_RECOVERY_OBSERVED_AT_UTC: 2026-08-31T01:00:28.841398Z
P3.9.2_EXHAUSTION_TO_RECOVERY_SECONDS: 39124.314614
P3.9.2_MEASUREMENT_SOURCE: LOCALLY_MEASURED
P3.9.2_CONFIDENCE: ESTIMATED
P3.9.2_RESET_CYCLE_SOURCE: LOCALLY_INFERRED
P3.9.2_REAL_PROVIDER_EXACT_RESET_CYCLES: 0
```

See
[`acceptance/P39_REAL_SHADOW_QUALITY_CAMPAIGN_2026-08-30.md`](acceptance/P39_REAL_SHADOW_QUALITY_CAMPAIGN_2026-08-30.md)
and
[`acceptance/P391_UNSUPPORTED_QUOTA_POLICY_ANALYSIS_2026-08-30.md`](acceptance/P391_UNSUPPORTED_QUOTA_POLICY_ANALYSIS_2026-08-30.md).
See also
[`acceptance/P392_OBSERVED_EXHAUSTION_RECOVERY_GOVERNOR_2026-08-31.md`](acceptance/P392_OBSERVED_EXHAUSTION_RECOVERY_GOVERNOR_2026-08-31.md).

## Current CI evidence

P3.9.1 code-bearing integration head verified on GitHub Actions:
`9fadfbc657a2c0c36bc6e8a858af1ad1cb45410c`.

```text
test: PASS
opencode-adapter: PASS
```

The PR check rollup remains the authority for the latest pushed branch head, including docs-only
follow-up commits.

Current local P3.9.2 validation on 2026-08-31:

- Ruff: PASS;
- targeted pytest: **43 passed**, 1 pytest-asyncio deprecation warning;
- full pytest: **180 passed**, 28 pytest-asyncio deprecation warnings after sandbox-external
  loopback HTTP rerun;
- `git diff --check`: PASS;
- OpenCode adapter typecheck + contract tests: PASS, 10/10 in a `/private/tmp` dependency copy;
- P3.8 real Shadow execution script: PASS, observation `shadow-79b324d8b775bb0d2e80e221`;
- P3.9 real Shadow campaign runner: PASS, 6 Codex-only observations across 1 provider, 1
  execution target and 4 task families;
- P3.9.1 real Shadow campaign: 20 Codex-only observations across 4 task families and 20 variants,
  with 8 verified and 12 blocked by Codex CLI usage limit;
- unsupported quota policy analysis: COMPLETE, production ACTIVE remains exact-only;
- P3.9.2 recovery probe: 2 Codex-only real attempts, 2 verified, recovered availability observed,
  0 provider-exact reset cycles;
- target Mac acceptance: `PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED`.

The normal PR CI remains the authority for pushed branch heads. Local Mac acceptance is tracked
separately because GitHub-hosted runners cannot prove the credential-safe local worker path.

## External acceptance still required

- supported credential handoff for provider-native MiniMax/Z.AI quota evidence on the intended
  local environment;
- real Shadow evidence across multiple quota reset cycles;
- explicit owner acceptance for production ACTIVE routing.

Until those gates are satisfied, production ACTIVE routing remains disabled by design.

## Target Mac local acceptance

Local disposable-repository acceptance on 2026-08-30 recorded:
`PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED`.

See [`acceptance/TARGET_MAC_ACCEPTANCE_2026-08-30.md`](acceptance/TARGET_MAC_ACCEPTANCE_2026-08-30.md).

This evidence covers the local P0 Safety Kernel, P1 deterministic verifier, loopback routing API,
OpenCode adapter fail-closed semantics, SHADOW record-only behavior and negative ACTIVE gate
combinations. It does not include provider-native MiniMax/Z.AI quota truth, multiple real Shadow
reset cycles, Keychain-specific credential handoff, or production owner approval.

## P4.2.4-B owner-initiated dispatch (fix/p4-final-ui-repair)

The owner-dispatch surface — manual target selection and the policy-driven
"按策略派发" / "按策略推荐" panel — was delivered end-to-end on
`fix/p4-final-ui-repair` and re-validated on `feat/p4-final-ui-repair` head
`63a524b`. Owner acceptance evidence:

- A disposable main repo + worktree + verifier profile is provisioned by
  `product_daemon.ensure_execution_policies()`, which is idempotent and
  fail-soft: the daemon still boots without owner-dispatch capability when
  the policies are unavailable.
- `POST /v1/tasks/{id}/dispatch` creates the `owner_dispatches` row,
  atomically transitions `READY → RUNNING` via
  `SafetyKernelStore.start_dispatched_worker`, and spawns a daemon thread
  that supervises the worker.
- The worker runs against a host-owned sandbox `worker-opencode.json` that
  allows edits inside the assigned worktree and denies bash / webfetch
  outright. The repo stays bit-identical across the run or the task fails
  closed with `MAIN_REPO_MUTATED`.
- The worker run row's persisted result includes the bounded sanitized
  `stdout_tail` and `stderr_tail` (8 KiB, ANSI-free, control-char-free).
  Owner-facing fields: task id, request id, execution target, scheduling
  policy, scheduling policy detail line, manual target (when policy =
  MANUAL), worker transcript tail, run status, exit code, output hashes.
- `POST /v1/tasks/{id}/dispatch/recommendation` ranks every
  dispatchable target by the task's archived policy and returns the top
  pick + per-row score / headroom / evidence_fresh / quota_state / reasons.
  Each row has its own "Dispatch" button that flows through the same
  handler as the manual dispatch.
- `POST /v1/tasks/{id}/cancel` cancels the active supervised process with
  the host-owned exact pid check; cancellation cannot deadlock behind a
  switch lease.
- The macOS app shows the manual dispatch and the recommendation panel
  on the task detail surface, with localized dispatch buttons
  (`action.dispatch`) and policy labels (`policy.manual`,
  `recommendation.panelTitle`).

Known limitations inherited by M0/M1:

- `recommend_owner_dispatch` uniformly sets capability_fit = 1.0 — owner
  dispatch does not infer intent into required capabilities. M1 WP2
  replaces this with tier-based matching.
- `_admit_quota` collapses quota state per provider rather than per
  binding window; UNCERTAIN_LOCKED fires per-target but the threshold is
  per-target, not per-pool-per-window.
- `target.execution_verified` is no longer consulted as a launch gate;
  the journal is the single source of truth (with `latest_verified_for_target`
  providing the demote-fallback so a transient UNKNOWN does not destroy a
  real verified history).

## P0 M0 trust hardening (fix/m0-trust)

The M0 line made the displayed state trustworthy:

- A1: `ExecutionEvidenceJournal.latest_verified_for_target` returns
  `(verified_evidence, stale_since)`. `ExecutionTargetHealthView` and
  `DispatchRecommendationCandidate` expose `execution_verified_stale` so
  the UI can render "we have history, but the latest run did not
  actually succeed" without re-running the verification probe.
- A2: `QuotaAvailabilityState.UNCERTAIN_LOCKED` fires after
  `UNCERTAIN_LOCKED_THRESHOLD` (=3) consecutive failed quota collections
  for the same target. A single `observe_success()` resets the streak and
  releases the lock atomically. Admission rejects with `QUOTA_UNKNOWN`.
- A3: `_emergency_repair` now persists a self-describing payload
  (`signal: 9` for SIGKILL, plus `emergency_repair: true`) and a
  human-readable reason ("worker exited unexpectedly (signal 9)").
  `_failure_result_payload()` is the single helper used by both the
  normal failure path and the emergency-repair path.
- A4: `RunView.pid_alive` is computed by `os.kill(pid, 0)` against RUNNING
  rows; the Swift inspector renders "已退出" instead of the stale
  "running pid N" the moment the OS confirms the worker is gone.
- A5: `pytest-rerunfailures` added to the dev extras; the daemon SIGINT
  shutdown test is decorated with `@pytest.mark.flaky(reruns=3, reruns_delay=1)`.

## P3.6 live provider and Shadow campaign

P3.6 provider-surface discovery on 2026-08-30 found no supported machine-readable remaining-quota
truth available through existing local non-secret CLI metadata. MiniMax OpenCode model catalog
metadata is visible, Z.AI is not configured as an OpenCode provider on this host, Codex/Claude CLI
availability does not prove subscription quota truth, DeepSeek remains PAYG-balance-only without a
safe credential handoff, and local Ollama capacity remains runtime availability rather than provider
quota.

The P3.6 local Shadow campaign was bootstrapped in ignored durable state under
`.personal-ai-orchestrator/p36-shadow/` with production ACTIVE disabled. P3.7 clarifies that this is
`BOOTSTRAPPED`, not `COLLECTING`, until automatic observation wiring is enabled for a real execution
path. It has 0 quality observations and 0 real reset cycles so far, so Shadow review eligibility
remains false.

See
[`acceptance/LIVE_PROVIDER_SHADOW_ACCEPTANCE_2026-08-30.md`](acceptance/LIVE_PROVIDER_SHADOW_ACCEPTANCE_2026-08-30.md).
See also
[`acceptance/P37_AUTHORITATIVE_SHADOW_ACCEPTANCE_2026-08-30.md`](acceptance/P37_AUTHORITATIVE_SHADOW_ACCEPTANCE_2026-08-30.md).
See also
[`acceptance/P38_FIRST_REAL_SHADOW_OBSERVATION_2026-08-30.md`](acceptance/P38_FIRST_REAL_SHADOW_OBSERVATION_2026-08-30.md).
