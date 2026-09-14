# Current task

T005 — Run targeted and broad project verification

Acceptance:

- Relevant Python lifecycle/control/campaign/database/process, Ruff, OpenCode adapter, Swift, and broader suites are recorded and pass or an honest blocker is recorded.

Verification:

- Commands are taken from `.github/workflows/ci.yml` plus targeted lifecycle suites.

Intended file scope: `.ralph/artifacts/*` and `.ralph/*` only; project sources are read-only unless verification exposes a repairable regression.
