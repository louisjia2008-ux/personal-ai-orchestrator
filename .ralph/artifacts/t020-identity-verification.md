# T020 — identity checkpoint verification before live campaign

## Exact implementation commit

- Identity fix: `885f006a3294a758b0808616c83b60c1b4c97d6d`
- Commit message: `fix: scope PI-5B3G execution identities by campaign`
- Identity scheme: `pi5b3g-campaign-identity-v1`
- Identity implementation SHA-256 before commit:
  `79ebc21d33d4a39d05bb0a6049b43c1963cfa8ace8c26a4268a6844b9382c03a`

## Verification

The execution-cell boundary terminates commands running beyond roughly 32
seconds, so the exact 93-file pytest inventory was partitioned by sorted test
file without excluding any test. The two slow 60-test control-plane halves and
six remaining file batches produced:

- Python: `1260 passed, 1 skipped` across the complete collected inventory.
- Existing rerun policy: one test reran and passed in the first batch.
- Environment correction: one Unix-domain-socket test initially used the long
  macOS default temporary path because a pipeline-scoped `TMPDIR` did not reach
  pytest; the same test passed with `TMPDIR=/tmp`. This was not a source failure.
- Repository-wide Ruff: PASS.
- `git diff --check`: PASS.
- Focused pre-commit result remains `57 passed` plus `74 passed`.

No Swift or OpenCode source changed. Exact-head CI remains authoritative for its
configured macOS Swift and OpenCode jobs.

## Sanitization and scope review

- Changed production code is limited to deterministic identity construction and
  replacing duplicated child identity literals with the same named derivations.
- No daemon, scope validator, verifier/profile, provider target, quota, retry,
  fallback, worker budget, or campaign observation semantic changed.
- Secret-pattern scan found only the pre-existing synthetic
  `SECRET_MARKER`/`credential_ref` test fixture; no credential value, raw model
  transcript, or dynamic intent is present.
- Both historical campaign records and canonical rows remain untouched.

Normal push and exact-head Draft PR #54 CI are still pending. No model call or
live campaign transition is allowed until they pass.
