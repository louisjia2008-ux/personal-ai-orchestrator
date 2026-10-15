# Owner dispatch restart and explicit recovery contract (#70)

## Reviewed scope

This bounded local implementation addresses issue
[#70](https://github.com/louisjia2008-ux/personal-ai-orchestrator/issues/70) on
baseline `919e219`. Its recovery design received independent review before the
state-reset implementation. It builds on the immutable attempt, spawn fence,
cleanup receipt and quarantine boundary documented in
[the #71 cleanup contract](WORKER_CLEANUP_CONTRACT_2026-10-05.md).

Recovery does not launch a worker, retry an old dispatch, release a writer,
clear quarantine, change scheduling policy, or change Production ACTIVE.
An interrupted worker may already have made external changes. Confirmed cleanup
proves only the existing #71 cleanup scope, not absence of earlier effects.

## Atomic startup closeout

The existing startup transaction first fences prior attempts, preserving legacy
UNKNOWN tombstones. It blocks in-flight task states and interrupts RUNNING runs.
It now also closes remaining OWNER_INITIATED_EXECUTION RESERVED/STARTED rows as
BLOCKED with fixed, transcript-free `STARTUP_RECOVERY_REQUIRED` diagnostics.
Other dispatch authorities keep their existing lifecycle owners.

Each immutable interruption row records the dispatch/task identity, original
state/version, matching attempt/run when positively correlated, original
workspace path, and resulting BLOCKED version when the task generation matches.
Missing or mismatched evidence is recorded without a recovery-capable generation
binding. Run IDs are never guessed from a task's latest run.

A pre-execution task is blocked only at the exact reservation/attempt source
version. The ordinary SUBMITTED-to-READY admission is recognized only with one
reservation at that version and the exact host admission transition audit after
its matching reservation audit. `version + 1` alone is insufficient. A started
attempt binds RUNNING, WORKER_FINISHED and VERIFYING through its exact run/PID,
source version and the precise legal transition offset. Stale reservations do
not mutate a newer READY generation or terminal task.

Repeating startup cannot append duplicate interruption records, repeat task
transitions, replace final dispatch results, or change cleanup facts. A failure
in the final audit rolls back task/run/dispatch closeout and fences together.
The startup operation is not invoked merely by opening another store connection.

## Explicit recovery authorization and evidence

The host's explicit RECOVER operation supplies:

- A new recovery request ID
- The task ID and expected current BLOCKED version
- The exact prior dispatch ID
- The expected store ID and current process epoch

The API must authenticate current owner-execution authority and current process
epoch, and reject a currently supervised execution. Store admission is atomic
and requires all of the following:

1. The prior dispatch is the exact startup-interrupted owner dispatch and its
   immutable interruption binds the current BLOCKED task version.
2. The task remains explicitly MANUAL and its bound target is unchanged from
   that dispatch. Recovery cannot make a task eligible for an automatic scan.
3. The old attempt is exact, fenced and non-legacy. Its workspace still matches
   both the interruption record and attempt. Its linked run, if any, matches.
4. Every attempt for the task or workspace path has an immutable, same-executor
   CONFIRMED cleanup receipt satisfying the same #71 identity and evidence
   validation used at receipt creation. A cached cleanup_state is insufficient.
5. No writer is retained, no run or dispatch is active, and no ACTIVE model
   switch lease freezes the task. Existing cleanup/workspace reconciliation must
   have independently released the old writer; RECOVER has no release rights.

Only then does one transaction move BLOCKED to READY and save the immutable
recovery receipt and audit event. It retains MANUAL policy and the selected target.
No executor is invoked. The owner must use a new START request to launch again;
normal current admission, authorization, quota and target gates run again.

### Cases that remain blocked

A RESERVED dispatch without an attempt is not proof that no child was created.
Existing reservations do not identify which host launch contract produced them,
so startup retains #71's legacy UNKNOWN tombstone. Recovery cannot clear it.
Likewise, missing cleanup, lost cleanup persistence, unresolved claims, changed
workspaces, residual writers, mismatched generations, changed/absent targets,
non-MANUAL policies and terminal tasks are not recoverable by this operation.
An already-BLOCKED historical task without a positively correlated interruption
ledger remains blocked; this change does not reconstruct older generations from
legacy startup audits.
No process-name/PID search, signal, cleanup retry or broad unquarantine command
is introduced.

## Idempotency and lost responses

A recovery request ID freezes the complete request tuple, including store and
process epoch. Exact replay returns the historical receipt before considering
the task's later state, without another transition. A conflicting tuple fails;
a second request ID cannot recover the same prior dispatch again. SQLite write
transactions serialize concurrent recovery and cancellation.

The receipt contains the original BLOCKED version and the recovered READY
version. It is historical evidence, not the current task. Read-only
RECOVERY_STATUS can resolve a lost response with the original request tuple
after a restart; its response must show the current task separately. A new
process epoch does not permit changing the old receipt's request tuple.
Same-request old START and runtime executor re-entry continue to return/observe
the original BLOCKED dispatch, even after successful recovery.

## Verification scope

Offline tests cover reservation, claim, spawn entry, observed child,
registration, worker completion and verification crash boundaries; repeated
startup; late receipts and lost cleanup; exact generation and workspace/target
binding; missing or damaged proof; request replay and lost replies; concurrent
recover/recover and cancel/recover; MANUAL exclusion from supervised-auto; and
fresh START using a distinct attempt after recovery.

The broader regression run must report socket restrictions separately from
assertion failures. Local fake-worker/SQLite tests do not establish native Mac,
provider runtime, paid acceptance, signing, deployment or release readiness.
