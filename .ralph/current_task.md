# Current task

T027 — verify and checkpoint spawn repair

Acceptance:

- Run the full Python suite and repository-wide Ruff.
- Inspect diff/sanitization/secrets and commit the spawn repair separately.
- Push normally and wait for every required exact-head PR #54 CI job before any model use.

Verification:

- Local verification artifact records exact results and frozen hash preservation.
- Local HEAD, remote branch, and Draft PR head match; exact-head Python, macOS Swift, and OpenCode CI pass.

No model call. Campaign D remains forbidden until this exact pushed-head gate is green.
