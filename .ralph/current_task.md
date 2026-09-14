# Current task

T011 — Inventory current dynamic scope validation chain

Acceptance:

- Read the exact current pao_delegate schema, external campaign validator, broker/child forwarding, rejection sanitization, and campaign admission implementation.
- Document every forbidden rule with RULE_ID, field, match type/category, fail behavior, current coverage, and safe-negation risk.
- Make no validator or production-code change during this task and do not infer the previous rejected phrase.

Verification:

- `.ralph/artifacts/t011-scope-validator-inventory.md` is complete and supported by exact file/line references.
- Git diff outside Ralph state contains no source/test changes.

Intended file scope: `.ralph/` only. All source, tests, frozen verifier artifacts, campaign state, and canonical runtime remain unchanged.
