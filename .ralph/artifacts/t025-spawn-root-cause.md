# T025 spawn observability and offline root cause

Date: 2026-09-14
Provider/model calls: 0
Classification: `WORKER_POST_CREATE_ACCOUNTING_ROLE_MISCLASSIFIED`

## Proven causal chain

1. Campaign C used campaign-scoped parent task ID
   `pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-task`.
2. The external budget supervisor classified a parent only when the worktree
   basename started `pi5b3g-obs`.
3. The Campaign C parent therefore entered the `child` branch.
4. `ProcessSupervisor.start` completed first, so an OS child and PID existed.
5. The child-only branch then evaluated `next(...)` over the empty pre-parent
   `generated_scope_validations` list.
6. Python converted the escaping coroutine `StopIteration` into
   `RuntimeError: coroutine raised StopIteration`.
7. The executor's broad `_spawn_worker` boundary translated that exception to
   `WORKER_SPAWN_FAILED` before durable run registration.

This exact chain explains all historical facts: one transient PID, generic
`RuntimeError`, zero durable run, zero scope validations, zero delegation, and
zero child workers.

## Deterministic no-egress reproduction

A local subclass reproduced the Campaign C wrapper ordering with
`ProcessSupervisor.start(("/bin/sleep", "30"), ...)` and the same legacy role
expression. The campaign-scoped parent was reported as `child`; the child-only
empty `next(...)` raised `RuntimeError: coroutine raised StopIteration` after
the supervisor reported an owned PID. Exact process-group cleanup removed the
PID and the supervisor's owned set returned to empty.

The permanent integration test
`test_post_create_spawn_adapter_failure_is_diagnosed_and_reaped` exercises the
same PAO executor/supervisor boundary. It proves:

- `SPAWN_ADAPTER_POST_CREATE`;
- exception class `RuntimeError`;
- child created and PID observed;
- no durable run or protocol bootstrap;
- exact SIGKILL/reap before pre-worker state repair;
- task/dispatch terminal `BLOCKED`;
- writer released;
- no owned PID or active execution remains;
- no raw argv value, prompt, or environment value in structured diagnostics.

## Sanitized observability

`pao-spawn-diagnostics-v1` records stage, exception class, safe errno, child
creation/observation/early-exit facts, safe exit code, executable and cwd
hash/existence/execute-bit facts, argv shape/count, environment key names only,
new-session and pipe milestones, durable-run status, protocol status, and the
auth-bootstrap observation state.

Task-local spawn observation closes the prior cleanup gap: if an adapter raises
after `ProcessSupervisor.start` created a process but before returning it, the
executor can recover, kill, and reap that exact process. Diagnostics are
best-effort and cannot change lifecycle correctness.

## Excluded classifications

- `PROCESS_CREATION_FAILED`: contradicted by the deterministic created PID.
- `EXECUTABLE_STARTED_THEN_EXITED`: the reproducer uses a sleeping worker; the
  wrapper raises while it is live.
- `WORKER_HANDSHAKE_FAILED` / `AUTH_BOOTSTRAP_FAILED` /
  `PROTOCOL_BOOTSTRAP_FAILED`: those stages were never reached.
- `DURABLE_RUN_REGISTRATION_FAILED`: Campaign C has no attempt/audit at that
  later boundary, and the reproduction raises before it.
- `WORKSPACE_OR_CWD_FAILURE`, `ENVIRONMENT_CONSTRUCTION_FAILURE`,
  `EXECUTABLE_OR_ARGV_FAILURE`, `PROCESS_GROUP_SETUP_FAILED`, and
  `STDIO_SETUP_FAILED`: the same production process-construction mechanism
  returns a live child before the wrapper exception.
- `PROCESS_SUPERVISION_RACE`: no scheduling race is needed; the failure is
  deterministic for every campaign-scoped parent ID with an empty child-scope
  list.

## Real executable no-egress smoke

The real PATH-resolved `pi --version` command was executed through
`ProcessSupervisor` with the production `build_worker_env`, disposable cwd,
new session, and stdout/stderr pipes. It exited 0, produced 7 stdout bytes and
zero stderr bytes, and left zero owned PIDs. Only hashes, lengths, environment
key names, and spawn metadata were retained. No prompt, model, auth refresh, or
provider request was used.

## Path/shebang audit

The Campaign C production executable is the PATH-resolved `pi` command, not a
space-containing shebang, so the earlier virtualenv-shebang defect is not its
cause. During the offline Node broker E2E, however, the test's generated fake
Pi script reproduced that known portability defect because `sys.executable`
was under this repository path containing spaces. Its test-only shebang is now
`#!/usr/bin/env python3`; this does not alter production launch semantics.

## Focused verification

- process/identity/dispatch/authority diagnostics: 80 passed, 1 socket-only
  test deselected in the restricted sandbox;
- Pi dispatch/broker/delegation/campaign/scope/identity matrix under short
  `/tmp` socket paths: 154 passed, 1 skipped;
- focused Ruff: pass.

