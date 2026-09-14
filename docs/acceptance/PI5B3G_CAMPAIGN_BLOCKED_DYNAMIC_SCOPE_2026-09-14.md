# PI-5B3G campaign — dynamic child scope rejected

Final status: `BLOCKED_PI_5B3G_DYNAMIC_CHILD_SCOPE_REJECTED`

All refreshed preflight gates passed. The repaired product was stopped through
its normal SIGTERM path, and the bounded SHADOW campaign started with admission
for three observations. Starting the campaign launched no workers and did not
change task/run/writer counts.

Observation 1 launched exactly one MiniMax-M3 parent. It invoked `pao_delegate`
exactly once. The host validated the generated child `intent` and `reason`
before forwarding them. Lengths, expected artifact identity, and semantic marker
checks passed, but the generated intent failed the newly owner-required
forbidden-scope check. The host therefore rejected it locally and returned only
the generic broker error `CHILD_EXECUTION_ERROR`. No child prompt was forwarded,
no child worker was launched, and the raw generated strings were not retained.
Only their SHA-256 values, lengths, and boolean checks are preserved.

The campaign stopped immediately. Observations 2 and 3 were not started. The
single parent attempt is consumed operationally and was not retried, even though
the campaign store correctly reports zero admitted delegation observations.
Final campaign state is `STOPPED / OWNER_STOPPED`, with zero retry, fallback, or
grandchild.

Terminal cleanup passed: the parent and its process group are absent, broker
socket and directory are absent, fixture HEAD/status are unchanged, no orphan
campaign process remains, SQLite is healthy, and RUNNING/ACTIVE/HELD is
`0/0/0`. The exact repaired product was restored normally. Its serving child is
the sole canonical socket owner, health is `ok` with a progressing heartbeat,
build identity is exact, and canonical SQLite remains healthy and idle.

The frozen verifier profile and implementation were not modified. No raw
MiniMax transcript, generated intent, generated reason, credential, or provider
payload is committed.
