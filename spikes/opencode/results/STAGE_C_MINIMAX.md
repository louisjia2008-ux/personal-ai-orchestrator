# OpenCode Stage C — MiniMax: HARDENED_RETEST_REQUIRED

The previously committed MiniMax Stage C run is retained as **historical pre-hardening
evidence**. It passed the then-current harness, but that harness accepted any non-empty assistant
text and the authoritative provider evidence was produced before the exact-PID cleanup repair.

Therefore the prior runtime result is now:

`HISTORICAL_PASS_SUPERSEDED_PENDING_HARDENED_RETEST`

Sanitized historical machine evidence remains at
[`stage_c_minimax_evidence.json`](./stage_c_minimax_evidence.json); it must not be represented as
the authoritative result of the repaired harness.

## Historical identity

| Field | Value |
| --- | --- |
| Provider display name | MiniMax Token Plan (minimaxi.com) |
| Provider id | `minimax-cn-coding-plan` |
| Model id used historically | `MiniMax-M2.5` |
| OpenCode | 1.18.23 |
| Auth metadata | PASS |
| Credentials exposed | NO |

## Hardened re-test gate

The next authoritative MiniMax run must use the current OpenCode catalog rather than assuming a
model ID and must prove all of the following with the repaired harness:

- auth metadata: PASS;
- catalog membership: PASS;
- SHADOW: PASS and no model mutation;
- ACTIVE: PASS only for the target session;
- second-session isolation: PASS;
- disposable README contains a runtime-generated nonce in its H1;
- the prompt does **not** contain the expected H1;
- the assistant output equals the host-parsed README H1 exactly;
- assistant error absent;
- provider ID and model ID exactly match;
- `finish == stop`;
- fixture remains byte-clean and is deleted;
- DELETE-session is followed by GET verification;
- exact fake-daemon PID and exact `opencode serve` PID are both gone after cleanup;
- cancellation is PASS or honestly `CANCELLATION_NOT_PROVEN`;
- no credential or raw provider response is logged or committed.

## Historical run (non-authoritative after hardening)

The pre-hardening run reported a real MiniMax completion, second-session isolation, cancellation,
and zero credential exposure. Those facts remain useful historical evidence, but they do not
satisfy the repaired Stage C acceptance gate until MiniMax is rerun locally with existing
provider authentication.

Quota remaining remains `UNKNOWN`; OpenCode Stage C completion evidence is not a quota API.
