# Integration Status

Status: `INTEGRATION_IMPLEMENTED / LOCAL_MAC_ACCEPTED / P3_8_FIRST_REAL_SHADOW_OBSERVATION_ACCEPTED`

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

## Current CI evidence

Latest pre-P3.8 integration head verified on GitHub Actions:
`b7671c5d16391c38195f044866d909c6762ad864`.

Current local P3.8 validation on 2026-08-30:

- Ruff: PASS;
- pytest: **158 passed**;
- `git diff --check`: PASS;
- OpenCode adapter typecheck + contract tests: PASS, 10/10;
- P3.8 real Shadow execution script: PASS, observation `shadow-79b324d8b775bb0d2e80e221`;
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
