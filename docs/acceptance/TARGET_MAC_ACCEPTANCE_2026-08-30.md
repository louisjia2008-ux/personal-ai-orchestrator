# Target Mac Acceptance Record - 2026-08-30

Status: `LOCAL_P0_P1_ROUTING_ACCEPTED / LIVE_PROVIDER_AND_LONGITUDINAL_SHADOW_PENDING`

Candidate head:

```text
Run from the committed integration branch; use the generated report's `head` field as the exact SHA.
```

Evidence command:

```text
.venv/bin/python scripts/target_mac_acceptance.py
```

Sanitized local evidence:

```text
Generated under /private/tmp/pao-target-mac-acceptance-*/target-mac-acceptance-report.json.
```

Target environment:

```text
macOS: ProductName: macOS; ProductVersion: 26.5.1; BuildVersion: 25F80
Python: 3.14.6
Node: v22.22.0
OpenCode: 1.18.23
```

## Local Runtime Acceptance

P0 Safety Kernel: `PASS`

- SQLite file-backed state uses WAL.
- Task submission is idempotent.
- Host-owned disposable Git worktrees preserve source checkout HEAD.
- Single-writer lock rejects a second writer.
- Exact supervised process cancellation terminates the owned process group and preserves an
  unrelated process created by the harness.
- Worker non-zero exit blocks the task.
- Malformed worker result blocks the task.
- Missing worktree reconciliation blocks the task and releases stale writer ownership.
- Startup reconciliation blocks uncertain RUNNING state and marks the active run INTERRUPTED.

P1 deterministic verifier: `PASS`

- A genuine pass reaches VERIFIED only after exact persisted immutable evidence exists.
- Failing targeted test blocks VERIFIED.
- `git diff --check` failure blocks VERIFIED.
- Changed file outside the allowlist blocks VERIFIED.
- Verifier command failure blocks VERIFIED.
- Missing verifier evidence blocks VERIFIED.
- Malformed persisted verifier evidence blocks VERIFIED.
- Duplicate evidence identity mutation is rejected.
- Engineering failures are not retried unless host-attested as known flaky infrastructure.
- Restart between WORKER_FINISHED/VERIFYING and VERIFIED blocks uncertain state.

OpenCode/routing loopback path: `PASS`

- SHADOW route returns and persists an immutable decision with catalog/policy/quota refs.
- SHADOW adapter outcome is RECORD_ONLY and does not request a model switch.
- Production ACTIVE remains denied without the complete activation gate.
- Non-JSON, origin-spoof, and oversized requests are rejected with sanitized errors.
- Non-loopback bind is rejected.
- Daemon-unavailable, malformed-decision, and BYPASS adapter outcomes keep the current model.
- Active gate requires all prerequisites; removing a prerequisite denies authorization.

## Not Executed / Still Pending

MiniMax live provider evidence: `NOT_EXECUTED_SUPPORTED_AUTH_SURFACE_REQUIRED`

Z.AI / GLM live provider evidence: `NOT_EXECUTED_SUPPORTED_AUTH_SURFACE_REQUIRED`

OpenAI / Codex subscription quota: `UNKNOWN_NO_SUPPORTED_MACHINE_READABLE_SUBSCRIPTION_QUOTA`

Anthropic / Claude subscription quota: `UNKNOWN_NO_SUPPORTED_MACHINE_READABLE_SUBSCRIPTION_QUOTA`

DeepSeek: `UNKNOWN_PAYG_BALANCE_NOT_SUBSCRIPTION_QUOTA`

Local runtimes: `NOT_EXECUTED_CAPACITY_IS_NOT_PERCENTAGE_QUOTA`

Shadow evidence across multiple real quota reset cycles: `NOT_EXECUTED`

Production ACTIVE owner approval: `NOT_CREATED`

Final status:

```text
PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED
```
