# PI-5B2 Real Child Execution Wiring

PI-5B2 is the first phase allowed to turn an accepted `pao_delegate` request into a real PAO child task. It remains feature-gated OFF by default until target-Mac acceptance.

## Required authority chain

Parent Pi worker
→ trusted `pao_delegate` socket client
→ parent-bound `UnixDelegationBrokerServer`
→ host `PAODelegationChildPort`
→ host scheduler/recommendation
→ durable `DELEGATED_CHILD` dispatch authority
→ independent child task/worktree/writer/run
→ existing runtime executor (Pi or OpenCode, host-selected)
→ existing quota admission
→ deterministic child verifier
→ bounded sanitized child result returned to parent

## Hard invariants

- the worker cannot choose child provider/model/runtime/target/worktree/verifier;
- parent identity and active parent run are checked host-side;
- child depth is exactly 1 and child delegation is disabled;
- child task and parent task have distinct worktrees and independent writer locks;
- the child uses the parent project/base SHA but receives its own durable task/run/dispatch IDs;
- child target selection uses current PAO planning and PI-4 shared quota truth;
- no automatic fallback after the child target is frozen;
- a child may be VERIFIED without changing parent task truth;
- parent deterministic verifier remains the only authority for the parent;
- broker output is bounded/sanitized and never returns raw credentials or raw provider errors;
- parent cancellation/exit must remove the broker socket and prevent stale child requests;
- production feature flag defaults to OFF.

## Dispatch authority

PI-5B2 must add a distinct durable `DELEGATED_CHILD` authority with legal source state `READY`. It must not reuse `OWNER_INITIATED_EXECUTION` or `SUPERVISED_AUTO`, because neither truthfully describes a worker-requested, host-approved child dispatch.

## Acceptance split

1. Code/CI: synthetic parent + fake/local child worker, no paid model calls.
2. Target Mac: one parent Pi request and one child execution, no recursion, no fallback, independent worktrees, child verifier VERIFIED, parent truth unchanged until its own verifier runs.

## Implemented host wiring

`PiRuntimeConfig.delegation_enabled` defaults to `False`. A host can explicitly
pass an enabled config to `build_control_service`; there is no worker-controlled
switch. The daemon injects its runtime router, quota observations, tier table,
execution evidence, connection manager, and recommendation service into
`PAODelegationChildPort`.

Enabled parents get a private temporary socket and an explicit trusted extension
outside their worktree. The session rejects requests until the parent's durable
RUNNING transaction commits. Exit, cancellation, spawn/start failures, timeout,
and emergency repair revoke the session and remove the socket. Child dispatch
records use `DELEGATED_CHILD`; both this authority and the durable child submission
namespace disable recursion in the Pi adapter, including later owner retries.

The child submission transaction rechecks matching active parent truth. It
inherits project, frozen base, working subpath, non-MANUAL policy, and tier floor.
The existing recommendation service admits the target and the durable dispatch
freezes it. The existing executor allocates its worktree/writer, rechecks launch
and quota gates, and runs the deterministic verifier. An aliased task workspace
is rejected before worker spawn. Child submission audit events retain parent
run/task lineage.

The child port waits at most 300 seconds by default. On timeout it returns the
current durable state with `verified=False`; it does not invent a terminal state.
An already admitted child remains owned by the existing executor. Results omit
worker transcripts and summaries; the Node tool exposes only a fixed-enum child
state and verification boolean. Child completion never transitions the parent.

Synthetic coverage lives in `test_pi5_child_execution.py` and
`test_pi5_execution_lifecycle.py`, alongside existing PI-5, Pi runtime, dispatch
authority, and PI-4 regression tests. The E2E runs the actual seeded TypeScript
socket client under Node with fake local parent/child workers and deterministic
verifiers. No target-Mac real-model acceptance is implied by these tests.
