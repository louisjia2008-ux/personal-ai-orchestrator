# Current task

T022 — finalize identity campaign evidence and CI

Acceptance:

- Preserve all three terminal campaign histories separately.
- Record the successful identity repair and the third campaign's first parent worker-spawn blocker without raw provider transcripts or credential values.
- Commit and normally push sanitized evidence, wait for exact-head CI, keep Draft PR #54 unmerged, and leave the repaired product healthy.

Verification:

- JSON parses, sanitization/secret/diff checks pass, local and remote branch heads match, all required CI jobs pass, and the worktree is clean.

T021 is terminal `failed_blocked`: its single authorized launch attempt is consumed. No campaign retry or later observation is permitted in T022.
