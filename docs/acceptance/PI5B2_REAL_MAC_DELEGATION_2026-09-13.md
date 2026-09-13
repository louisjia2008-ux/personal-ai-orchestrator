# PI-5B2 target-Mac real delegation acceptance — 2026-09-13

FINAL_STATUS: **PI_5B2_REAL_MAC_DELEGATION_ACCEPTANCE_BLOCKED_TOOL_ALLOWLIST**

One real Pi parent ran. No delegation request reached the broker and no child
worker ran. The parent correctly declined to fabricate child verification, did
not create its result file, and ended BLOCKED through the frozen deterministic
verifier. There was no retry, fallback, separate probe, or production source edit.
PR #36 remains DRAFT and must not be merged on this evidence.

## Narrow production integration defect

At baseline `04f41981d77b448d73c527d195756bb815f24c8d`,
`src/personal_ai_orchestrator/pi_runtime.py:264-265` always emits
`--tools read,edit,write,grep,find,ls`, including when delegation is enabled.
The parent argv did contain `--no-extensions`, the trusted worktree guard,
and the explicit trusted PI-5 extension. Loading that extension was insufficient:
Pi 0.85.1 also applies the `--tools` list to custom tools.

A narrow read of the installed Pi code established:

- `dist/main.js:420-421` copies the parsed tool list into `options.tools`.
- `dist/core/sdk.js:141` uses `options.tools` as `allowedToolNames`.
- `dist/core/agent-session.js:2161-2166` activates registered tools only when
  present in that allowlist when it is supplied.

Thus `pao_delegate` was excluded. The parent reported that only the six file
tools were available and stopped without inventing a result. Its actual calls
were `ls` and `grep`; protocol validation passed and extension-error count was
zero. Existing synthetic Node E2E directly invokes the registered extension tool
and does not exercise this Pi SDK allowlist boundary. Source fingerprints and a
bounded sanitized parent explanation are retained with the evidence below.

Per the acceptance instructions, implementation stops here. A separate repair
should explicitly admit `pao_delegate` only for the enabled parent, preserve the
OFF argv and the bash/powershell/webfetch exclusions, and add an offline test of
actual Pi tool activation. No repair or further model budget is implied here.

## Acceptance evidence

| Field | Result |
| --- | --- |
| BASELINE_HEAD | `04f41981d77b448d73c527d195756bb815f24c8d` |
| FINAL_HEAD | This evidence-only commit; resolve with `git log -1 --format=%H -- docs/acceptance/PI5B2_REAL_MAC_DELEGATION_2026-09-13.md` |
| PI_BINARY | `/Users/louisjia/.local/state/fnm_multishells/13206_1789225544014/bin/pi` |
| PI_VERSION | `0.85.1` |
| PI_AUTH_METADATA | minimax-cn ready, api_key; no-refresh; no credential contents inspected |
| PARENT_EXECUTION_TARGET | `pi-minimax-cn-coding-plan-MiniMax-M3` |
| PARENT_RUNTIME | Registry `pi`; protocol `pi-json` |
| PARENT_PROVIDER / PARENT_MODEL | `minimax-cn` / `MiniMax-M3`, observed in durable run result |
| FEATURE_FLAG_DEFAULT | FALSE, unchanged |
| FEATURE_FLAG_ACCEPTANCE_OVERRIDE | TRUE for the disposable parent executor only |
| PARENT_TASK_ID | `pi5b2-real-parent` |
| PARENT_RUN_ID | `run-owner-dispatch-pi5b2-real-parent-dispatch` |
| PARENT_DISPATCH_ID | `owner-dispatch-pi5b2-real-parent-dispatch` |
| PARENT_POLICY | BALANCED, non-MANUAL |
| PARENT_WORKTREE | `/Volumes/Taoruide外接/AI LLM TOOLS/pi5b2-real-acceptance-20260913/worktrees/pi5b2-real-parent` |
| BROKER_SOCKET_PERMISSIONS | `0600`; private directory `0700` |
| BROKER_ACTIVATED_AFTER_DURABLE_RUNNING | YES; see observed ordering below |
| DELEGATION_REQUEST_COUNT | 0 |
| DELEGATION_REQUEST_FIELDS | NOT_OBSERVED; no request sent |
| CHILD_TASK_ID / CHILD_DISPATCH_ID / CHILD_AUTHORITY | NOT_CREATED / NOT_CREATED / NOT_EXERCISED |
| CHILD_SELECTED_EXECUTION_TARGET | NOT_SELECTED |
| CHILD_RUNTIME / CHILD_PROVIDER / CHILD_MODEL | NOT_EXECUTED / NOT_EXECUTED / NOT_EXECUTED |
| HOST_TARGET_SELECTION | Child selection NOT_EXERCISED. Read-only preflight recommendation admitted M3; M2.7 and M2.7-highspeed lacked execution verification. This preview was not a frozen child decision. |
| SHARED_QUOTA_GATE | Read-only preflight and parent admission passed. Child admission NOT_EXERCISED. |
| CHILD_WORKTREE / WORKTREES_DISTINCT | NOT_CREATED / NOT_EXERCISED |
| PARENT_WRITER_PRESENT | YES, acquired for parent execution; full token omitted |
| CHILD_WRITER_PRESENT / WRITERS_DISTINCT | NO / NOT_EXERCISED |
| CHILD_DELEGATION_DISABLED | NOT_EXERCISED; child executor config was FALSE |
| GRANDCHILD_COUNT | 0; durable tasks contain only the parent |
| CHILD_VERIFIER | Six offline self-tests passed; real verification NOT_EXECUTED |
| CHILD_FINAL_STATE | NOT_CREATED |
| PARENT_STATE_WHEN_CHILD_VERIFIED | NOT_APPLICABLE; no child verification occurred |
| PARENT_VERIFIER | Six offline self-tests passed; real verifier FAILED because parent JSON was absent. Profile unchanged. |
| PARENT_FINAL_STATE | BLOCKED |
| AUTO_FALLBACK | NO |
| BROKER_SOCKET_CLEANED / BROKER_TEMP_DIRECTORY_CLEANED | YES / YES |
| WRITERS_RELEASED | YES |
| RUNNING_ROWS / ACTIVE_RUN_ROWS | 0 / 0 |
| ORPHAN_PROCESSES | 0; parent PID/process group 13099 absent after completion |
| MAIN_REPO_UNCHANGED | YES; same HEAD and clean status before/after |
| REAL_PARENT_WORKER_PROCESSES | 1 |
| REAL_CHILD_WORKER_PROCESSES | 0 |
| TOTAL_REAL_WORKER_PROCESSES | 1; budget capped at 2, exclusive marker prevents another attempt |
| INTERNAL_WORKER_ACTIVITY | 161 Pi events, 2 tool starts/ends; not a count of billable requests |
| SOURCE_CODE_CHANGED | NO |
| CREDENTIAL_VALUES_READ | NO by agent inspection; existing Pi/PAO credential consumption stayed internal |
| CREDENTIAL_VALUES_EXPOSED | NO |

## Frozen fixture, verifiers, and timing

`FIXTURE_REPO` is
`/Volumes/Taoruide外接/AI LLM TOOLS/pi5b2-real-acceptance-20260913/fixture`.
`BASE_SHA` and `MAIN_HEAD_BEFORE` are
`7bb8752a7a73207bc7d3863645fec8a2b159e0ba`; `MAIN_STATUS_BEFORE` was empty.
The empty source checkout stayed unchanged; the host allocated a separate linked
parent worktree. No real owner project or PAO source checkout was used as the
model workspace.

Both verifier profiles were serialized, reloaded, and tested through the existing
DeterministicVerifier before inference. Each accepted exact semantic JSON and
formatted JSON, and rejected wrong values, extra keys, invalid JSON, and an
additional unapproved file. Parent verification also requires the actual boolean
`true`, not numeric equality. The profiles were never weakened:

- Child SHA-256: `8ab340b77099b18d58db40720cbe513d0a627c5767ccb1a3c3abf423f2faacae`.
- Parent SHA-256: `ca28e8a1373b855f579240232f3d24b33bc2e95d4b56220ce34c1e884cec6db1`.

The acceptance harness used normal PAO executor configurations with separate
frozen parent/child verifier profiles and the same durable store. The child port
retained DispatchRecommendationService and RuntimeDispatchExecutor. Its registry
and prior execution evidence were copied from sanitized prior acceptance data;
no execution probe or fabricated qualification was added. Only the existing
read-only collector refreshed the explicitly authorized authenticated MiniMax
provider. No owner connection import occurred.

The MiniMax commercial pool was AVAILABLE at 02:42:25 UTC and again at 02:47:35
UTC: 5-hour remaining 97%, weekly remaining 35%. The preflight found no shared
pool blocker. These are time-stamped observations, not future quota guarantees.

Observed activation order, UTC:

1. Parent process spawned; broker socket existed with inactive session while task
   was READY (02:48:08.668626).
2. Parent task and run both observed durably RUNNING (02:48:08.670378).
3. Broker activation completed (02:48:08.670514).
4. Parent exited normally with no delegation call (02:48:14.810631).
5. Frozen verifier failed; parent BLOCKED and cleanup completed (02:48:14.895290).

## Validation and retained evidence

No broad pytest, Swift, OpenCode, or full Ruff suite was rerun. The existing
[exact code-head CI](https://github.com/louisjia2008-ux/personal-ai-orchestrator/actions/runs/34730955124)
remains the baseline. This attempt ran only 12 offline verifier self-tests,
read-only metadata/quota checks, real state/evidence checks, artifact sanitization,
and diff hygiene.

- [Real run, lifecycle, durable states, and cleanup](pi5b2-2026-09-13/acceptance-result.json)
- [Verifier self-tests and frozen hashes](pi5b2-2026-09-13/offline-preflight.json)
- [Child verifier](pi5b2-2026-09-13/child-verifier.json) and [parent verifier](pi5b2-2026-09-13/parent-verifier.json)
- [Read-only quota observation](pi5b2-2026-09-13/quota-preflight.json)
- [Host preflight and candidate admission](pi5b2-2026-09-13/host-preflight.json)
- [Sanitized parent explanation](pi5b2-2026-09-13/parent-explanation.json)
- [Verifier outcome and installed Pi source fingerprints](pi5b2-2026-09-13/diagnosis.json)
