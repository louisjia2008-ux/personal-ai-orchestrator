# Current task

T016 — Finalize blocked campaign evidence and CI

Acceptance:

- Preserve sanitized evidence of the pre-worker request-ID conflict.
- Prove campaign admission closed, zero workers/resources, and product restore.
- Diff/JSON/sanitization/secret checks pass.
- Normal-push only and wait for exact-head CI; PR remains Draft/unmerged.

Verification:

- Evidence parses and contains no raw provider transcript or credential value.
- Local/remote/PR exact HEAD match.
- Required GitHub checks pass on that exact SHA.

Intended file scope: new blocker evidence and Ralph state only. Do not repair or
retry the campaign-host identity defect in this run.
