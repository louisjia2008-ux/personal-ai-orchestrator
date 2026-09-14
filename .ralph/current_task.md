# Current task

T012 — Build offline adversarial scope corpus

Acceptance:

- Add a deterministic local corpus covering safe exact/wording/negation, unsafe authority/file/tool/delegation/retry/fallback/injection, and ambiguous fail-closed inputs.
- Encode expected ALLOW or exact rejection category without calling any external model.
- Run the corpus against a faithful baseline of the current external validator and record its false-positive/false-negative gaps before implementation changes.

Verification:

- Corpus includes every owner-required category and deterministic case identifiers.
- Offline baseline report demonstrates current behavior and is sanitized.

Intended file scope: new test/corpus fixtures plus `.ralph/` evidence only. Production source and frozen verifier artifacts remain unchanged; no model/provider calls.
