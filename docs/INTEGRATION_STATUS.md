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

Known limitation inherited by M1 (carry into M1, fix in M2):

- **Emergency kill does not drain the worker pipes.** The supervisor
  cancels the process group with SIGKILL on the emergency-repair path,
  but does not capture the pipe buffers; `_failure_result_payload()`
  therefore emits `signal: 9` and a synthesised reason, never the
  worker's last stdout/stderr tail. Bounded tails (ANSI / control
  stripped, 8 KiB each) are only available on the normal exit path
  where the supervisor drains the pipes before reap. Do **not** try
  to fix this in M1; M2 migrates to `opencode serve` and the session
  abort API will replace the SIGKILL path entirely.
- A4: `RunView.pid_alive` is computed by `os.kill(pid, 0)` against RUNNING
  rows; the Swift inspector renders "已退出" instead of the stale
  "running pid N" the moment the OS confirms the worker is gone.
- A5: `pytest-rerunfailures` added to the dev extras; the daemon SIGINT
  shutdown test is decorated with `@pytest.mark.flaky(reruns=3, reruns_delay=1)`.

## M1 WP0 — daemon tick infrastructure (feat/m1-wp0-daemon-tick)

The first WP of M1 puts a deterministic periodic step registry in
front of the bundled daemon so every later WP (burn / tiers / pressure
scoring / supervised auto / backlog / weekly report) registers its
periodic work in one place.

- **DaemonSupervisor** (`src/personal_ai_orchestrator/daemon_supervisor.py`).
  One daemon thread drives registered callables on a fixed interval.
  Each step is isolated (one exception does not skip siblings); three
  consecutive failures enter backoff (fires once every 12 ticks until a
  success); `clock` is injected so tests advance time deterministically.
  Naive clocks are rejected with a warning instead of crashing.
- **Audit contract**. Heartbeat is intentionally silent in
  `audit_events`. Only `SUPERVISOR_STEP_FAILED`,
  `SUPERVISOR_STEP_BACKOFF` and `SUPERVISOR_STEP_RECOVERED` are
  written, and only via `SafetyKernelStore.record_system_event` —
  the new public wrapper introduced for cross-module system audits.
  Heartbeat must not pollute the audit log every 5 seconds.
- **Daemon main loop** (`src/personal_ai_orchestrator/daemon.py`).
  Replaced `while not stop.wait(3600): pass` with
  `supervisor.run(stop)` so both `--control-only` and the routing path
  run a heartbeat. New `--tick-interval-seconds` CLI flag falls back
  to `PAO_TICK_INTERVAL_SECONDS` env var, then 5.0s default.
- **/v1/health**. `HealthView` gains three optional fields:
  `last_tick_at` (ISO string, `None` until the first tick),
  `tick_interval_seconds` (float), `supervisor_steps` (tuple of
  `SupervisorStepView` carrying per-step name / last_run_at /
  last_duration_ms / consecutive_failures / in_backoff). All three
  are absent-tolerant — pre-WP0 daemons decode cleanly, and old
  clients that only read `status` / `api_version` are unaffected.
- **Swift side**. `HealthView` and new `SupervisorStepView` decode
  the new fields with `decodeIfPresent`. `lastTickDate` /
  `lastRunDate` reuse `TaskTiming.parseTimestamp` for relative-time
  formatting. Three new Swift decoder tests (forward + backward
  compatible + minimal SupervisorStepView).
- **Process daemon wiring** (`product_daemon.py`). The bundled
  daemon's `build_daemon_argv` takes an optional
  `tick_interval_seconds` parameter; nothing changes by default.

WP0 deliberately ships exactly one step (`heartbeat`). WP1+ WP
branches register their periodic work behind the same registry without
touching `daemon.py` again.

## M1 WP1 — burn curve (`feat/m1-wp1-burn`)

WP1 puts a deterministic classification in front of every observed
quota window: how fast am I burning this quota relative to the ideal
line, and is there still time to change course before it resets. The
seven-value truth table (`UNMETERED` / `STALE` / `EXHAUSTED` /
`STARVED` / `AHEAD` / `BEHIND` / `ON_TRACK`) is the single source of
truth shared by the dashboard card, the dispatch candidate chip, and
the future WP3 scorer.

- **`quota_burn.py`** (new). Pure functions, zero I/O. `assess(...)`
  classifies one window at one `now`; `infer_window_started_at(...)`
  reconstructs `reset_at - duration(kind)` for windows the collector
  omitted; `rolling_hourly_cap(...)` is the sliding rule the 5h
  admission path uses. **Only naive datetimes raise** — every other
  ill-shaped input (used_fraction outside `[0, 1]`, total ≤ 0,
  clock-skew `now < window_started_at`, `now >= reset_at`) is clamped
  or coerced and the truth table proceeds. A quota handler that
  crashes on a 3-second-stale reset snapshot would be worse than the
  snapshot itself.
- **`STARVED` semantics.** "Lots of remaining, reset imminent" — the
  verdict means "let it expire and you wasted it", **not** "almost
  empty". The UI label is "Expiring unused" / "将过期未用".
- **`source_pressure` lives in `dispatch_recommender.source_pressure_for`**
  so the card and the recommender can never disagree about which row
  of the truth table fired. `PlanQuotaProjection.source_pressure()`
  delegates to the same `assess` call.
- **`/v1/quota` shape.** Every `QuotaPlanWindowView` now carries an
  optional `burn` sub-object (8 fields; UNMETERED returns `null` for
  every numerical field, STALE keeps real values). Every
  `QuotaProviderCardView` carries an optional `source_pressure` string.
  Every `DispatchRecommendationCandidate` carries the same string for
  the recommendation panel chip. **All three are absent-tolerantly
  decoded** — pre-WP1 daemons decode cleanly.
- **Audit-only-on-failure stays in force**: `source_pressure` is
  read-only projection, not a journal write.
- **WP1 is pipeline-only**, scoring is WP3's job. `_score` and
  `_score_candidate` are untouched. The `DispatchCandidateInput.windows`
  field is in place for WP3 to consume.

WP1 deliberately ships no new daemon-side periodic step. WP2–WP7
register their work behind the same `DaemonSupervisor` registry WP0
introduced without touching `daemon.py` again.

## M1 WP2 — model tier table (`feat/m1-wp2-tiers`)

WP2 turns the dispatch recommender's flat `capability_fit = 1.0` into
a tier-aware function. Every task carries a `min_tier` floor (T0
flagship, T1 workhorse, T2 fast, T3 free); every target is classified
against a host-owned tier table at startup; the recommender hard-
eliminates a target strictly below `min_tier` and applies a gentle
penalty (`1.0 - 0.1 * (tier_index(min_tier) - tier_index(tier))`) to
an over-qualified target. The verdict surfaces on three wire
shapes — the per-target `ExecutionTargetHealthView.tier`, the
recommendation candidate `DispatchRecommendationCandidate.tier`,
and the task's `TaskView.min_tier` — and the Swift dashboard reads
all three.

### `model_tiers.py` — pure module

- `ModelTier(StrEnum)`: T0/T1/T2/T3. Lower index = higher capability.
- `tier_index(t)` + `meets_minimum(t, min)`: 4×4 matrix covered by
  parametrised tests.
- `TierEntry(tier, caps)`: frozen dataclass. `caps` is reserved for
  WP3 per-capability score components.
- `TierTable(entries, default)`: frozen dataclass + `lookup(target_key)`
  returning `(entry, reason)` where reason is one of `"exact"` /
  `"glob"` / `"default"`. Patterns use `fnmatch`; longest match wins.
- `parse_tier_table(raw)`: strict validator (`version == 1`, every
  entry is `{tier, caps}`, tier ∈ ModelTier, caps non-empty strings).
  Every error message names the offending field.
- `merge_tier_tables(default, override)`: override patterns replace
  same-key default patterns; the rest is a union.
- `DEFAULT_TIER_TABLE_JSON`: shipped defaults cover the four
  zai-coding-plan / minimax-{cn-,}coding-plan / minimax-* families
  (T1) and `opencode-*-free` (T3, pre-installed for WP4 free-tier
  targets). Everything else falls back to the default T1 entry.

### `target_key` format

A `target_key` is one `execution_target_id`: a string of the shape
`"{provider_id}-{sku}"` (single dash, never a slash). The dash
shape is the same one `provider_discovery.py:1290` synthesises at
discovery time (`f"{record.provider_id}-{sku}"`). `fnmatch` `*`
matches any character **including dashes**, so `opencode-*-free`
matches both `opencode-glm-4.5-free` and `opencode-glm-free`. The
host that wants narrower matching must spell out each pattern
explicitly; always scope a free-tier glob to a provider prefix so a
future `minimax-cn-coding-plan-GLM-4.5-free` SKU does not silently
inherit a free-tier classification.

### Fourth host-owned artifact

`runtime-state/policies/model-tiers.json` joins the existing three
(execution-repo, verifier-profile.json, worker-opencode.json) with
the same idempotent-seed / owner-edits-never-overwritten rule. The
daemon loads it on startup; the loader returns `(table, source)`
where `source` is one of `"owner_file"` / `"default_fallback"`
and surfaces on `/v1/health.model_tiers_source`. A malformed file
emits `MODEL_TIERS_INVALID` (`{path, error, error_type}`) via
`store.record_system_event` and falls back to the shipped defaults
— owner dispatch never goes dark because of a tier-table typo.
The table is loaded exactly once at startup; there is no hot reload
by design (the table is small, the recommender is a hot path, and
a mid-session swap would silently change every recommendation in
flight). A host that wants to change the table restarts the daemon.

### Storage + control API

- `TaskRecord.min_tier: str = "T1"` + `_ensure_column("tasks",
  "min_tier", "TEXT NOT NULL DEFAULT 'T1'")`. The schema bump is
  idempotent; legacy stores read "T1" via a `row["min_tier"]`
  fallback in `_task_from_row`.
- `TaskSubmitRequest.min_tier: str | None = None` (Pydantic
  validator rejects strings outside the four known values with a
  `400 invalid_min_tier`). `None` normalises to "T1" at storage.
- `TaskView.min_tier: str = "T1"` defaults so the Swift picker and
  the recommender see the same default.
- `ControlPlaneService.tier_table` + `model_tiers_source` are
  populated by `daemon.build_control_service` after
  `load_model_tiers(path, audit=store)`. `open_request` threads
  them through to the request-local copy.
- `_collect_dispatch_candidates` resolves each target's tier via
  `self.tier_table.lookup(target_id)`; the resulting
  `(ModelTier, match_reason)` tuple flows into
  `DispatchCandidateInput.tier` / `.tier_match_reason`.
- `_execution_target_view` does the same for the per-target
  provider-card surface.

### Recommender integration

- `DispatchCandidateInput.tier: ModelTier | None = None` +
  `tier_match_reason: str | None = None`. Defaults keep every
  existing call site compiling.
- `_hard_eligibility` adds the tier-floor check. A target strictly
  below `min_tier` returns
  `(False, "tier_below_minimum(tier={T2},min_tier={T1})")` so the
  UI can render the reason verbatim. `tier is None` does NOT
  eliminate — the scoring path treats None as T1 and the reasons
  tuple records `tier_unknown_assumed_T1` so the UI can flag it.
- `_score` uses
  `capability_fit = 1.0 - 0.1 * (tier_index(min_tier) - tier_index(tier or T1))`,
  clamped to `[0, 1]`. Gentle penalty by design — WP3's pressure
  term can absorb it. The floor is hard because picking a model
  below the task's tier is the kind of decision no policy override
  should undo.
- `recommend_owner_dispatch` accepts
  `min_tier: ModelTier = ModelTier.T1` (default keeps every
  existing call site behaving identically). The reasons tuple
  includes `tier=T1 min_tier=T1 match=exact|glob|default` on every
  admitted candidate.

### `ensure_execution_policies` consumer map (all four callsites)

The four-artifact return value is consumed as follows (line numbers
on `feat/m1-wp2-tiers @ 1a49405`):

- `product_daemon.ensure_execution_policies` returns the 4-tuple
  (`src/personal_ai_orchestrator/product_daemon.py:160`).
- `product_daemon.main` unpacks policies and passes the fourth
  path to `build_daemon_argv` (`product_daemon.py:222-235`).
- `daemon.parse_args` exposes `--model-tiers-path`
  (`daemon.py:122-128`).
- `daemon.build_control_service` loads the JSON via
  `load_model_tiers(path, audit=store)` and threads the result into
  `ControlPlaneService(tier_table=..., model_tiers_source=...)`
  (`daemon.py:248-273, 334-339`).
- `daemon.main` passes `args.model_tiers_path` through
  (`daemon.py:407`).

### Swift surface

- `SubmitRequest.minTier: String?` (Encodable; nil emits no JSON
  key so the daemon applies its T1 default). `TaskView.minTier`,
  `ExecutionTargetHealthView.tier` / `.tierMatchReason`,
  `DispatchRecommendationCandidate.tier` / `.tierMatchReason`,
  `HealthView.modelTiersSource`: all `decodeIfPresent` so pre-WP2
  fixtures still parse cleanly.
- `OrchestratorStore.selectedMinTier: String = "T1"` (default T1
  preserves existing user behaviour).
- `DashboardView.NewTaskSheet`: four-tier `Picker` with help line.
- `QuickSubmitView`: same picker, inline.
- `Resources/ResourceExecutionTargetsSection.ExecutionTargetRow`:
  tier chip alongside the existing VERIFIED/UNVERIFIED chip.
  `tone: .neutral` always; SF Symbol map
  `bolt.fill` / `gearshape.fill` / `hare.fill` / `leaf.fill`.
- `Tasks/TaskExecutionSection.DispatchRecommendationRow`: same tier
  chip on the recommendation panel.

### Default table content (final)

The shipped `model-tiers.json` covers every known provider family
on the basis of `provider_discovery.PROVIDER_FAMILIES`:

```json
{
  "version": 1,
  "tiers": {
    "zai-coding-plan-*":                   {"tier": "T1", "caps": ["coding"]},
    "minimax-cn-coding-plan-*":            {"tier": "T1", "caps": ["coding"]},
    "minimax-coding-plan-*":               {"tier": "T1", "caps": ["coding"]},
    "minimax-*":                           {"tier": "T1", "caps": []},
    "opencode-*-free":                     {"tier": "T3", "caps": []}
  }
}
```

The `opencode-*-free` glob is a pre-installed placeholder for the
WP4 free-tier targets that do not exist yet. The host that wants
narrower matching must spell out each pattern explicitly.

### Audit-only-on-failure stays in force

`/v1/health.model_tiers_source` is read-only projection, not a
journal write. `MODEL_TIERS_INVALID` is the only system event WP2
emits, and only on a malformed host file.

### Backward compatibility

Every new field on the wire is Optional. Pre-WP2 daemons decode
cleanly: `TaskView.minTier` reads `nil`, the `tier` chip selector
falls back to its "unknown" label, `HealthView.modelTiersSource`
reads `nil`. WP2 is pipeline-only; scoring weights are not touched
(WP3's job).

## M1 WP3 — pressure scoring (`feat/m1-wp3-pressure-scoring`)

WP3 turns the dispatch recommender's flat `capability_fit = 1.0`
into a tier-aware, pressure-aware function and threads the same
shape through the scheduler's `evaluate_target` path. Both paths
consume a single shared `ScoreWeights` dataclass — so a future
tuning commit that wants to nudge weights touches the dataclass
once and both score columns move together.

### `ScoreWeights` (in `scheduler.py`)

```python
@dataclass(frozen=True)
class ScoreWeights:
    quality: float    # × capability_fit
    pressure: float   # × (−pressure_score)
    headroom: float   # × min(remaining_fractions)
    latency: float    # existing latency term, value unchanged
    cost: float       # × (−expected_cost), M1 holds at 0
```

`objective_weights(objective) -> ScoreWeights` (correction: no
private prefix; previously private `_objective_weights` was
cross-module-imported by `dispatch_recommender`):

| objective | quality | pressure | headroom | latency | cost |
|---|---|---|---|---|---|
| QUALITY_FIRST | 1.4 | 0.2 | 0.3 | (existing) | 0.1 |
| QUOTA_SAVER | 0.5 | 0.4 | 0.3 | (existing) | 1.0 |
| SPEED_FIRST | 0.9 | 0.6 | 0.4 | (existing) | 0.5 |
| **BURN_DOWN** | 0.4 | 1.0 | 0.6 | (existing) | 0.2 |
| BALANCED / MANUAL fallback | 0.7 | 0.6 | 0.4 | (existing) | 0.3 |

`RoutingObjective.BURN_DOWN = "BURN_DOWN"` joins the enum; the
owner-facing picker exposes it via
`scheduling_settings.SELECTABLE_GLOBAL_POLICIES`.

### Score formula (both paths)

```
score = weights.quality  × capability_fit
      + weights.pressure × pressure_term
      + weights.headroom × headroom_term
      − weights.latency  × latency_term
      − weights.cost     × cost_term
```

- `capability_fit`: WP2 (tier penalty 0.1 per step, clamped 0–1).
- `pressure_term`: `−pressure_score` of the WEEKLY window
  (`QuotaWindowSnapshot.burn(now)`). UNMETERED / STALE → `0` +
  reason `burn_unmetered` / `burn_stale_ignored`. STARVED → `+1.0` +
  reason `quota_expiring_unused` (the verdict at the ceiling; no
  extra `w_pressure` to avoid double-counting).
- `headroom_term`: `min(remaining_fractions)` across the candidate's
  observed windows. None → `0` + reason `headroom_unmetered`.
- `latency_term`: existing `log1p(expected_latency_ms / 1000.0)`.
- `cost_term`: `0` (M1 holds; the cost surface lands in M3).

**`Σ weight × value == core_score`** is the scoring identity (the
test pins both paths to it). Legacy pre-WP3 nudges
(`membership_weight_bonus`, `priority_penalty`, `success_prior_bonus`,
`scarcity_*`) are appended as their own `ScoreComponent` rows with
`source="legacy_nudge"` so shadow-campaign ranking does not drift.

### Hard constraints (both paths)

- **5h rolling smoothing**: `rolling_hourly_cap(used_fraction,
  elapsed_seconds)` returns true AND `tier(target) != min_tier` →
  hard-eliminate, reason
  `rolling_window_smoothing(tier=T0,min_tier=T1)`. `tier ==
  min_tier` is exempt. Missing `window_started_at` or missing
  `used_fraction` short-circuit to "no gate fires".
- `EXHAUSTED` is not gated here — admission / `quota_state` cover
  it. `pressure_term = −1.0` naturally ranks it last.

### Scheduler path (`evaluate_target`)

- Pulls the binding up so the 5h smoothing gate can read
  `FIVE_HOUR` windows.
- `headroom_min` reuses the existing
  `snapshot.minimum_remaining_fraction(at=now)` (correction: don't
  compute separately). None → `0` + reason `headroom_unmetered`.
- Reads `weekly_window.burn(now)` for `pressure_term`. The
  `STALE` or `window_start_inferred=True` paths downgrade the
  component's `confidence` to `ESTIMATED` so the UI can label the
  bar with "(estimated start)" — the same affordance the WP1
  `burn.window_start_inferred` field already provided.
- `score_components` rows now carry real `confidence` (EXACT /
  ESTIMATED / UNKNOWN) and `source` strings
  (`registry.capabilities`, `burn_curve.weekly`,
  `burn_curve.inferred`, `quota_window.minimum_remaining_fraction`,
  `target_telemetry.expected_latency_ms`,
  `target_telemetry.expected_cost_to_green_usd`,
  `legacy_nudge`). The previous "SCORE_WEIGHTS" catch-all is gone.
- `evaluate_target` short-circuits `MANUAL` (correction #9): the
  scheduler never produces an auto-rank for it. `score=None`,
  `admitted=False`, reason "manual policy: orchestrator does not
  auto-rank". The recommender (defence-in-depth) falls back to
  `BALANCED` weights + tags every admitted candidate's reasons
  with `manual_policy_recommendation_uses_balanced`.

### Dispatch recommender (`recommend_owner_dispatch`)

- `_headroom` returns `(headroom_min, headroom_mean)`. The minimum
  drives the score (correction #4); the mean stays on the
  candidate view for the UI.
- `_score` uses the same five-term shape as the scheduler.
- `_hard_eligibility` adds the 5h smoothing gate (correction: both
  paths, not just the recommender).
- `effective_policy = BALANCED` when asked to recommend against
  `MANUAL`; `result.policy` on the wire still reads `"MANUAL"` so
  the client does not see a label it did not ask for.

### DAG (after WP3, no cycles)

```
quota_burn.py (pure stdlib)
    ↑
model_tiers.py (pure stdlib)
    ↑
quota_availability.py
quota_observability.py
    ↑
model_registry.py
    ↑
scheduler.py           ← ScoreWeights / objective_weights
    ↑ ↑
dispatch_recommender.py
control_api.py
product_daemon.py  ────┘
```

Direction verified with `grep -rn "^from personal_ai_orchestrator"`.
No reverse edge from `scheduler` → `dispatch_recommender`.

### Storage + control API

- `TaskRecord.min_tier: str = "T1"` (already WP2; WP3 reads it via
  `recommend_dispatch`).
- `DispatchRecommendationCandidate.headroom_min: float | None = None`
  (WP3 commit 4). The view threads the binding-window minimum;
  `None` propagates the "every window has missing data" signal.
- `DispatchRecommendationScoreComponent.weight: float | None = None`
  (commit 4's wire shape). The Swift decoder reads it as
  `decodeIfPresent Double?`.
- `recommend_dispatch` reads `task.min_tier` (defaults to T1) and
  threads it into `recommend_owner_dispatch`. Corrupt values
  raise `TASK_MIN_TIER_INVALID` (commit 4) and fall back to T1
  with the `min_tier_invalid_assumed_T1(raw=…)` reason flag.

### Swift surface

- `DispatchRecommendationCandidate.headroomMin: Double?` — the
  binding-window minimum drives the headline number alongside
  `score`. `headroomMean` stays alongside.
- `DispatchRecommendationScoreComponent.weight: Double?` — per-row
  weight for the expanded score-components table.
- `enum SelectablePolicyFallback.policies` — the single source of
  truth for the owner-facing fallback list (correction #8).
- `L10n.schedulingPolicyName("BURN_DOWN")` / `.detail("BURN_DOWN")`
  with new `policy.burnDown` / `policy.burnDown.detail` strings
  in both `en.lproj` and `zh-Hans.lproj`.
- `ResourceQuotaSection` draws the ideal-pace tick (WP1 deferred →
  WP3 ships): `QuotaMeter(idealPaceTick: binding.burn?.expectedUsedFraction)`
  draws a 1.5pt vertical line at the ideal-pace position. `nil`
  legacy fixtures render without the tick.

### Backward compatibility

Every new field on the wire is Optional / absent-tolerant. Pre-WP3
daemons decode cleanly: `headroomMin` and `weight` are both
`decodeIfPresent`; Swift's lenient `String?` decoder handles the
new `"BURN_DOWN"` value (the test
`testBurnDownPolicyStringDecodesAndRenders` pins the round-trip).
The 9 frozen `ROUTING_ROLE_CONTRACT.md` fixtures are unchanged —
the new BURN_DOWN coverage is an inline JSON test in
`RoutingContractTests.swift` (no new fixture file).

## M1 WP4 — unmetered pool (`feat/m1-wp4-unlimited-pool`)

WP4 lands the OpenCode Zen free-model family on the dispatch
side and the macOS Resources page. The new ``pool_kind`` /
``auth_kind`` axis lets the daemon and the picker distinguish
``"windowed"`` providers (which report quota windows) from
``"unmetered"`` providers (which do not — only locally observed
rate limits apply).

### Discovery (commit 1)

- ``ProviderFamilySpec``: new ``auth`` (``"env"`` | ``"oauth"`` |
  ``"none"``), ``pool_kind`` (``"windowed"`` | ``"unmetered"``),
  and ``free_model_skus`` (explicit SKU listing).
- ``PROVIDER_FAMILIES`` adds the ``opencode`` family with
  ``auth="none"``, ``pool_kind="unmetered"``, and the 7
  fixtures (`big-pickle`, `ling-3.0-flash-fin-free`,
  `mimo-v2.5-free`, `muse-spark-1.2-contributor-free`,
  `muse-spark-1.3-contributor-free`, `nemotron-3-ultra-free`,
  `nemotron-3.5-lightning-free`). Free-models-glob cross-check
  emits ``FREE_MODEL_SUFFIX_UNLISTED{sku}`` (suffix-unlisted)
  or ``OPENCODE_MODEL_UNCLASSIFIED{sku}`` (paid-looking); both
  dedup per daemon lifetime.
- ``ProviderDiscovery``: new ``auth_kind`` / ``pool_kind`` fields
  mirror the spec.
- ``model_tiers.DEFAULT_TIER_TABLE_JSON``: ``opencode-*-free``
  glob already present from WP2 speculation; add the explicit
  ``opencode-big-pickle`` override (T3) for the one free SKU
  without the ``-free`` suffix.

### Quota model (commit 2)

- ``QuotaWindowKind.UNMETERED`` added; ``duration_seconds()``
  returns ``None``.
- ``QuotaAvailabilityState.AVAILABLE_UNMETERED`` added;
  ``UNCERTAIN_LOCKED`` is unreachable for unmetered targets
  because ``consecutive_failures`` is pinned to ``0``.
- ``observe_rate_limited`` transition: 15-minute default
  cooldown, never bumps ``consecutive_failures``.
- ``previous_state_baseline`` field on
  ``QuotaAvailabilityEvidence`` so ``state_at`` picks the right
  expiry path (windowed → ``RECOVERY_PROBE_DUE``; unmetered →
  ``AVAILABLE_UNMETERED`` directly).
- ``UnmeteredQuotaCollector``: pure-local collector (no I/O).
  Emits one ``UNMETERED`` window + a ``PlanQuotaProjection``
  with the covered free SKUs as the pool. ``evidence()``
  returns a fresh ``AVAILABLE_UNMETERED`` journal entry with
  the unmetered baseline pinned.
- ``daemon.default_quota_collectors()`` registers the opencode
  collector (no conditional on credentials).

### Worker outcome classification (commit 3)

- ``worker_outcome_classifier.classify_worker_failure``:
  conservative marker list (``"usage limit"``, ``"rate limit"``,
  ``"too many requests"``, ``"quota exceeded"``,
  ``"insufficient quota"``, ``"429"`` for QUOTA_OR_RATE_LIMIT;
  ``"unauthorized"``, ``"401"``, ``"403"``, ``"not logged in"``
  for AUTH). The bare token ``"quota"`` is **not** in the list
  — it matches this project's own source code and was the
  source of the regression risk the user flagged for shadow's
  old list. ``stdout`` is ignored by design.
- ``shadow_campaign_runner._worker_failure_classification``
  delegates to the new helper (the legacy bare-``"quota"`` text
  match is gone).
- ``dispatch_executor._classify_and_record_worker_outcome``
  runs the helper on the worker's ``stderr_tail``. On
  ``QUOTA_OR_RATE_LIMIT``:
  - windowed targets (``pool_kind="windowed"``) call
    ``observe_exhaustion`` (1h cooldown, the legacy windowed
    behaviour).
  - unmetered targets (``pool_kind="unmetered"``) call
    ``observe_rate_limited`` (15-minute cooldown).
  - both write a ``QUOTA_BLOCKED`` evidence row; the existing
    ``latest_verified_for_target`` demote-fallback covers
    ``QUOTA_BLOCKED`` automatically (the §3.4 contract).
- 4.4 scoring assertions: T3 task with unmetered top pick;
  COOLDOWN unmetered target eliminated with ``recovers_at``
  reason; 5h smoothing skips unmetered targets (no FIVE_HOUR
  window exists).

### Control plane views (commit 4)

- ``ExecutionTargetHealthView``: ``auth_kind`` / ``pool_kind``.
- ``QuotaProviderCardView``: ``pool_kind`` + ``unmetered`` block
  (carrying ``rpm_observed``, ``error_rate_1h``,
  ``cooldown_until``).
- ``UnmeteredObservationView``: read-time view-model.
- ``_build_unmetered_observation`` derives metrics from
  existing stores (no new supervisor step): ``rpm_observed``
  from the runs table count in the last 60s, ``error_rate_1h``
  from the last hour of ``ExecutionEvidenceJournal`` rows,
  ``cooldown_until`` from the earliest target-level cooldown.

### macOS app (commit 5)

- The Resources page splits populated resources into windowed
  vs unmetered buckets. Unmetered cards render under a new
  ``resource.group.unmetered`` header with a system-image-free
  label so it reads as a category, not a status.
- 6 new L10n keys in both ``en.lproj`` and ``zh-Hans.lproj``:
  ``resource.group.unmetered``, ``quota.windowKind.unmetered``,
  ``quota.unmetered.errorRate``, ``quota.unmetered.rpm``,
  ``quota.unmetered.cooldownUntil``, ``quota.unmetered.noWindow``.
- All six enter ``L10n.requiredKeys`` so ``LocalizationTests``
  catches a missing translation.

### Known limitations (commit 3.5)

The worker-outcome classifier uses text match on ``stderr_tail``;
OpenCode changing the error message format would silently miss
the verdict. M2 migration to ``opencode serve`` typed errors
will replace the helper with a structured field; the
``dispatch_executor`` call site stays the same.

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


## M1 WP5a-1 — SUPERVISED_AUTO foundations (`feat/m1-wp5a1-auto-foundations`)

This section documents the technical foundation only. **Autonomous
execution is NOT yet implemented.** The host-owned planning tick,
the grace countdown execution, the auto endpoints, the mode-change
abort, and the pending-shadow autonomous lifecycle all belong to
`feat/m1-wp5a2-auto-tick` (WP5a-2) and must not start until this WP
is merged and reviewed.

### Status

- **TECHNICAL FOUNDATION COMPLETE**: settings, persisted policy
  fields, task states, routing contract mode, data model, API /
  client decoding, and documentation all landed.
- **AUTONOMOUS EXECUTION NOT YET IMPLEMENTED**: no tick, no
  countdown, no auto endpoints, no autonomous dispatch.
- **PRODUCTION ACTIVE UNCHANGED**: ``mode == "ACTIVE"`` still
  requires authorised activation authority; the facade returns
  409 ``production_active_not_authorized`` when not authorised.

### Settings (commit 2)

- ``SchedulingSettings.mode`` —
  ``MANUAL`` (pre-WP5a-1 default) / ``SUPERVISED_AUTO`` /
  ``ACTIVE``. ``SET DEFAULT_MODE = "MANUAL"`` so a pre-WP5a-1
  settings file loads cleanly.
- ``SchedulingSettings.set_mode`` validates the value against
  ``SELECTABLE_MODES``; the facade rejects
  ``mode = "ACTIVE"`` without activation authority with 409
  ``production_active_not_authorized``.
- The persisted JSON payload gains a ``mode`` key alongside
  ``default_scheduling_policy`` so a single atomic write covers
  both fields.
- ``SchedulingSettingsView`` exposes ``mode`` + ``selectableModes``.

### Project-level policy (commit 2)

- ``projects`` table gains three columns:
  ``supervised_auto_allowed`` / ``unattended_allowed`` /
  ``grace_seconds`` (defaults ``False`` / ``False`` / ``120``).
- ``SafetyKernelStore.set_project_settings`` validates
  ``grace_seconds`` in ``[1, 86_400]``; ``ValueError`` is
  mapped to 400 ``invalid_grace_seconds`` by the facade.
- ``PUT /v1/projects/{id}/settings`` persists the three fields
  with an audit event
  ``PROJECT_SUPERVISED_AUTO_SETTINGS_SET``.

### Task state machine (commit 3)

- ``TaskState.AUTO_PLANNED`` and ``TaskState.AUTO_GRACE`` are the
  two new values.
- The transition map is **minimal**:
  - ``READY → AUTO_PLANNED`` (tick promotion — WP5a-2 lands)
  - ``AUTO_PLANNED → {AUTO_GRACE, READY, BLOCKED, CANCELLED}``
  - ``AUTO_GRACE → {READY, BLOCKED, CANCELLED}`` (no autonomous
    ``AUTO_GRACE → RUNNING`` in WP5a-1)
- Four new tasks-table columns:
  ``auto_decision_id`` / ``auto_grace_deadline_at`` /
  ``auto_acked_at`` / ``auto_reason``. All ``NULL`` by default.

### Routing contract (commit 3)

- ``RoutingMode.SUPERVISED_AUTO = "SUPERVISED_AUTO"`` is a
  closed-union value. The Python validator already rejects
  ``switch_requested = True`` for any non-ACTIVE mode; this
  contract survives unchanged.
- ``resolve_adapter_outcome`` adds an explicit
  ``SUPERVISED_AUTO`` branch that returns ``RECORD_ONLY`` with
  reason ``"supervised-auto decision is host-executed and
  cannot switch the current session"``. The adapter never
  initiates this mode — the side-effect gate at
  ``decision_contract.ts:59`` (``mode !== "ACTIVE"``) remains
  intact, and the plugin's ``optionMode`` guard still only accepts
  ``BYPASS`` / ``SHADOW`` / ``ACTIVE``.

### Frozen WP5a-2 contracts (commit 3 — recorded for WP5a-2 to honor)

The following contracts are frozen in code comments + this section
so WP5a-2's tick implementation cannot accidentally weaken them:

#### Active lease check

``SwitchLeaseAuthority.has_active_lease(task_id)`` (to be added in
WP5a-2) must satisfy **all** of:

- ``task_id`` matches
- ``status == "AUTHORIZED"``
- ``expires_at > now``

Expired leases must NOT permanently block the auto tick. A
subsequent ``authorize`` call on the same decision must produce
a fresh lease.

#### Mode change abort

When a task is in ``AUTO_PLANNED`` or ``AUTO_GRACE`` and the
effective scheduling mode changes from ``SUPERVISED_AUTO`` to
anything else, WP5a-2 must fail closed:

- abort the autonomous execution (no ``AUTO_DISPATCHED``)
- stop the grace countdown (``auto_grace_deadline_at`` cleared
  on transition)
- no dispatch
- pending shadow cleaned / finalised per the lifecycle rules in
  ``docs/M1_WP5_SPEC.md`` §3.6
- no stale autonomous action after owner policy change

The clear-on-veto helper that wipes ``auto_decision_id`` /
``auto_grace_deadline_at`` / ``auto_acked_at`` /
``auto_reason`` lands in WP5a-2; WP5a-1 deliberately does NOT
clear them on a ``READY`` veto so the helper's commit can
intentionally introduce the clear.

### Test coverage summary

- ``tests/test_dispatch_initiator.py`` (9 tests) — refactor
  preservation contract.
- ``tests/test_dispatch_recommendation_service.py`` (6 tests) —
  recommendation service contract.
- ``tests/test_scheduling_settings.py`` (10 tests) — mode
  round-trip + ACTIVE gate 409 + legacy payload compatibility.
- ``tests/test_project_settings.py`` (10 tests) — fresh DB,
  pre-WP5a-1 DB migration, set_project_settings round-trip,
  grace_seconds validation, audit event.
- ``tests/test_safety_kernel_transactions.py`` (+9 tests) —
  AUTO_PLANNED / AUTO_GRACE state machine, version increments,
  rollback, persistence, veto path.
- Swift ``ModelAndStatusTests`` (+7 tests since commit 1) —
  SchedulingSettingsView + ProjectView + TaskView decode
  (current + lenient legacy).
- Swift ``RoutingContractTests`` (+1 test) — SUPERVISED_AUTO
  decode on the routing view (no new fixture file).
- ``integrations/opencode/decision_contract.test.ts`` (+7 tests)
  — closed-union parser, mode mismatch, switch_requested
  rejection, legacy ACTIVE / SHADOW / BYPASS regression.

### Items NOT yet implemented (WP5a-2 scope)

- ``supervised_auto_step`` tick in ``DaemonSupervisor``.
- ``SwitchLeaseAuthority.has_active_lease`` helper.
- ``dispatch_executor`` autonomous path with
  ``expected_state=AUTO_GRACE``.
- ``routing_service._record_pending_shadow`` extension for
  ``mode == SUPERVISED_AUTO``.
- ``POST /v1/tasks/{id}/auto/{ack,veto,dispatch-now}``
  endpoints.
- ``mode`` change abort behaviour (the
  ``AUTO_ABORTED{reason="mode_changed"}`` audit + the
  clear-on-veto helper).
- Pending-shadow autonomous lifecycle (veto / finalise / abort).
- 24h unacked timeout for ``AUTO_GRACE``.

## M1 WP5a-2 — SUPERVISED_AUTO execution loop (`feat/m1-wp5a2-auto-tick`)

WP5a-1 landed the foundations; WP5a-2 lands the first real autonomous
side effect. Current status:

- **SUPERVISED_AUTO: IMPLEMENTED** — host-owned planning tick, frozen
  decision, grace / ack / veto, safe autonomous dispatch, verifier
  gatekeeping, pending-shadow lifecycle.
- **Production ACTIVE: UNCHANGED** — still
  ``DISABLED_BY_DESIGN`` unless activation authority authorises it;
  the two modes remain separate authorities.
- **Plugin autonomous authority: NO** —
  ``integrations/opencode/plugin.ts`` is byte-identical to WP5a-1;
  ``optionMode`` still rejects SUPERVISED_AUTO and the
  ``mode !== "ACTIVE"`` side-effect gate is untouched.
- **Auto dispatch authority: HOST / SAFETY KERNEL** — dispatch flows
  through ``initiate_owner_dispatch`` with the distinct
  ``authority="SUPERVISED_AUTO"`` and ``expected_state=AUTO_GRACE``;
  quota admission, worktree isolation, single-writer lock, main-repo
  immutability and the deterministic verifier all apply unchanged.
- **Verifier: AUTHORITATIVE** — worker exit ≠ VERIFIED; only a
  deterministic verifier PASS backed by immutable evidence may
  finalize a verified pending shadow.

### Tick (`supervised_auto_step.py`)

One bounded, deterministic, fake-clock-friendly sweep per invocation;
registered on ``DaemonSupervisor`` under ``supervised-auto``. The
``--control-only`` daemon **never registers it** (zero autonomous tick
execution; the product daemon is control-only today, so autonomous
execution is opt-in via the non-control-only runtime). Tick failures
are bounded: audited via ``SUPERVISED_AUTO_TICK_FAILED`` and the
supervisor's existing backoff — the daemon never crashes, and no task
is ever marked successful by a tick failure.

Six hard gates before any side effect (spec §3.4 step 1): admitted
top-1 recommendation; quota availability in
``{AVAILABLE_OBSERVED, AVAILABLE_UNMETERED}``; evidence freshness
(≤ 7 days); tier ≥ min_tier (via recommender admission); execution
target launchable (reusing ``validate_execution_target_launch`` as
the protected-surface gate — spec §3.4 item 5 clarification); no
active switch lease. Any gate failing ⇒ ``AUTO_SKIPPED{reason}``
(deduped per task+reason) and no side effect.

### Frozen decision contract

- ``auto_decision_id = f"auto-{task_id}-v{state_version-at-planning}"``
  — stable across ticks of one cycle; a veto/abort/timeout bumps the
  version, starting a fresh cycle.
- Routing ``request_id = f"supervised-auto-{auto_decision_id}"`` —
  no timestamps, no per-tick randomness; the durable
  ``routing_decisions.request_id`` UNIQUE constraint is unchanged and
  later ticks reuse the exact frozen decision (crash boundary A
  reconciles without a second decision).
- Dispatch ``request_id =
  f"supervised-auto-dispatch-{auto_decision_id}"`` — idempotent
  reservation; duplicates return the existing record, never a second
  worker.
- Pending shadow ``pending_id == auto_decision_id`` (裁决 15).

### Grace / ack / veto / dispatch-now

- ``unattended_allowed=True``: deadline set on entering AUTO_GRACE.
- ``unattended_allowed=False``: no countdown before the owner ack
  (``POST /v1/tasks/{id}/auto/ack``); the FIRST ack requires the exact
  ``task_state_version``; an already-acked retry is idempotent (200
  with the current task) and can never extend the deadline. 24h
  unacked ⇒ abort to READY
  (``AUTO_ABORTED{reason="unacked_timeout"}``).
- ``POST /v1/tasks/{id}/auto/veto`` and ``/v1/tasks/{id}/cancel`` on
  AUTO_*: back to READY, task policy locked MANUAL, pending shadow
  discarded, all four auto columns cleared, ``AUTO_VETOED`` audited
  (durable ``request_id`` idempotency).
- ``POST /v1/tasks/{id}/auto/dispatch-now``: skips only the remaining
  grace; every execution-admission gate still applies.

### Fail-closed aborts (§8/§25)

Mode change away from SUPERVISED_AUTO and project opt-out revoke
abort every AUTO_* lifecycle inside the same settings handler; the
tick's revocation sweeps are the crash backstop. The dispatch path
revalidates mode/project/version/lease immediately before reserving
(TOCTOU), and the executor's exact-``expected_state`` RUNNING guard
closes the reserve→start race: a concurrent veto turns the worker
start into a fail-closed ``TASK_NOT_READY`` with no process spawned.

### AUTO metadata invariants (§32)

Every terminal exit clears the four ``auto_*`` columns off the active
task row: veto, mode abort, project abort, unacked timeout,
pre-worker admission failure, and the executor close-out (verified /
failed / cancelled). The tick's terminal-state sweep is the durable
backstop when the executor dies mid-close-out. Historical truth lives
in the audit trail, the routing decision row and the shadow journal —
never in stale task-row fields.

### Pending-shadow lifecycle (§26)

Planning writes exactly one pending; repeated ticks never duplicate.
Verified runs finalize into exactly one real observation (deterministic
``observation_id`` makes double-finalization content-idempotent);
failed workers finalize with a truthful failed outcome; every abort
exit discards. Nothing hangs.

### Test coverage summary

- ``tests/test_supervised_auto_step.py`` (33 tests) — gates, frozen
  decision stability, request-id stability, unattended/attended grace,
  ack deadline pinning, dispatch exactly once, lease blocking (active
  vs expired), mode/project aborts, 24h timeout, crash boundaries A/C,
  restart round-trip, pre-worker BLOCKED reconciliation, terminal
  sweep, supervisor registration (control-only excluded).
- ``tests/test_mode_change_abort.py`` (7 tests) — §25 matrix incl.
  TOCTOU (deadline passed + mode flip ⇒ no dispatch), ACTIVE API
  rejection leaving AUTO intact, RUNNING immunity, executor-side
  expected-state guard.
- ``tests/test_auto_endpoints.py`` (18 tests) — ack/veto/dispatch-now
  success/stale/wrong-state/duplicate matrix, deadline pinning,
  cancel-as-veto, HTTP 400/404/409/200 over the real UDS surface.
- ``tests/test_pending_shadow_lifecycle.py`` (6 tests) — lifecycle
  disposition matrix; verified + failed finals run the REAL
  ``OwnerDispatchExecutor`` with the scripted worker + deterministic
  verifier.
- ``tests/test_switch_lease.py`` (+5 tests) — ``has_active_lease``
  truth table (matching unexpired / expired / wrong task / COMPLETED /
  ABORTED).
- ``tests/test_safety_kernel_transactions.py`` — the WP5a-1
  AUTO_GRACE→RUNNING prohibition test updated to the WP5a-2 contract
  (edge exists; unpaired bare transitions still fail
  ``assert_running_invariant``).

## M1 WP5a-2 crash-consistency closeout (`feat/m1-wp5a2-auto-tick`, follow-up commits)

Independent review blocked the merge: the AUTO lifecycle exits were
multi-stage writes, so a crash between durable commits could leave
``READY`` + active-looking auto metadata and an orphan pending shadow
(the terminal sweep never covered READY). Repaired on the same branch:

- **Atomic lifecycle close**: ``abort_auto_lifecycle`` performs the
  source-state gate (AUTO_PLANNED / AUTO_GRACE; BLOCKED only with
  ``allow_blocked`` AND a non-NULL ``auto_decision_id``), the exact
  version check, READY + four-column clear + optional MANUAL lock,
  exactly ONE ``state_version`` bump, one audit event and one cleanup
  intent — all in ONE ``BEGIN IMMEDIATE``. Abort, veto (incl.
  cancel-as-veto), unacked timeout and the pre-worker BLOCKED
  reconciliation all route through it.
- **Durable shadow-cleanup outbox** (``auto_shadow_cleanup_outbox``):
  the pending discard is promised in the abort transaction and
  fulfilled after COMMIT by an idempotent, bounded drain — crash-safe
  at every interleaving; a journal failure never rolls back SQLite
  truth. ``clear_auto_state_metadata`` enqueues the same intent so the
  terminal sweep / executor close-out inherit the guarantee.
- **Tick ordering**: outbox drain + stale-READY-metadata recovery run
  BEFORE revocation sweeps, planning and dispatch (old-lifecycle
  cleanup precedes new planning), with a second drain at tick end.
- **READY stale-metadata recovery**: a READY row with lifecycle
  metadata (decision id / deadline / ack) is recovered atomically with
  ``AUTO_METADATA_RECOVERED{ready_state_stale_auto_metadata}``;
  ``auto_reason`` alone is the tick's legal skip hint and a READY row
  with only a frozen routing decision is the legal crash boundary A —
  neither is mistaken for staleness.
- **ACK contract frozen**: first ACK requires the exact version; an
  already-acked retry is idempotent and never extends the deadline.
  VETO is exact before first mutation (same-``request_id`` replay
  idempotent); DISPATCH-NOW is an exact admission guard.
- Tests: ``tests/test_auto_crash_recovery.py`` (15) — deterministic
  fault injection (failing journal, store-level commits without the
  drain, fresh-connection restarts; no sleep) covering the veto /
  mode-change / project-disable / unacked-timeout / pre-worker-BLOCKED
  crash windows, the legacy READY stale-metadata row, the frozen
  boundary-A negative, the outbox mechanics (§20 matrix) and the
  one-bump / one-audit / plain-owner-BLOCKED-untouched invariants.

### WP5a-2 current-cycle reconciliation repair (review round 2)

Reconciliation of terminal SUPERVISED_AUTO dispatch rows is now
strictly current-cycle: the row must match
`supervised_auto_dispatch_request_id(task.auto_decision_id)` (gate
before any mutation), and "this dispatch has a run" is decided by the
exact `run-{dispatch_id}` row — never by a task-scoped historical run
lookup. Historical blocked rows cannot abort a new cycle
(`tests/test_auto_reconciliation.py`: old run cannot hide a current
pre-worker failure; old blocked dispatch cannot abort a new cycle;
current dispatch with its exact run is never pre-worker aborted;
run correlation is dispatch-scoped — only `run-X` counts for
dispatch X; a no-metadata historical row fails closed, BLOCKED stays
BLOCKED).

### WP5a-2 post-worker shadow finalization repair (review round 3)

The terminal VERIFYING → VERIFIED/BLOCKED commit previously preceded
the filesystem `finalize_pending` — a crash in between let the restart
terminal sweep DISCARD the pending of a really-executed worker
(permanent observation loss). Now: durable
`auto_shadow_finalize_outbox` intents with immutable payloads
(observed_at pinned for byte-identical replays), the terminal
transition + intent in ONE SQLite transaction
(`apply_verification_outcome`), an idempotent bounded drain
(missing pendings complete only with a proven matching observation,
else fail closed with a sanitized system event), finalize-beats-discard
precedence in both the enqueue rules and the drains, exact-run
terminal-sweep classification with fail-closed reconstruction, and the
`shadow_journal=None` cleanup-drain bug fixed (intents stay OPEN).
Deterministic crash tests: tests/test_auto_shadow_finalize_recovery.py
(15) — verified/failed-verdict/non-zero-exit recovery, byte-identical
replays, payload-conflict fail-closed, pre-worker discard boundary,
outbox precedence and the None-journal contract.

### WP5a-2 final outcome ordering repair (review round 4)

Shadow finalization previously froze the intermediate verifier
outcome — a later main-repo check downgraded VERIFIED → BLOCKED and
left a stale verified=True observation; and a real-run lifecycle with
a missing pending could clear `auto_decision_id` before any durable
finalize intent / proven observation existed. Now: the main-repo
immutability result is composed with the verifier verdict BEFORE one
final terminal transaction (verifier PASS + mutation ⇒ BLOCKED +
verified=False/verification_success=True shadow), a central
real-execution recovery guard gates every metadata clear
(intent-or-proven-observation), and the finalize outbox freezes the
pending's full immutable identity so completion proof rebuilds and
exactly compares the expected observation. Deterministic tests:
tests/test_auto_final_outcome.py (9) — real-worker main-mutation
ordering, happy path, missing-pending preservation (executor + sweep),
intent-authorized clear with outbox-alone restart proof, and full
semantic proof (divergent taxonomy/timestamp/quota/burn rejected).

### WP5a-2 namespace + shadow identity repair (review round 5)

Three review findings closed: (1) OWNER dispatch request ids could
occupy the deterministic SUPERVISED_AUTO dispatch namespace and AUTO
crash recovery trusted any RESERVED row on that id — owner input in the
namespace is now rejected before reservation (400
reserved_dispatch_request_id_namespace), and recovery re-admission
requires an exact authority/task/version/target/dispatch identity match
(the one shared `owner_dispatch_matches_expected` rule); a foreign row
closes the current lifecycle fail-closed (dispatch_namespace_conflict)
and is never executed or mutated. (2) A present pending shadow is no
longer trusted by file id alone — the finalize drain proves it equals
the frozen `identity_json` before finalizing, and completion requires
the exact expected observation to be durably present (a successful
`finalize_pending` return is not proof). (3) Observation-only recovery
proof now correlates on the real durable `route-*`
`RoutingDecision.decision_id` with exactly-one + terminal
verdict-consistency requirements. Deterministic tests:
tests/test_auto_namespace_and_identity.py (18) — namespace rejection
+ normal owner contract, foreign-row non-execution across every tuple
dimension, exact-row crash recovery, present-pending identity matrix
(target/catalog/quota/provider mismatch, silent-finalize fault
injection), and observation-only proof (real route id proves;
auto id or verdict mismatch does not).
