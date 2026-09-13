# PI 5B2 final real Mac delegation acceptance

FINAL_STATUS: **PI_5B2_REAL_MAC_DELEGATION_ACCEPTANCE_COMPLETE**

Acceptance baseline: `0bb89a9f6c4fc90cd7854b301f0a9ce780d28f4b`.
Branch: `feat/pi-delegation-execution-05b2`. PR #36 remains OPEN / DRAFT.
Do not merge. This report records a new explicitly authorized attempt; it does
not replace or reinterpret either previous blocked report.

## Result and authorized budget

One real Pi 0.85.1 MiniMax M3 parent called the actual model-visible
`pao_delegate` tool. The production socket broker created a host-bound child;
host BALANCED recommendation resolved the canonical persisted quota pool and
selected MiniMax M3. One real child ran in its own worktree, passed its frozen
verifier, and reached VERIFIED. The parent received the production tool result,
continued with its own file, passed its frozen verifier, and reached VERIFIED.

Exactly one parent attempt and one child attempt created two real worker
processes. There was no second attempt, fallback, grandchild, reviewer worker,
production source repair, or post-run model request. Session retries and provider
retries were disabled; automatic compaction was disabled in this fixture's
dedicated Pi settings. An exclusive run marker prevents reusing this attempt.

## Real runtime gates

| Field | Directly observed result |
| --- | --- |
| PI_VERSION | 0.85.1 |
| PARENT_TARGET | pi-minimax-cn-coding-plan-MiniMax-M3 |
| PARENT_RUNTIME / PROVIDER / MODEL | pi / pi-json; minimax-cn; MiniMax-M3 |
| REAL_PARENT_ATTEMPTS / PROCESSES | 1 / 1; PID 63303 |
| PARENT_TASK_ID | pi5b2-final-parent |
| PARENT_RUN_ID | run-owner-dispatch-pi5b2-final-parent-dispatch |
| PARENT_WRITER | Actual acquired writer fingerprint `5913d3bb97828663`; subsequently released |
| PARENT_QUOTA_POOL_ID | minimax-token-plan-cn |
| PARENT_TOOLS | read,edit,write,grep,find,ls,pao_delegate |
| DELEGATION_REQUEST_COUNT | 1 |
| DELEGATION_REQUEST_ID / ORDINAL | chatcmpl-tool-93dd6ef277c096d6 / 1 |
| DELEGATION_TOOL_CALL_REAL | YES; real Pi tool event matches broker response ID |
| BROKER_ADMISSION | PASS; COMPLETED / CHILD_EXECUTION_FINISHED / verified=true |
| HOST_CHILD_TARGET_SELECTION | EXECUTED; BALANCED; selected by host policy |
| CHILD_TASK_ID | pi5-child-fde92b4ef8466950ec06 |
| CHILD_AUTHORITY | DELEGATED_CHILD; host parent/run lineage; depth 1 |
| CHILD_TARGET | pi-minimax-cn-coding-plan-MiniMax-M3 |
| CHILD_RUNTIME / PROVIDER / MODEL | pi / pi-json; minimax-cn; MiniMax-M3 |
| REAL_CHILD_ATTEMPTS / PROCESSES | 1 / 1; PID 63332 |
| CHILD_RUN_ID | run-owner-dispatch-pi5-child-dispatch-pi5-child-fde92b4ef8466950ec06 |
| CHILD_WRITER | Actual acquired writer fingerprint `2d557c4414834c8f`; subsequently released |
| WORKTREES_DISTINCT | PASS; neither worktree is fixture main |
| WRITERS_DISTINCT / SINGLE_WRITER_INVARIANT | PASS; two distinct held writers for two distinct task workspaces |
| CHILD_DELEGATION_DISABLED | PASS; runtime flag false, only guard extension, actual tool list excludes pao_delegate |
| GRANDCHILD_COUNT | 0 |
| CHILD_VERIFIER / FINAL_STATE | PASS / VERIFIED |
| PARENT_STATE_WHEN_CHILD_CREATED | RUNNING; child SUBMITTED during host recommendation |
| PARENT_STATE_WHEN_CHILD_RUNNING | RUNNING; observed with both writers held |
| PARENT_STATE_WHEN_CHILD_VERIFIED | RUNNING; observed when production child port returned verified result |
| PARENT_VERIFIER / FINAL_STATE | PASS / VERIFIED |
| AUTO_FALLBACK | NO |
| SOURCE_CODE_CHANGED_DURING_ACCEPTANCE | NO |

Writer fingerprints are hashes of the actual persisted lock values; the observer
also compared the underlying values for inequality while both were held. No
writer authority value or credential contents are included in this report.

## Canonical quota identity: real cold-process evidence

The read-only preflight and real execution were separate Python processes.
Preflight collected and persisted the observation. The execution process created
a new `QuotaRefreshService` and never refreshed that service or injected an alias.
Its `_observed_pool_id` was empty before launch and during child recommendation.
Parent and child startup admission separately retained their production collector
checks; these do not populate the refresh service's process-local mapping.

At real child recommendation:

```text
pi-minimax-cn-coding-plan-MiniMax-M3
  -> runtime_quota_routing.quota_pool_id_for_target
  -> QUOTA_SOURCES["minimax-cn-coding-plan"]
  -> minimax-token-plan-cn
  -> persisted pool-addressed snapshot
```

| Quota field | Runtime evidence |
| --- | --- |
| CHILD_RECOMMENDATION_QUOTA_POOL_ID | minimax-token-plan-cn |
| CHILD_RECOMMENDATION_OBSERVATION_FOUND | YES |
| CHILD_RECOMMENDATION_OBSERVATION_SOURCE | PERSISTED_POOL_CACHE; normalized PROVIDER_API observation |
| QUOTA_OBSERVATION_STATE | AVAILABLE |
| QUOTA_OBSERVATION_CONFIDENCE | EXACT |
| QUOTA_OBSERVATION_AGE_SECONDS | 45.474382 |
| OBSERVATION_TIME | 2026-09-13T06:00:47.894190+00:00 |
| SNAPSHOT_ID | quota-a679f5bc-6aea-48fa-8526-c0e78e8bb35f |
| CACHE_SHA256 | ca50fe7ef7b7c4db76f67dfd09fdd363bd5e1b7adbce4a919e1a629b91b93541 |
| REMAINING_FRACTIONS | five-hour 0.91; weekly 0.34 |
| EPHEMERAL_OBSERVED_POOL_REQUIRED | NO; execution-process mapping entries=0 |
| SHARED_QUOTA_GATE | PASS for parent and child against minimax-token-plan-cn |
| CHILD_QUOTA_POOL_ID | minimax-token-plan-cn |
| SHARED_POOL | YES |

The read-only observer recorded the actual pool-cache calls made by production
recommendation and associated their snapshot IDs with the canonical target
resolution. It did not supply a replacement observation or override the result.
The cached bytes matched the recorded SHA-256 after execution. There was one
current pool snapshot, not independent balances per target. Both launch-time
admission records report AVAILABLE_OBSERVED / ESTIMATED, which is the separate
availability-state contract; the underlying recommendation observation remained
AVAILABLE / EXACT. No UNKNOWN state was promoted to fabricate eligibility.

The prompt contained no child target, provider, model, or runtime selection.
The host evaluated all three registered PI targets. M3 was admitted; M2.7 and
M2.7-highspeed were excluded for absent execution verification. No fallback ran.

## Ordering and real parent continuation

UTC on 2026-09-13, from host events and persisted lifecycle records:

1. 06:01:28.351740 — real parent PID 63303 spawned; its explicit tool list
   included pao_delegate. The broker was inactive until durable RUNNING.
2. 06:01:28.352691 — parent entered RUNNING.
3. 06:01:33.365081 — production broker delivered the depth-1 child plan.
4. 06:01:33.371257 — host selected M3 while parent remained RUNNING and child
   was SUBMITTED; canonical quota observation age was 45.474382 seconds.
5. 06:01:33.579247 — real child PID 63332 spawned without pao_delegate.
6. 06:01:33.580915 — actual simultaneous writer/worktree isolation observed;
   parent remained RUNNING.
7. 06:01:37.727292 — child entered WORKER_FINISHED, then VERIFYING.
8. 06:01:37.781353 — child entered VERIFIED after the frozen verifier passed.
9. 06:01:37.824776 — production child port returned VERIFIED / true while
   parent remained RUNNING.
10. 06:01:41.833404 — parent entered WORKER_FINISHED, then VERIFYING.
11. 06:01:41.886838 — parent entered VERIFIED after its frozen verifier passed.

The captured real parent protocol gives independent continuation ordering:
its pao_delegate start is stream line 136; its matching tool-result receipt at
line 137 contains `{"child_state":"VERIFIED","verified":true}`; its own write
starts at line 180. Tool metadata was extracted after process exit, so extraction
timestamps are not substituted for emission timestamps. Matching request IDs,
stream positions, live host events, and SQLite transitions establish the order.
The harness did not call broker internals, synthesize requests, pre-create a
child, or inject a result. Its observer forwarded the production child plan and
returned the production child result unchanged.

## New disposable fixture and frozen verification

Fixture main:
`/Volumes/Taoruide外接/AI LLM TOOLS/pi5b2-real-acceptance-20260913-final/fixture`

Initial and final fixture HEAD: `0431845631c193ef9c1a63108cc2a954d44ea827`.
Initial and final fixture status: empty / clean. The initial commit was empty:
no user project data, dependencies, credentials, or network work were required.

Parent worktree:
`/Volumes/Taoruide外接/AI LLM TOOLS/pi5b2-real-acceptance-20260913-final/worktrees/pi5b2-final-parent`

Child worktree:
`/Volumes/Taoruide外接/AI LLM TOOLS/pi5b2-real-acceptance-20260913-final/worktrees/pi5-child-fde92b4ef8466950ec06`

Both worktrees derive from the fixture base and are distinct from fixture main
and the PAO source checkout. Parent changed only `pi5_parent_result.json`;
child changed only `pi5_child_result.json`. The host guard confined file tools
to each worktree; bash was not enabled. The child had only the guard extension,
not the delegation extension, with delegation disabled and DELEGATED_CHILD
authority. These were observed runtime settings, not inferred from non-use.

Verifier profiles were copied byte-for-byte from the previous frozen profiles:

- Child SHA-256: `8ab340b77099b18d58db40720cbe513d0a627c5767ccb1a3c3abf423f2faacae`.
- Parent SHA-256: `ca28e8a1373b855f579240232f3d24b33bc2e95d4b56220ce34c1e884cec6db1`.

Each real verifier ran `git diff --check <fixture-base> --` and the frozen Python
semantic-JSON command; all four stages returned 0. Child evidence ID:
`verify-4ce5d0196a8a96be218ce52e`. Parent evidence ID:
`verify-b71ae3a20425c2032cd19f79`. Both had no unexpected paths.

Child artifact: `{"status":"PI5_CHILD_VERIFIED"}`.
Parent artifact: `{"status":"PI5_PARENT_VERIFIED","child_verified":true}`.
The parent did not read the child worktree; its responsibility depended on the
actual verified broker result. The prior parent verifier was not weakened.

## Cleanup, validation, and limits of counting

All 53 deterministic post-run checks passed. Counts are zero for running tasks,
active runs, retained writer locks, nonterminal tasks, orphan processes,
temporary broker sockets, and fixture Git locks. The supervisor retained no
owned PID. Exact PID/PPID/process-group checks found no surviving run-owned
processes. No broad process termination was used. PAO has no separate task-lock
table: task execution ownership is represented by workspace writer locks and
active task/run state, all cleared here.

The run's temporary auth symlink and dedicated Pi settings directory were
removed without reading/copying credential contents into evidence. The 12
offline verifier self-test repositories were removed. The disposable main,
result worktrees, local state DB, and sanitized records are retained for review;
they carry no active execution ownership. Raw protocol/state DB files are not
committed. Source HEAD/status before and after workers were identical; all
committed changes were written only after both workers had ended.

Model counting: two real worker processes emitted five completed assistant
response events (parent 3, child 2). The captured protocol does not expose a
transport-level provider HTTP request counter; that count remains UNKNOWN.
Retries were disabled. Multiple conversational turns within these two workers
are not additional worker attempts. No further provider/model request was made
after execution ended.

Validation includes the 12 frozen verifier self-tests, four successful real
verifier stages, 53 post-run consistency/cleanup checks, JSON parsing, the
existing `assert_sanitized` checker, credential-shape scanning, changed-file
scope inspection, and `git diff --check`. No broad local suite was rerun.
[Acceptance baseline CI passed](https://github.com/louisjia2008-ux/personal-ai-orchestrator/actions/runs/34740573653).
Evidence-head CI is reported on PR #36 after this evidence-only commit.

## Evidence

- [Real processes, quota reads, broker response, tool ordering, and task results](pi5b2-2026-09-13-final/acceptance-result.json)
- [Persisted quota snapshot; bytes match the runtime read](pi5b2-2026-09-13-final/quota-snapshot.json)
- [Actual verifier stages, persisted lifecycle, ownership and 53 checks](pi5b2-2026-09-13-final/postrun-checks.json)
- [Frozen parent verifier](pi5b2-2026-09-13-final/parent-verifier.json)
- [Frozen child verifier](pi5b2-2026-09-13-final/child-verifier.json)
- [New fixture and 12 offline verifier self-tests](pi5b2-2026-09-13-final/offline-preflight.json)
- [Separate-process read-only quota preflight](pi5b2-2026-09-13-final/host-preflight.json)

Next action: review this successful real acceptance and retain PR #36 OPEN /
DRAFT. No merge, production enablement, or further worker run is authorized by
this completed acceptance.
