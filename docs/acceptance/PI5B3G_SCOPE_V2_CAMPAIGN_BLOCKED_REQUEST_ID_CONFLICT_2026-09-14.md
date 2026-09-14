# PI-5B3G scope-v2 campaign — blocked before worker launch

Final status: `BLOCKED_PI_5B3G_NEW_CAMPAIGN_REQUEST_ID_CONFLICT`

The deterministic validator refinement completed successfully and was remotely
checkpointed before live execution. Exact-head CI passed for validator commit
`64ce05c414ba25fab62f175ca98812e8dd396a6f`. The 44-case corpus, focused
broker/lifecycle tests, relevant delegation/campaign set, full Python suite,
and Ruff all passed. No frozen semantic verifier/profile file changed.

The refreshed live preflight passed after recognizing the exact historical
campaign as the expected immutable `STOPPED / OWNER_STOPPED` predecessor rather
than incorrectly requiring an empty store. Product health/build/socket,
SQLite, idle accounting, fixture, verifier hashes, validator hash, target,
MiniMax auth/quota, and shared-pool gates all passed.

The repaired product then shut down through one normal SIGTERM to its verified
PyInstaller parent. Parent and serving child exited, the canonical socket was
removed, SQLite remained `ok`, and RUNNING/ACTIVE/HELD remained `0/0/0`.

The host created new campaign
`delegation-campaign-fb69fd7288714eeaacbb9e5550ab5c82`. It was distinct from
the historical failed campaign and began with a fresh three-observation budget.
Before any parent dispatch or model worker launch, however, the host attempted
to submit Observation 1 with historical request ID `pi5b3g-obs1-submit`.
Canonical task storage rejected it with `conflicting_request_id`. The audit
stream after campaign start contains only one `PROJECT_REGISTERED` event; no
task, dispatch, worker, worktree, or broker was created. The only matching
parent task/run rows remain the historical 02:53 UTC attempt.

This is a campaign-host identity defect, not a scope-gate rejection and not a
model result. Per the stop-on-invariant contract, no ID was changed live, no
retry was attempted, no next observation was started, and no second campaign
was created. New campaign accounting is 0 parent / 0 child / 0 total; outbound
MiniMax parent prompts are 0; scope validations are 0.

The new campaign was transitioned exactly from `ACTIVE` to
`STOPPED / OWNER_STOPPED` with zero consumed observations. No campaign process,
worktree, broker socket, or writer remained. The exact repaired product was
restored and verified as one GUI, one PyInstaller parent, one serving child,
and one canonical socket owner. Health is `ok`, heartbeat advanced, SQLite is
`ok`, and RUNNING/ACTIVE/HELD is `0/0/0`.

No raw provider transcript, generated intent/reason, credential value, or
provider payload is retained or committed. The validator was not loosened.
