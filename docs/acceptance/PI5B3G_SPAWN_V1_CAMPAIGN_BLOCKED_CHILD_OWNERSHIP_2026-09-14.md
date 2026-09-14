# PI-5B3G Campaign D blocked at child ownership registration

## Status

`BLOCKED_PI_5B3G_CHILD_WORKER_OWNERSHIP_REGISTRATION_FAILED`

Campaign D `delegation-campaign-c129634a908548c497b7c5477851d764` stopped after its first authorized observation. No retry occurred and Observations 2 and 3 were not started.

## What passed

- The exact pushed source head `19adaa95b698ae8d6e9f80abf9ec8fd5eb139b67` had green Python, macOS Swift, and OpenCode CI before model use.
- Live preflight passed for the repaired product, canonical database, fixture, verifier, validator, identity scheme, target/auth, exact quota, and shared-pool gate.
- The normal product released canonical ownership through the proven graceful SIGTERM route.
- The campaign-scoped parent request was admitted without collision.
- The parent process was created, durably registered, marked running, completed its Pi JSON protocol with exit code 0, and invoked `pao_delegate` exactly once.
- `pi5b3g-child-scope-v2` returned `ALLOW_EXACT_SCOPE`; the broker forwarded exactly one child.
- The accepted parent-spawn repair therefore passed its live parent-worker gate.

## First blocking invariant

The child process was created as PID 99613 and its run was durably registered. During the next ownership-registration step, the external Campaign D observer raised `AttributeError`. Canonical `pao-spawn-diagnostics-v1` recorded:

- stage `DURABLE_RUN_REGISTERED`
- child created and PID observed
- executable and cwd valid
- stdout/stderr pipes and new session created
- durable run created
- protocol bootstrap not started
- no safe errno

The executor's emergency repair terminated and reaped the exact unowned child with signal 9, failed the child run, released its writer, and returned only `{child_state: BLOCKED, verified: false}` to the parent. The parent correctly did not create its artifact and became terminal `BLOCKED`.

Source inspection proves the harness defect: its child-only activation observer reads `task.delegated_parent_task_id`, but repository `TaskRecord` has no such attribute. Delegated lineage is retained in the child `TASK_SUBMITTED` audit payload. The parent path never executes this child-only access, which explains why the repaired parent spawn passed while child ownership registration failed.

This was a campaign-harness instrumentation defect, not a scope-validator rejection, request-ID collision, parent-spawn recurrence, verifier failure, or provider failure. It must not be repaired and retried inside this consumed campaign.

## Accounting and cleanup

- authorized observations: 3
- admitted observations: 1
- completed observations: 0
- parent workers started: 1
- child workers started: 1
- total workers started: 2
- retries: 0
- fallback: no
- grandchildren: 0
- campaign state: `STOPPED`
- campaign processes and orphans: 0
- SQLite `quick_check`: `ok`
- `RUNNING_ROWS / ACTIVE_RUNS / HELD_WRITERS`: `0 / 0 / 0`
- fixture: clean at `d764edb4ca65c8ce5f77fcb2964d320ac3a503b9`

The normal repaired product was restored with one GUI, one PyInstaller parent, one serving child, one canonical socket owner, health `ok`, advancing heartbeat, expected build/helper identity, valid code signature, healthy SQLite, and zero campaign process.

Machine-readable sanitized evidence is in [campaign-blocked-child-ownership.json](pi5b3g-spawn-v1-2026-09-14/campaign-blocked-child-ownership.json). It contains no raw provider transcript, raw dynamic intent, environment values, or credential values.
