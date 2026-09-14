# Current task

T013 — Implement deterministic diagnosable scope validator

Acceptance:

- Add a repository-owned local validator with positive observation/filename/JSON proof, category-specific unsafe requests, narrowly supported safe negation, and fail-closed ambiguity.
- Return a stable sanitized result containing version, decision, ordered RULE_ID/category/stage, expected filename, lengths/hashes, and no raw text.
- Integrate validation before broker child execution so rejection cannot reach the child port; retain a sanitized trace hook for campaign evidence.
- Add permanent corpus and broker tests proving deterministic IDs, no raw transcript, and zero child calls/workers after rejection.

Verification:

- All 44 corpus cases match the frozen expected decision/rule/category.
- Required direct tests cover boundary lengths, deterministic rejection, sanitized evidence, and no child execution after rejection.
- Existing PI-5 broker/child/campaign/security tests remain green.

Intended file scope: one new validator module, narrow broker integration, focused tests, and `.ralph/` state. Frozen semantic verifier/profile remain byte-identical; no model/provider calls.
