# T013 deterministic PI-5B3G scope validator

## Outcome

PASS. `pi5b3g-child-scope-v2` is a repository-owned, local, deterministic,
opt-in broker policy. It combines positive observation/file/JSON proof with
ordered semantic-category rules and narrowly recognized explicit negations.
Ambiguous language and validator failures fail closed.

The gate consumes the parent session's bounded call before validation and runs
before child plan construction or child-port execution. A rejection is cached
idempotently and returns a stable rule ID.

## Sanitized observability

The trace contains validator version, decision, rule ID, category, stage, input
field, observation number, expected filename, normalized intent length, intent
SHA-256, reason length, reason SHA-256, child-forwarded, and
child-worker-started. It contains no raw intent, reason, or provider transcript.

## Offline corpus and verification

- corpus SHA-256: `96d54800a199f5c0a4244a3b3f32238bcf9854f6ea5bdd5780922ab219352ff7`
- cases: 44 total; 10 safe; 30 unsafe; 4 ambiguous/fail-closed
- safe negation cases: 7
- corpus result: 44/44 expected decision, rule ID, and category
- focused validator/broker/lifecycle: 66 passed
- delegation/campaign set: 202 passed, 1 skipped
- final full Python after the fail-closed exception path: 1243 passed, 1 skipped
- Ruff and `git diff --check`: PASS

The existing Node E2E initially failed because its generated shebang used the
project venv's absolute path containing spaces. The same venv through the
no-space `/tmp/pao-venv` alias passed all focused tests without changing that
test. The final full suite passed through that same no-space alias.

Frozen semantic verifier/profile files were not modified. No model/provider
call was made during T011-T013.
