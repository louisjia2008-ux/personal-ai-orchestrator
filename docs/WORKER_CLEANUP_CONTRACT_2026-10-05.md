# Worker cleanup and quarantine contract (#71)

## Scope and baseline

This local change implements approved design slices A/B/C for
[issue #71](https://github.com/louisjia2008-ux/personal-ai-orchestrator/issues/71).
Its exact remote baseline is draft PR #69 commit
`0642d6d4106b78a90137c73629d24fddc7348cd8`, source tree
`c96b706a557bc525e7bfbce900aaa183246e71c4`. The issue-first requirement was
checked before implementation. The design and final patch receive separate
independent review.

The patch does not add automatic #70 recovery/retry, an owner-unquarantine
operation, provider calls, activation authority, changes to Production ACTIVE,
credential handling, publication, merge, or deployment. A request still denotes
its original operation. A cleanup receipt does not grant another execution or
prove that the earlier worker had no external effects.

## Durable boundary

Before an adapter can create a child, the host claims one immutable attempt for
its dispatch. The attempt freezes the task, current source-state version,
reservation version, authority, executor identity, writer token and workspace.
An unresolved claim quarantines the workspace immediately.

A one-shot spawn permit and creation claim persist before the OS boundary.
Immediately around synchronous `Popen`, a SQLite write transaction revalidates
the unfenced attempt and records the exact created child. There is no `await`
inside that transaction. Startup fencing therefore cannot commit between the
last authority check and process creation. A rollback after OS entry leaves the
previously committed uncertain claim, rather than creating proof of no launch.

The mutable spawn ticket is shared across adapter tasks. It is fenced before
cancellation and retains creation/cleanup ownership through late callbacks.
Only a closed, fenced creation operation positively known to have produced no
child may record `SPAWN_FAILED_NO_CHILD`; a pending empty ticket is UNKNOWN.

Cleanup receipts are immutable and attempt-scoped. A started CONFIRMED receipt
requires observed leader exit, exact-child reap, observed absence of the created
process group, and both streams' EOF with completed collectors and no reader
error or forced close. Every identity and fact is checked in the storage API.
A later receipt cannot substitute for a newer writer or attempt.

All writer acquisition/release, direct workspace mutation, execution success,
startup and workspace reconciliation honor quarantine. Startup fences prior
attempts and creates UNKNOWN tombstones for legacy reservations, runs, or
retained writers lacking matching evidence, including terminal tasks. Terminal
state alone never proves that a workspace is safe to reuse.

## Process identity and proof scope

The supervisor is the sole reaper of each `Popen` child. It retains the child
object and owns one observe/signal/reap lock. No independent asyncio child
watcher or `Popen.poll`/`send_signal` path can silently reap the identity pin.
The host must retain the default SIGCHLD disposition and avoid external reapers.
Ignored/custom handlers are rejected before spawn; a later disposition change
revokes signal authority and yields UNKNOWN without resetting the host handler.

Where available, `waitid(WNOWAIT)` observes an exited leader without reaping it.
The unreaped child, including a zombie, pins its PID and the created group ID
while TERM/KILL decisions are made. On the `waitpid(WNOHANG)` fallback, observing
and reaping exit atomically removes all further group-signalling authority.
Live-child signalling remains possible; leader-first descendants may instead
require UNKNOWN quarantine. Python 3.12 on macOS uses this conservative fallback.
Linux fallback tests are not native macOS acceptance.

After the final reap, `killpg(pgid, 0)` is observation only. ESRCH supports scope
absence; an extant group, EPERM, identity loss, or another error does not. No
nonzero signal is sent to a reaped identity, by process name, or to a guessed PID.

Proof scope is **CREATED_PROCESS_GROUP**. Descendants that deliberately escape
that session/group are outside this proof. This is not a general OS containment
mechanism and does not claim to enumerate or eliminate all descendant trees.

## One bounded operation

OpenCode, Pi and their explicit execution probes use the same collection and
cleanup operation. Output storage is capped, but excess bytes continue to drain
through termination. The adapters retain their separate protocol and output
limits. Reader errors, forced close, incomplete EOF, truncated output, timeout,
and cleanup uncertainty cannot authorize verification.

Default cleanup budget is 10 seconds, with TERM grace at most 5 seconds and a
final persistence reserve of at most 1 second (10% for smaller test budgets).
Normal leader exit gets at most 2 seconds for EOF, bounded by the original worker
deadline. Repeated cancellation joins the same strongly retained cleanup task
and original monotonic deadline; it cannot create a fresh budget. SQLite cleanup
writes and cleanup-only connection creation do not wait on database locks.
Contention leaves the durable quarantine intact instead of extending teardown
through multiple busy timeouts.

The deadline is a cooperative userspace bound. It cannot promise a hard wall
clock limit if the event loop, kernel process creation, filesystem or storage
itself stops responding. Sending KILL is evidence of a signal, not evidence of
exit. Forced pipe close is never recorded as observed EOF.

Timeout remains host failure 124 even if a TERM handler exits zero. Once owner
cancellation wins, failed cancellation finalization cannot fall through to a
successful worker result. Exact run/writer/PID comparisons protect a newer
attempt from stale emergency repair; registry removal also compares object
identity.

## Operational outcome

- CONFIRMED: the matching writer may be released after durable receipt commit;
  normal verification still requires its existing deterministic host evidence
- UNKNOWN or receipt-write failure: expose recovery-required state, retain
  attempt/ownership evidence, and prevent writer release/acquisition/replay
- Restart: fence uncertain historical execution and preserve quarantine;
  no automatic launch or invented success

No public quarantine-clear command is added. Manual recovery policy and any
new explicit retry authorization remain separate #70 work.

## Verification record

Exact commands, test counts, baseline failure comparison, independent review,
source hashes and limitations are supplied in the accompanying validation
report. Local offline process/pipe fixtures do not establish target-Mac,
provider runtime, signing, deployment, or release readiness. The baseline's
existing successful CI run is evidence for the baseline only, not this patch.
