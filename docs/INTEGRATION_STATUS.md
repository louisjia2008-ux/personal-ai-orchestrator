# Integration Status

Status: `INTEGRATION_IMPLEMENTED / LOCAL_MAC_ACCEPTED / LIVE_ACCEPTANCE_PENDING`

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

## Current CI evidence

Latest code-bearing integration head verified on GitHub Actions:
`96ee1fcb6c6891909ec873f91dd43fee789747d9`.

- Ruff: PASS;
- pytest: **136 passed**;
- `git diff --check`: PASS;
- OpenCode adapter typecheck + contract tests: PASS.

Subsequent commits only update this status document; the normal PR CI remains the authority for the
final branch head. These are Linux CI results and do not replace target-Mac acceptance.

## External acceptance still required

- provider-native MiniMax/Z.AI authentication evidence on the intended local environment;
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
