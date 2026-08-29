# OpenCode Stage C — Final Report

**Status: `OPEN_CODE_STAGE_C_REPAIR_PARTIAL_PENDING_MINIMAX_RETEST`**

PR #17's Stage C harness has been hardened so provider acceptance is deterministic and evidence
semantics are conservative. The repaired code no longer treats non-empty assistant text as a
completion pass, no longer emits raw HTTP bodies, and no longer claims session deletion unless
it is verified.

| Provider | SHADOW | ACTIVE | Isolation | Completion | Cancellation | Current status |
| --- | --- | --- | --- | --- | --- | --- |
| MiniMax | historical PASS | historical PASS | historical PASS | hardened re-test required | hardened re-test required | **HARDENED_RETEST_REQUIRED** |
| Z.AI/GLM | historical PASS | historical PASS | historical PASS | not re-run | not re-run | **DEFERRED_PENDING_QUOTA_RESET** |

## Hardened Stage C acceptance contract

A real completion now passes only when every condition below is true:

1. an assistant completion exists;
2. no assistant error is present;
3. provider ID exactly matches the selected provider;
4. model ID exactly matches the selected catalog model;
5. `finish == stop`;
6. assistant output equals the host-derived expected README H1 exactly.

The disposable fixture now generates a runtime nonce in its H1. The model prompt contains only
instructions to read `README.md` and return its H1; it never contains the expected heading. The
host parses the expected H1 independently. This turns the completion smoke into a repository-read
proof rather than a prompt-echo proof.

## Cleanup and evidence hardening

The repaired harness:

- captures the exact fake-daemon PID;
- captures the exact `opencode serve` PID without a subshell;
- stops only those exact PIDs;
- verifies both PIDs are gone;
- verifies the fixture is Git-clean before deletion;
- deletes the fixture and work directory;
- records session deletion as attempted and separately verifies deletion with GET/not-found;
- reports `session_cleanup = NOT_PROVEN` if deletion cannot be verified;
- maps provider/runtime errors to bounded categories rather than committing raw response bodies;
- never dumps raw assistant error objects into evidence.

The credential-free Stage C unit/syntax checks are part of normal CI. Real provider smoke remains
local-only because GitHub Actions has no provider credentials.

## MiniMax

The old MiniMax evidence was generated before the cleanup fix and before the strict completion
oracle. It is retained as historical evidence but is explicitly superseded for final acceptance.
The repaired harness must be rerun locally against a model discovered from the current OpenCode
catalog before PR #17 can be frozen as fully repaired.

Current status:

`MINIMAX_STAGE_C = HARDENED_RETEST_REQUIRED`

## Z.AI / GLM

No additional Z.AI calls were made during this repair. The prior observed failure category is:

`MODEL_UNAVAILABLE`

The user has reported that the Z.AI weekly quota is exhausted, which is a possible contributing
factor. The actual root cause remains:

`NOT_YET_CONFIRMED`

Current status:

`ZAI_STAGE_C = DEFERRED_PENDING_QUOTA_RESET`

A re-test is required after the quota resets. The repository no longer claims that either the
provider entitlement or OpenCode is definitely broken.

## Security

- Credential contents read by the repaired Stage C code: **NO**.
- Credential contents logged/committed: **NO**.
- Raw HTTP/provider response body committed by the repaired path: **NO**.
- Authorization/Cookie/API-key/OAuth headers committed: **NO**.

## Remaining acceptance blocker for PR #17

One authoritative local MiniMax run with existing authentication is still required. GitHub-hosted
CI can verify the non-credential contract but cannot perform that runtime acceptance without
copying credentials into CI, which this spike explicitly forbids.
