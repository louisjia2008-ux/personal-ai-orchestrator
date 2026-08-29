# Integration Status

Status: `INTEGRATION_IMPLEMENTED / LIVE_ACCEPTANCE_PENDING`

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
version. An ACTIVE decision that requests a switch now carries that same version as part of its
immutable contract.

## ACTIVE switch authorization boundary

The prior distributed TOCTOU boundary has been tightened at the durable task-state layer.
A routing decision alone is not authority to mutate the OpenCode session. Immediately before the
side effect, the adapter must request a short-lived switch lease from the orchestrator.

The switch authority:

- reloads the persisted routing decision;
- requires ACTIVE mode and `switch_requested=true`;
- binds the lease to the exact decision ID, request ID, task ID, task state version and session ID;
- revalidates the current durable task is still READY/RUNNING at the exact decision version;
- allows at most one live switch lease for a task;
- installs a SQLite trigger that rejects task state/version transitions while a non-expired lease
  is authorized;
- resolves the lease as COMPLETED or ABORTED after the local switch attempt;
- bounds a crashed adapter's state freeze with a 10-second default lease and 30-second hard maximum.

This closes the scheduler-to-switch state race for authoritative task transitions inside the
Safety Kernel. Target-Mac acceptance is still required to validate real OpenCode session-switch
behavior and timing before production ACTIVE is enabled.

## P0 Safety Kernel foundation

Implemented:

- SQLite state store; file-backed databases use WAL;
- typed task state machine;
- idempotent task submission;
- run/workspace/approval/audit/routing-decision tables;
- host-owned Git worktree creation;
- one writer token per task worktree;
- DB-level unique constraint allowing at most one RUNNING worker run per task;
- worker-run admission atomically verifies RUNNING task state, exact writer-token ownership and
  absence of another active worker run before persisting the run;
- worker exit atomically updates the run row, task state/version and both audit events, preventing
  a crash from leaving a FINISHED run paired with a still-RUNNING task;
- task submission, workspace registration, run persistence, routing-decision persistence and
  startup reconciliation couple authoritative mutation and audit writes in one SQLite transaction;
- worker-exit handling verifies that `run_id` belongs to the supplied `task_id` before mutating
  either run or task state;
- restart recovery can re-adopt a persisted managed worktree only after verifying managed-root
  path identity, Git registration, expected branch, and recorded-base ancestry;
- exact process-group supervision and cancellation;
- host cancellation requires the persisted run PID to match the exact process owned by
  `ProcessSupervisor`, aborts any live switch lease, then atomically marks the run CANCELLED,
  transitions the task to CANCELLED, releases writer ownership and writes audit evidence;
- PID mismatch fails before signalling, so a different process cannot be killed accidentally;
- daemon startup performs fail-closed reconciliation before exposing routing;
- uncertain RUNNING/WORKER_FINISHED/VERIFYING tasks are atomically blocked and active runs marked
  INTERRUPTED during startup reconciliation;
- startup workspace reconciliation clears stale writer ownership after blocking and blocks any
  otherwise-routable task whose registered worktree disappeared;
- worker crash / malformed worker-result handling;
- no worker transition directly to VERIFIED/COMPLETED.

Injected audit-failure tests prove rollback for worker start, worker exit, routing-decision writes
and startup reconciliation. These tests verify that the authoritative state mutation does not
survive when its corresponding audit write fails.

The integration line does not claim target-Mac acceptance merely from Linux CI. macOS process,
filesystem, credential and restart acceptance must still be exercised on the intended host before
P0 is declared production-authoritative.

## P1 deterministic verification foundation

Implemented:

- host-defined argv verifier profiles;
- no prompt-defined shell command contract;
- changed-file scope gate;
- `git diff --check`;
- deterministic command stages and bounded output;
- immutable verification evidence identity and append-only evidence journal;
- VERIFIED requires the exact passing `VerificationResult` to already exist in that journal;
- a forged evidence ID or mismatched persisted evidence fails closed to BLOCKED;
- passing text without host evidence cannot advance to VERIFIED;
- verifier failure or missing evidence blocks the task.

## P2.5 scheduler corrections

Implemented:

- separate logical `ModelSKU` and concrete `ExecutionTarget`;
- fail-closed simultaneous quota-window semantics;
- absolute usable-headroom admission before ranking;
- high-risk reliability floor;
- task context-window requirement;
- vision/tool runtime requirements;
- failure-count escalation floor;
- deterministic scheduler-to-adapter decision bridge;
- content-addressed policy snapshots plus quota snapshot references;
- durable retry semantics return the original routing decision instead of recomputing against a
  later timestamp/quota view;
- duplicate request races converge on the first durable decision;
- request-ID reuse for a different task or routing mode fails closed.

## P3 provider audit

Static audit coverage includes:

- MiniMax Token Plan;
- Z.AI Coding Plan;
- OpenAI API and Codex/ChatGPT plan allowance;
- Anthropic API and Claude subscription allowance;
- DeepSeek PAYG balance;
- local/unmetered runtimes.

`UNKNOWN` is intentional when a stable supported machine-readable remaining subscription quota
surface is not documented. Historical API usage/cost is not relabelled as remaining plan quota.

Live blockers remain:

- MiniMax quota probe requires an approved supported credential handoff on the local machine;
- Z.AI live probe/retest remains pending reset/auth availability;
- no credential file may be read/copied merely to satisfy acceptance evidence.

## P3.5 Shadow evidence

Implemented:

- append-only `ShadowObservation` journal;
- stable observation identity;
- manual target vs scheduler target;
- immutable catalog/policy/quota references;
- before/after quota snapshot references;
- predicted/observed burn fields;
- verified outcome, regression, attempts/time-to-green, handoff count;
- reset-cycle accounting;
- conservative `review_eligible` summary.

`review_eligible` is **not** ACTIVE authorization. `ActiveRoutingGate` additionally requires:

1. P0 authority accepted;
2. P1 authority accepted;
3. fail-closed adapter validation;
4. Shadow evidence accepted;
5. safe BYPASS validation;
6. explicit owner approval.

## Current CI evidence

The latest code-bearing integration head verified on GitHub Actions is
`96ee1fcb6c6891909ec873f91dd43fee789747d9`:

- Ruff: PASS;
- pytest: **136 passed**;
- `git diff --check`: PASS;
- OpenCode adapter typecheck + contract tests: PASS.

Subsequent commits in this section are documentation-only and must still pass the normal PR CI
before being treated as the final green PR head. These are Linux CI results and do not replace
target-Mac acceptance.

## External acceptance still required

The following cannot be honestly completed from a GitHub-hosted runner:

- provider-native MiniMax/Z.AI authentication evidence using credentials stored only on the user's
  target Mac/OpenCode environment;
- macOS-specific process/Keychain/runtime and switch-lease timing acceptance;
- real Shadow evidence across multiple future quota reset cycles;
- owner acceptance for production ACTIVE routing.

Until those gates are satisfied, production ACTIVE routing remains disabled by design.
