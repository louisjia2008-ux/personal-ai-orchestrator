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
  -> immutable RoutingDecision refs
  -> durable SafetyKernelStore decision ledger
  -> SHADOW record-only
  -> ACTIVE only if every activation gate is satisfied
```

## P0 Safety Kernel foundation

Implemented:

- SQLite state store; file-backed databases use WAL;
- typed task state machine;
- idempotent task submission;
- run/workspace/approval/audit/routing-decision tables;
- host-owned Git worktree creation;
- one writer token per task worktree;
- exact process-group supervision and cancellation;
- startup reconciliation that blocks uncertain in-flight state;
- missing-worktree reconciliation;
- stale writer-lock release only after the task is blocked/terminal;
- worker crash / malformed worker-result handling;
- no worker transition directly to VERIFIED/COMPLETED.

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
- immutable verification evidence identity;
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
- content-addressed policy snapshots plus quota snapshot references.

## P3 provider audit

Static audit coverage now includes every initially listed provider class:

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

## External acceptance still required

The following cannot be honestly completed from a GitHub-hosted runner:

- provider-native MiniMax/Z.AI authentication evidence using credentials stored only on the user's
  target Mac/OpenCode environment;
- macOS-specific process/Keychain/runtime acceptance;
- real Shadow evidence across multiple future quota reset cycles;
- owner acceptance for production ACTIVE routing.

Until those gates are satisfied, production ACTIVE routing remains disabled by design.
