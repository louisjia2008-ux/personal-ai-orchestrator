# Current task

T014 — Verify and remotely checkpoint validator

Acceptance:

- Repeat the full Python suite after the final fail-closed micro-change.
- Ruff, diff checks, JSON checks, and secret scan pass.
- Commit the validator refinement separately with meaningful lineage.
- Normal-push only and wait for exact-head CI before any MiniMax call.

Verification:

- Full Python and Ruff pass.
- Local/remote/PR exact HEAD match.
- Required GitHub checks pass on that exact SHA.

Intended file scope: validator implementation/tests and Ralph evidence only.
Frozen semantic verifier/profile remain byte-identical. No MiniMax egress before
this task is complete.
