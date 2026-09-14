# Current task

T028 — execute fourth bounded PI-5B3G campaign

Acceptance:

- Refresh product/build/heartbeat/DB/ownership, fixture, frozen hashes, target/auth, exact quota, and shared-pool preflight.
- Create one fresh Campaign D, preflight all identities and sanitized spawn-contract facts, then gracefully transition canonical ownership.
- Run at most three strictly sequential authorized observations, stopping on the first spawn/scope/safety failure; clean up and restore the product.

Verification:

- Authoritative campaign/process/audit/verifier/DB evidence proves either 3/3/6 success or the exact first blocker with no retry.
- Terminal cleanup restores the repaired product healthy with advancing heartbeat and no campaign process/alternate owner.

Use only the renewed Campaign D authorization. Never reuse or mutate Campaigns A-C.
