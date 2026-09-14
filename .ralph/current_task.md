# Current task

T029 — finalize Campaign D blocker evidence and CI

Acceptance:

- Preserve Campaign D separately from historical Campaigns A-C.
- Retain only sanitized process, protocol, routing, verifier, task-state, quota, and cleanup evidence.
- Commit and normally push the blocker evidence, then wait for required exact-head CI.

Verification:

- JSON parse, diff check, sanitization assertion, and secret scan pass.
- Draft PR #54 exact head passes all required CI and remains unmerged.

Campaign D is consumed and terminal. Do not retry it or start another model worker.
