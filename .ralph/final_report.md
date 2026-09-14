# Ralph final report — PI-5B3G scope-validator hardening

## Final status

`BLOCKED_PI_5B3G_NEW_CAMPAIGN_REQUEST_ID_CONFLICT`

## Completed engineering work

- Preserved the completed daemon lifecycle repair and historical failed campaign.
- Inventoried the old broad-regex gate without inferring its unknown raw trigger.
- Froze a deterministic 44-case offline corpus: 10 safe, 30 unsafe, 4 ambiguous.
- Implemented `pi5b3g-child-scope-v2` with positive single-file/JSON proof,
  ordered category rules, explicit safe-negation handling, fail-closed ambiguity,
  stable RULE_ID diagnostics, transcript-free hashes/lengths, and opt-in broker wiring.
- Proved rejection consumes the bounded call and never reaches child execution.
- Passed 66 focused tests, 202 delegation/campaign tests with 1 skip, full Python
  1243 with 1 skip, and Ruff.
- Committed/pushed validator commit `64ce05c414ba25fab62f175ca98812e8dd396a6f`;
  exact-head Python, macOS Swift, and OpenCode CI passed before live work.

## Live result

All refreshed preflight gates passed. The repaired canonical daemon shut down
normally and released its socket/state. A distinct new campaign
`delegation-campaign-fb69fd7288714eeaacbb9e5550ab5c82` was created, but the
host reused historical request ID `pi5b3g-obs1-submit`. Canonical storage
rejected task submission with `conflicting_request_id` before dispatch.

No MiniMax parent prompt was sent. No parent or child worker, child forwarding,
scope validation, retry, fallback, grandchild, campaign worktree, or broker was
created. Per the stop-on-invariant contract, the observation was not retried
and no later observation or replacement campaign was started.

## Cleanup and restore

The new campaign is `STOPPED / OWNER_STOPPED` at 0 consumed observations.
Campaign processes/resources and state writers are zero. The exact repaired
normal PAO product is restored with one GUI, one PyInstaller parent, one serving
child, one canonical socket owner, health `ok`, advancing heartbeat, SQLite
`quick_check=ok`, and RUNNING/ACTIVE/HELD `0/0/0`.

## Evidence and remaining issue

Sanitized evidence is in
`docs/acceptance/pi5b3g-scope-v2-2026-09-14/` and its companion Markdown
report. No raw provider transcript, generated dynamic argument, or credential
value was retained or committed. Draft PR #54 remains open, Draft, and unmerged.

Remaining issue: before any separately authorized future campaign, the campaign
host must deterministically mint fresh parent task/request/dispatch identities
that cannot collide with historical canonical rows, and that offline repair
must receive a new checkpoint and exact-head CI. This run grants no retry.
