# Current task

T020 — verify and remotely checkpoint identity repair

Acceptance:

- Run the full required Python suite and repository-wide Ruff.
- Inspect diff/status, scan for credentials/raw transcripts, and commit the identity repair separately.
- Push normally and require all Draft PR #54 jobs green on the exact pushed HEAD before any model call.

Verification:

- Local full verification, clean/sanitized diff, focused commit, remote/PR head equality, and exact-head CI PASS.

No model calls or live daemon/campaign state changes are permitted until this task is complete.
