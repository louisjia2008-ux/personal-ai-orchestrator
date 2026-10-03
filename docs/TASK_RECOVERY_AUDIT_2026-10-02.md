# Task failure and recovery audit — 2026-10-02

## Baseline and scope

The remote baseline was checked before work: `main` was
`32dd7a3fd3cfe21ed924ec1b5ffb274833d81a3f`; draft PR #69 was still open at
`6f0b67eabd0d5c6b5baab60c880705a8e07b028f`. This focused patch is based on PR #69
and preserves its earlier routing, settings, quota, and Dashboard repairs.

Only isolated repositories, temporary SQLite stores, fake workers, mocked
transport errors, and event barriers were used. There were no live provider
calls, paid calls, live task dispatches, target-Mac acceptance, credential or
security changes, publication, merging, or deployment.

## Repaired behavior

1. **Malformed Pi output fails closed.** JSON decoder limits can raise plain
   `ValueError` for oversized integers or `RecursionError` for excessive
   nesting. The parser now treats both as invalid JSONL instead of leaking them
   into task-lifecycle exception handling. End-to-end fake Pi tests assert a
   failed run, blocked task, released writer, and no second run on re-entry.
2. **Unexpected `ValueError` cannot abandon a RUNNING task.** The executor now
   checks durable task state before treating the exception as concurrent
   finalization. A still-RUNNING task uses the existing emergency repair and
   exact-child reap path. Tests also retain coverage for `RuntimeError` and
   `ConnectionError`, with no orphan run, process, or active registry entry.
3. **Timeout cannot become success through exit zero.** A worker that handles
   termination gracefully may report OS exit code zero after the host deadline.
   The executor now supplies host timeout status `124` to the existing failed
   worker path and success-evidence calculation. A `WORKER_TIMED_OUT` audit
   retains the actual OS exit code. The regression proves no verification
   transition occurs even though the worker wrote verifier-valid output.
4. **Requested cancellation coordinates graceful exit zero.** The worker-exit
   path waits for an in-flight cancellation transaction regardless of the OS
   exit code. An event-barrier test pauses cancellation after reap and proves
   the task, run, and dispatch all become CANCELLED, with no writer retained.

No durable schema, Safety Kernel transition rule, retry authority, automatic
restart dispatch, process-group signalling policy, or provider connection
policy was changed. Transport-disconnect coverage is an injected
`ConnectionError`; it does not establish real provider disconnect acceptance.

## Verification

- New regression file: `tests/test_worker_failure_recovery.py`, **9 passed**
- The same final regression file against baseline source: **7 failed, 2 passed**
- Full baseline Python run: **1268 passed, 5 skipped, 21 failed, 56 errors**
- Full patched Python run: **1277 passed, 5 skipped, 21 failed, 56 errors**
- Exact failed/error test IDs were compared: the same **77** baseline-blocked
  cases fail under the Linux environment's denied Unix-domain socket creation
- Excluding only those 77 baseline-blocked IDs: **1277 passed, 5 skipped,
  77 deselected**
- Ruff lint, Ruff format check, and `git diff --check`: passed
- DSH dispatch plugin: **9 passed**; Pi-DSH dispatch plugin: **7 passed**
- Independent focused runtime review: **81 passed**, with its sole failure the
  same pre-existing HTTP cancellation test blocked by Unix-domain sockets

These are local, offline results. CI has not run for this unpublished patch.
The previous PR #69 CI run does not validate these new changes. Native Swift
and target-Mac UI checks were not run; this patch changes no native client or
shared client contract. OpenCode adapter sources and dependencies are unchanged.

For the red replay, run the new test file from this worktree with `PYTHONPATH`
pointing to the baseline checkout's `src` directory. The final tests do not
import newly introduced production symbols, so all seven failures exercise the
old behavior rather than failing during collection.

## Confirmed follow-ups outside this patch

The following were reproduced using isolated mocks or local fake workers. They
remain open; this patch is not a claim that all cancellation/recovery paths are
fixed. Follow CONTRIBUTING's issue-first policy before changing Safety Kernel
or worktree ownership behavior.

### Durable restart reconciliation

- Restart changes a RUNNING task to BLOCKED and its run to INTERRUPTED, but
  leaves the owner dispatch STARTED. Executor re-entry ignores non-RESERVED
  rows and same-request retry returns the stale dispatch.
- A crash after owner reservation commits but before its thread starts leaves
  READY/RESERVED. Same-request retry returns `created=False` without starting
  the executor; the current replay sweep covers supervised-auto reservations.
- Proposed next step: open a focused recovery issue defining truthful terminal
  dispatch repair and explicit owner retry semantics. Add crash-boundary,
  repeated-recovery, and concurrent-retry fixtures before implementation.
  Do not simply redispatch uncertain prior execution.

### Process and stream teardown

- Both runtime adapters bound process wait, then await stdout/stderr EOF without
  a deadline. An exited fake process with a stalled reader exceeds the deadline.
- Timeout cancels drainers before termination/reap. A fake SIGTERM handler that
  writes 1 MiB can leave task/run RUNNING with writer held even after SIGKILL,
  because process completion waits for an undrained paused pipe.
- Cancellation while the spawn adapter is suspended after process creation can
  escape before durable run/active-registry registration, leaving READY/RESERVED
  with a held writer and an observed live child.
- A group leader's exit is not proof its descendants exited. Current supervisor
  cleanup can drop ownership while a same-group inherited-pipe descendant lives.
- Proposed next step: open a lifecycle teardown issue, agree exact-child/group
  ownership and bounded cleanup semantics, then implement one coordinated
  process-and-stream deadline with cancellation-safe pre-registration cleanup.
  Include timeout/owner-cancel overlap, repeated cancellation, blocked output,
  inherited pipes, and no-unrelated-process-signalling tests.

No duplicate billable execution was demonstrated. Same-request re-entry after
the repaired failure paths creates no additional run in the new regressions;
that narrower result is not proof of cross-process or crash-recovery exactly-once
execution.
