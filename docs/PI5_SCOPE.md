# PI-5 Bounded Delegation

PI-5 lets a Pi worker request bounded host-mediated delegation without turning Pi into the orchestrator authority.

PI-5A is intentionally non-executing: a Pi worker may emit a structured delegation request, but the custom tool never starts another model/process and never grants repository access. PAO remains responsible for deciding whether a request is admissible, which execution target to use, quota admission, child worktree isolation, deterministic verification, and final task truth.

Initial invariants:

- delegation is host-enabled and off by default;
- a worker may request only a bounded number of children;
- the worker supplies subtask intent/reason only, never provider/model/runtime;
- requests are structured evidence, not execution authorization;
- no recursive child delegation in the initial broker design;
- parent and child must never share a writable worktree;
- child completion cannot mark the parent complete;
- quota and deterministic verification remain PAO-owned;
- the delegation tool cannot enable shell access or bypass the existing worktree guard.

PI-5A delivers the typed request contract, deterministic Pi extension, bounded JSON-stream extraction, and feature-flagged argv wiring. PI-5B can add the host broker/IPC and real isolated child execution after PI-5A acceptance.
