# OpenCode Stage C — Z.AI / GLM: DEFERRED_PENDING_QUOTA_RESET

**Runtime completion status: `DEFERRED_PENDING_QUOTA_RESET`**

No additional Z.AI provider calls are authorized for this repair cycle. The user has reported
that the Z.AI weekly quota is currently exhausted, so the previous turn-execution failure is
frozen as an observation rather than promoted to a provider/OpenCode root-cause claim.

## Frozen identity / prior observation

| Field | Value |
| --- | --- |
| Provider display name | Z.AI Coding Plan |
| Provider id | `zai-coding-plan` |
| Last model attempted | `glm-5.3-flash` |
| OpenCode at observation time | 1.18.23 |
| Auth metadata | PASS |
| SHADOW routing | PASS |
| ACTIVE session mutation | PASS |
| Second-session isolation | PASS |
| Real completion | DEFERRED_PENDING_QUOTA_RESET |
| Cancellation | DEFERRED |
| Credentials exposed | NO |

## Observed failure

The prior runtime attempt produced the sanitized category:

`MODEL_UNAVAILABLE`

This observation is retained for history. It does **not** establish why provider execution was
unavailable.

## Root-cause status

- Possible contributing factor: weekly quota exhausted.
- Root cause: `NOT_YET_CONFIRMED`.
- OpenCode defect: `NOT_CONFIRMED`.
- Provider entitlement defect: `NOT_CONFIRMED`.
- Credential defect: `NOT_CONFIRMED`.

The earlier wording that characterized this as definitely provider/plan-side is superseded by
this report.

## Required re-test

After the Z.AI quota resets, repeat the hardened Stage C provider-native completion test using a
catalog-confirmed model. Until then:

`ZAI_STAGE_C = DEFERRED_PENDING_QUOTA_RESET`

No credential changes, re-login, or additional quota-consuming calls should be attempted as
part of PR #17 repair.
