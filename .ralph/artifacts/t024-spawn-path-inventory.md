# T024 parent worker spawn-path inventory

Date: 2026-09-14
Mode: read-only source and historical-state reconstruction; no model/provider call

## Result

Campaign C failed after the OS process was created but before the campaign
accounting wrapper returned the `SupervisedProcess` to the durable executor.
The generic `WORKER_SPAWN_FAILED` boundary currently covers that post-create
wrapper failure as well as true `create_subprocess_exec` failures.

Durable run registration occurs **after** subprocess creation. Campaign C has
no run row because the campaign wrapper raised before the executor reached the
atomic `start_dispatched_worker` call.

## Ordered production path

1. `initiate_owner_dispatch` reserves the canonical dispatch, transitions the
   task from `SUBMITTED` to `READY`, and starts one daemon thread whose target
   is `executor.execute` (`dispatch_initiator.py:307-324`).
2. `RuntimeDispatchExecutor.execute` resolves the selected target to its
   host-owned runtime executor, checks the shared quota pool, and calls that
   executor (`runtime_dispatch_executor.py:88-138`).
3. `OwnerDispatchExecutor.execute` creates one asyncio loop with
   `asyncio.run`; `_execute_async` reloads the durable reservation and validates
   authority, task state, registered project, current target launchability,
   repository fingerprint, worktree, writer ownership, and quota
   (`dispatch_executor.py:256-424`).
4. `PiDispatchExecutor._spawn_with_activation` creates the parent delegation
   broker and trusted socket tool, builds the host-owned Pi JSON argv, validates
   tool visibility, writes `WORKER_ARGV_BUILT`, and calls the configured
   supervisor (`pi_dispatch_executor.py:181-265`).
5. `ProcessSupervisor.start` invokes `asyncio.create_subprocess_exec` with the
   exact argv, selected cwd and allowlisted environment; stdout and stderr are
   pipes and `start_new_session=True`. Only after the await returns does it wrap
   and index the child by PID (`process_supervisor.py:32-51`).
6. Only after `_spawn_worker` returns does the executor derive the run ID and
   atomically persist `RUNNING`, PID, writer token, and run row through
   `start_dispatched_worker` (`dispatch_executor.py:422-448`). A failure in
   this later operation is `RUN_START_FAILED` and cancels the exact returned
   process (`dispatch_executor.py:449-463`).
7. The `ActiveExecution` registry is populated after durable start
   (`dispatch_executor.py:465-473`). Pi stdout/stderr draining, timeout,
   provider/model protocol validation, and completion occur later
   (`pi_dispatch_executor.py:276-367`). Thus no protocol or auth-output
   classification had begun in Campaign C.

## What the current generic boundary includes

The `except Exception` at `dispatch_executor.py:423-434` encloses the entire
runtime-specific `_spawn_worker`. For Pi this includes broker creation, socket
tool seeding, argv construction/validation, the OS subprocess call, and any
custom supervisor logic executed before its `start` coroutine returns. It is
therefore not evidence that OS process creation itself failed.

By contrast, durable start failures are translated separately to
`RUN_START_FAILED`, while protocol failures occur after a durable run and are
represented in the worker result.

## Campaign C historical reconstruction

Authoritative canonical audit for task
`pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-task` shows, in order:

- canonical submit and owner dispatch reservation;
- worktree registered under the identity-v1 campaign directory;
- writer acquired;
- exact MiniMax quota admitted;
- `WORKER_ARGV_BUILT` for executable label `pi`, JSON mode, no session,
  no approval, no extensions, frozen file-tool set plus `pao_delegate`,
  host-selected `minimax-cn/MiniMax-M3`, two trusted extension paths, and the
  fixed bounded parent prompt;
- writer released and task/dispatch blocked as `WORKER_SPAWN_FAILED` with only
  `RuntimeError` retained.

There is no Campaign C run row. The workspace row is present with no writer.
Sanitized campaign evidence records transient PID 68140 and proves it was no
longer alive after cleanup. Provider/model egress remains
`UNKNOWN_NOT_PROVEN` because process creation alone does not prove exec or a
provider request.

The launch contract used:

- target: `pi-minimax-cn-coding-plan-MiniMax-M3`;
- runtime/provider/model: Pi JSON / `minimax-cn` / `MiniMax-M3`;
- executable: PATH-resolved `pi`;
- cwd: the registered disposable observation worktree;
- environment: the production allowlist from `build_worker_env`, with only the
  campaign-owned `PI_CODING_AGENT_DIR` value added by the external budget
  supervisor; no environment values are recorded here;
- stdio/session: stdout pipe, stderr pipe, new process session/group;
- timeout: executor-configured bounded worker timeout;
- protocol: one-shot Pi JSON stream, exact final provider/model, balanced tool
  start/end, no extension errors.

## Successful-launch comparison

The earlier Campaign A task `pi5b3g-obs1-parent` used the same executable,
Pi JSON mode, target/model, tool set, environment construction, pipe/session
settings, and runtime-specific executor. Its canonical audit records
`WORKER_ARGV_BUILT`, then `RUN_STARTED` at PID 38492 and `READY -> RUNNING`.

The material host-side delta is campaign execution identity. Campaign A's
worktree basename began `pi5b3g-obs1...`; Campaign C's campaign-scoped basename
began `pi5b3g-4c614...-obs1...`. The Campaign C external `BudgetSupervisor`
still classifies a parent only when `Path(cwd).name.startswith("pi5b3g-obs")`
(`run_campaign.py:385-388`). The new parent is therefore classified as a child.
After `super().start` has already returned a process, the wrapper enters its
child-only accounting path and searches for a forwarded child scope record
(`run_campaign.py:403-410`), but no such record can precede the first parent.
This is the exact post-create surface to reproduce in T025.

## Schema and cleanup boundary

`owner_dispatches.request_id` is unique and stores the terminal failure code
and reason; `runs.run_id` is primary-keyed and an active-run uniqueness index
protects one active run per task (`safety_kernel.py:492-516`). Campaign C's
absence of a run proves the atomic durable-start function was not reached.

The current pre-worker failure path releases the writer and blocks the legal
source task and dispatch, but it assumes no child exists. A post-create wrapper
exception violates that assumption: the child is indexed inside the supervisor
yet is not returned to the executor for exact cancellation. T025/T026 must add
stage-aware, transcript-free evidence and exact cleanup for this boundary.
