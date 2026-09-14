# Current task

T009 — Execute bounded PI-5B3G SHADOW campaign

Acceptance:

- Current build, campaign, quota, execution-evidence, fixture, frozen-verifier, and zero-owner gates pass; exactly three sequential SHADOW observations complete through the real parent-to-`pao_delegate`-to-child path with no retry, fallback, or grandchild.

Verification:

- Terminal evidence accounts for exactly three parents and three children, all six worker processes have exited, campaign admission is closed, and canonical DB/writer/process state is clean before the normal repaired product is restored.

Intended file scope: the already-prepared external campaign host, disposable fixture/worktrees, canonical runtime/database/socket, and sanitized evidence only. Frozen verifier semantics and production source remain unchanged.
