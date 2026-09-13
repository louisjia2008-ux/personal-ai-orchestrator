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
