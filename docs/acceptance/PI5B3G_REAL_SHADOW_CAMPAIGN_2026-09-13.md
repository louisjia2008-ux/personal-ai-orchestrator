# PI-5B3G target-Mac campaign preflight — 2026-09-13

FINAL_STATUS: **PI_5B3G_REAL_SHADOW_CAMPAIGN_BLOCKED_PREFLIGHT**

The existing target-Mac product daemon is reachable, but its B3F campaign
control endpoint returns HTTP 404 and its provider projection contains no
`pi-minimax-cn-coding-plan-MiniMax-M3` target. The product runtime also contains
no execution-verification evidence for that exact target. The campaign was
therefore not started. No real parent or child worker was launched.

## Scope and baseline

- Repository: `louisjia2008-ux/personal-ai-orchestrator`.
- Branch: `feat/pi-delegation-real-shadow-campaign-05b3g`; PR #53 remains Draft.
- Fetched remote/source baseline: `16e2f146241bebcbdb37a2b51023c02c01226fae`.
- Contract baseline main: `64a45a3adaafb42cac2c6a55a3ac0db31c06f059`.
- The complete `docs/PI5B3G_REAL_SHADOW_CAMPAIGN.md` was read before preflight.
- A clean dedicated source worktree was created on the external disk. Source
  HEAD and status remained unchanged until these acceptance documents were added.

## Direct preflight evidence

| Check | Result |
| --- | --- |
| `pi --version` | `0.85.1` |
| `pi auth check --provider minimax-cn --json --no-refresh` | `ready` |
| `pi --list-models minimax-cn` | Includes `MiniMax-M3` |
| Existing product UDS `GET /v1/health` | HTTP 200, `status=ok` |
| Existing product UDS `GET /v1/providers` | HTTP 200, five providers, zero matching parent targets |
| Existing product UDS `GET /v1/settings/delegation-calibration-campaign` | HTTP 404, `not_found` |
| Exact parent execution evidence in product runtime | Absent |
| Current quota/shared-pool admission | Not evaluated; earlier host gates blocked |

The catalog command initially encountered sandbox lock-file permission errors;
it succeeded with normal host access. Both invocations were catalog operations,
not inference workers. No auth file contents or credential values were inspected.

The missing endpoint establishes that the required surface is unavailable on
the observed daemon; it does not establish the daemon's deployed commit or a
production source defect. No daemon upgrade/restart, provider import, evidence
copy into the host, exact-target inference probe, or production repair was made.

## Budget, cleanup, and limits

Authorized observations: 3. Admitted/completed observations: 0/0.
Parent/child/total real worker processes: 0/0/0. Retries, fallback, grandchildren:
0/NO/0. Observations 1–3 are all `NOT_STARTED`.

No fixture, task, run, writer, broker, or worker was created by this attempt.
No campaign start or stop POST was issued. The existing campaign state is
**UNKNOWN_ENDPOINT_UNAVAILABLE**, not STOPPED or EXHAUSTED. Global daemon task,
writer, and process counts were not audited and remain unknown. Isolation,
slot accounting, delegation tool behavior, and deterministic verification were
not exercised. No real owner project was used for worker execution.

Offline verifier profiles/tests were not prepared because the mandatory host
preflight failed before fixture creation or any model use. No model output or
raw provider transcript was produced. Production source and settings were not
changed; enforcement was not enabled.

## Readiness and validation

The existing read-only `load_readiness_evidence(runtime_state_root)` and
`evaluate_delegation_readiness` were run against the observed product runtime,
using the explicit policy:

```python
DelegationReadinessPolicy(
    min_complete_samples=3,
    require_quota_comparability=True,
    require_same_pool_sample=False,
    require_different_pool_sample=False,
)
```

Result: **NOT_READY**, zero shadow observations and zero complete samples.
Blocking reasons: `NO_SHADOW_EVIDENCE`, `INSUFFICIENT_COMPLETE_SAMPLES`.
Advisories: `NO_QUOTA_COMPARABLE_SAMPLES`, `SAME_POOL_NOT_OBSERVED`,
`DIFFERENT_POOL_NOT_OBSERVED`. The full sanitized report is committed alongside
this document. This is a preflight diagnostic, not a completed campaign report.

Only acceptance evidence consistency, existing `assert_sanitized` checks for
every committed JSON, and `git diff --check` are required locally for this
documentation-only result. Full pytest, Swift, OpenCode, and full Ruff were not
run locally. Exact-head CI is reported on PR #53 after the evidence push.

## Next action

Reconcile the target-Mac host with the frozen B3F operator surface and the
existing verified parent target, then repeat read-only preflight. Resolve the
host deployment/state boundary before starting this campaign. The zero-worker
stop does not justify bypassing a gate or substituting historical fixture state
for current host truth. PR #53 remains Draft and must not be merged.
