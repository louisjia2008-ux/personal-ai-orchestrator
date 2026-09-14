# T019 — deterministic campaign identity implementation

## Result

PASS. `pi5b3g-campaign-identity-v1` derives every parent identity from the
authoritative `delegation-campaign-<32 hex>` ID, observation number, finite role,
and finite operation. Reconstruction is deterministic; a fresh campaign UUID
necessarily changes the namespace. All generated canonical IDs use the allowed
alphabet and remain at most 128 characters.

Implementation SHA-256 before commit:
`79ebc21d33d4a39d05bb0a6049b43c1963cfa8ace8c26a4268a6844b9382c03a`.

## Production child audit

The existing child task derivation was moved without semantic change into the
identity module and is still SHA-256(parent task, parent run, ordinal), truncated
to 20 hex characters. Named child submit and dispatch request helpers now drive
both the actual `PAODelegationChildPort` and the delegated-child namespace check
in `initiate_owner_dispatch`. Child dispatch and run identities remain the
canonical deterministic derivatives of the child dispatch request.

The model still controls no campaign/task/request/dispatch/run identity. Its
protocol `tool_call_id` remains only a bounded session-local broker replay key,
not a global persistence identity.

## Offline proof

- Focused identity + canonical UDS admission + broker + child + dispatch tests:
  `57 passed`.
- Remaining campaign lifecycle + parent execution + Safety Kernel + accepted
  scope-validator regressions: `74 passed`.
- Focused Ruff over all changed production/test source: PASS.
- `git diff --check`: PASS.

The canonical UDS integration test seeds historical
`pi5b3g-obs1-submit`, creates Campaign A, submits and dispatches Observation 1,
replays both exact payloads idempotently, proves changed submit/dispatch payloads
retain their canonical conflict codes, stops Campaign A, creates Campaign B, and
successfully admits a distinct Observation 1. No provider or model call occurs.

## Unchanged contracts

No daemon lifecycle, verifier/profile, scope-validator, provider target, quota,
retry, fallback, worker limit, campaign claim, or observation semantics changed.
