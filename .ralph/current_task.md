# Current task

T019 — implement and prove campaign-scoped identities

Acceptance:

- Add a small validated deterministic `pi5b3g-campaign-identity-v1` factory.
- Reuse named production child identity helpers rather than duplicated literals.
- Add the required unit matrix and canonical control-plane admission/resubmission/conflict integration tests with no provider calls.

Verification:

- Focused identity, canonical admission, child identity, campaign, and broker tests pass without changing unrelated semantics.

No model calls or live daemon/campaign state changes are permitted in this task.
