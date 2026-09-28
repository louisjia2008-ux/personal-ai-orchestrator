# PI-5B2 real Mac delegation acceptance after activation fix

FINAL_STATUS: **PI_5B2_REAL_MAC_DELEGATION_ACCEPTANCE_BLOCKED_CHILD_QUOTA_OBSERVATION_MISSING**

Source baseline: `37e5ff0e8db5988c9ef11b15a5f52532c66d1bcd`.
PR #36 remains OPEN / DRAFT. Do not merge.

The single authorized attempt ran one real Pi parent, which called the actual
model-visible `pao_delegate` tool exactly once. The host broker accepted the
request and created a durable child task. Host target selection then rejected
all candidates: M3 lacked a quota observation in the recommendation service;
the other candidates lacked execution verification. No child worker started.
Both tasks ended BLOCKED. No retry, fallback, production fix, or further model
call was attempted by the acceptance harness after this failure.

## Direct runtime evidence

| Field | Observed result |
| --- | --- |
| PI_VERSION | 0.85.1 |
| PARENT_TARGET | pi-minimax-cn-coding-plan-MiniMax-M3 |
| PARENT_RUNTIME / PROVIDER / MODEL | pi / pi-json; minimax-cn; MiniMax-M3 |
| REAL_PARENT_ATTEMPTS / PROCESSES | 1 / 1; PID 39379 |
| PARENT_TASK_ID | pi5b2-activation-parent |
| PARENT_RUN_ID | run-owner-dispatch-pi5b2-activation-parent-dispatch |
| PARENT_WRITER | Acquired; fingerprint f104caa6e42c3ff0; released after completion |
| PARENT_TOOLS | read,edit,write,grep,find,ls,pao_delegate |
| EXTENSIONS | Host worktree guard and explicit host socket tool; discovery disabled |
| DELEGATION_REQUEST_COUNT | 1 |
| DELEGATION_REQUEST_ID | call_01a0991678e97403aa1fdd0c; ordinal 1 |
| DELEGATION_TOOL_CALL_REAL | YES; Pi tool_execution_start metadata correlates to broker response ID |
| BROKER_ADMISSION | Request accepted and child plan/task created; execution admission blocked |
| HOST_CHILD_TARGET_SELECTION | EXECUTED; BALANCED; top_pick=null |
| SHARED_QUOTA_GATE | Parent PASS; child launch gate NOT_REACHED because recommendation failed closed |
| CHILD_TASK_ID | pi5-child-e9e1b0c371f1b07e7e46 |
| CHILD_AUTHORITY | Host-bound delegated parent lineage and depth-1 plan valid; DELEGATED_CHILD dispatch authority NOT_CREATED |
| CHILD_TARGET / RUNTIME / PROVIDER / MODEL | NOT_SELECTED / NOT_EXECUTED / NOT_EXECUTED / NOT_EXECUTED |
| REAL_CHILD_ATTEMPTS / PROCESSES | 0 / 0 |
| CHILD_RUN_ID / WORKTREE / WRITER | NOT_CREATED / NOT_CREATED / NOT_ACQUIRED |
| WORKTREES_DISTINCT / WRITERS_DISTINCT | NOT_EXERCISED; no child workspace exists |
| CHILD_DELEGATION_DISABLED | Plan delegation_allowed=false; actual child runtime NOT_EXERCISED |
| GRANDCHILD_COUNT | 0; exactly two durable tasks |
| CHILD_VERIFIER / FINAL_STATE | NOT_EXECUTED / BLOCKED |
| PARENT_STATE_WHEN_CHILD_CREATED | RUNNING, observed while child was SUBMITTED |
| PARENT_STATE_WHEN_CHILD_RUNNING | NOT_APPLICABLE; child never ran |
| PARENT_STATE_WHEN_CHILD_VERIFIED | NOT_APPLICABLE; child was not verified |
| PARENT_STATE_WHEN_CHILD_BLOCKED | RUNNING; broker returned verified=false |
| PARENT_VERIFIER / FINAL_STATE | FAIL, missing required parent JSON / BLOCKED |
| AUTO_FALLBACK | NO |
| BROKER_CLEANUP | PASS; socket and private directory removed |
| RUNNING_ROWS / ACTIVE_RUN_ROWS / ORPHAN_PROCESSES | 0 / 0 / 0 |
| WRITER / TASK LOCK CLEANUP | No retained writer tokens, nonterminal tasks, active supervisor PIDs, or fixture Git locks |
| MODEL_CALL_COUNT | UNKNOWN at provider-request level; 2 assistant message_end events observed in the one parent process |
| SOURCE_CODE_CHANGED_DURING_ACCEPTANCE | NO |

The parent only called `pao_delegate`; it made no read/edit/write/shell calls.
Its protocol was valid, with one tool start/end and zero extension errors.
It did not write a false successful artifact after the broker returned BLOCKED.
The authoritative parent verifier ran, failed, and prevented VERIFIED.
No child verifier or runtime-isolation success can be inferred from this run.

## Request and lifecycle ordering

UTC on 2026-09-13:

1. 04:46:19.302522 — fresh parent quota admission passed with
   AVAILABLE_OBSERVED / ESTIMATED evidence.
2. 04:46:19.307786 — real parent PID 39379 spawned with `pao_delegate` in argv.
3. 04:46:19.309059 — parent task and run durably RUNNING; broker activated after
   this transition. Socket permissions were 0600; directory permissions 0700.
4. 04:46:22.144060 — the socket-driven broker delivered its host child plan.
5. 04:46:22.146956 — durable child existed in SUBMITTED while parent remained
   RUNNING; host recommendation returned no admitted target.
6. 04:46:22.147829 — child returned BLOCKED / verified=false while parent stayed
   RUNNING. The broker's stored response has the same call ID and ordinal as the
   real parent tool call.
7. 04:46:23.647054 — parent exited normally; subsequently the frozen verifier
   rejected the missing result file, and host final state became BLOCKED.

The requested intent was to create only `pi5_child_result.json` with exact
semantic JSON `{"status":"PI5_CHILD_VERIFIED"}`. The actual model arguments and
reason are retained in the sanitized run evidence. They did not contain target,
provider credential, executable, repository, worktree, verifier, nesting, or shell
authority fields. The host minted child identity and inherited project/base/policy.

The Pi tool metadata was extracted from the captured protocol after process exit,
so its `observed_at` is extraction time, not the instant the request was emitted.
The host child-plan receipt timestamp provides the live broker-processing bound;
`stream_order=1`, ordinal 1, and matching call IDs establish correlation.
The harness never synthesized a request, called `broker.session.handle`, or
injected a child result. Its child-port observer forwarded only the plan delivered
by the normal socket broker, without changing its intent or result.

## Blocker diagnosis; no fix performed

Read-only inspection found a quota pool identity mismatch in the shipped wiring:

- `default_quota_collectors()` constructs the MiniMax collector with its default
  snapshot pool `minimax-token-plan-cn`.
- The quota source table resolves `minimax-cn-coding-plan` to
  `minimax-coding-plan-cn`.
- `QuotaRefreshService` can bridge this disagreement using its in-memory
  `_observed_pool_id` map after a refresh. That map does not survive a process
  boundary.
- The preflight process refreshed and could see the snapshot. The new real-run
  process read the table pool, missed the persisted snapshot, and had no fallback
  mapping. An offline postrun read reproduced snapshot_present=false while the
  snapshot remained on disk under `minimax-token-plan-cn`.
- Parent admission independently refreshed through the direct collector and
  wrote quota-availability evidence. Recommendation headroom windows come from
  quota refresh observations, so that parent admission record did not provide
  the child scheduler with the missing observation.

Thus the scheduler correctly failed closed with `no quota observation reported
for this target`. This is not evidence of actual provider exhaustion: preflight
reported 93% five-hour / 35% weekly remaining, and parent admission passed.
Those observations are timestamped and must not authorize a future run.
No production code, quota rules, verifier semantics, or fallback behavior was
changed. The mismatch requires a separately scoped deterministic repair/review;
this attempt provides no authorization to repair and rerun.

## Disposable fixture and frozen verification

Fixture main:
`/tmp/pao-acceptance/pi5b2-real-acceptance-20260913-activation/fixture`

Initial and final main HEAD:
`e5086f9ba857e017346dc1abb71433d129603390`.
Initial and final main status: empty / clean.

Parent worktree:
`/tmp/pao-acceptance/pi5b2-real-acceptance-20260913-activation/worktrees/pi5b2-activation-parent`

The worktree is separate from fixture main and PAO source. It also remained
clean. Child worktree was not created. PAO source HEAD/status were identical
before and after the worker; only this documentation was added afterward.

The parent must delegate exactly once, consume the actual verified child result,
and only then write `pi5_parent_result.json` with semantic JSON
`{"status":"PI5_PARENT_VERIFIED","child_verified":true}`. Parent and child
profiles allow only their respective result filename. Whitespace is irrelevant;
wrong values, extra keys, malformed JSON, and additional paths fail.

Profiles match the prior frozen hashes:

- Child: `8ab340b77099b18d58db40720cbe513d0a627c5767ccb1a3c3abf423f2faacae`.
- Parent: `ca28e8a1373b855f579240232f3d24b33bc2e95d4b56220ce34c1e884cec6db1`.

The exact host verifier commands and real return codes are in diagnosis.json
and the two profiles. Parent semantic command returned 1; diff hygiene returned
0. There was no real child verification.

## Budget, cleanup, and evidence validation

An exclusive run marker and supervisor limits enforced at most one parent and
one child process. This authorization is consumed: no unused child capacity
permits a second attempt. Session retry was disabled, provider maxRetries=0,
and automatic compaction was disabled in this fixture's dedicated Pi agent
settings; no persistent owner settings were changed. Existing Pi credentials
were accessed internally via a temporary auth symlink; no auth contents were
copied into evidence or displayed. The symlink and dedicated runtime directory
were removed after exit.

Exact PID, PPID, and process-group checks found no remaining processes belonging
to parent PID 39379. No broad killall/pkill was used. Broker resources and the 12
offline self-test fixture repositories were removed. The disposable main repo,
clean parent worktree, local state DB, and sanitized evidence remain for review;
they hold no active execution or ownership. Raw worker transcripts and the local
state DB are not committed.

Validation: 12 frozen verifier self-tests passed before launch; 26 deterministic
postrun consistency/cleanup checks passed. These checks validate the recorded
BLOCKED result, not successful real acceptance. JSON parsing, the existing
`assert_sanitized` helper, credential-literal scanning, and `git diff --check`
were used. No broad local test suite or provider/model call was run afterward.

[Accepted source-head CI](https://github.com/louisjia2008-ux/personal-ai-orchestrator/actions/runs/34736110177)
was green; it does not override this real runtime blocker.

Evidence:

- [Run, real tool call, broker response, task lifecycle](pi5b2-2026-09-13-activation/acceptance-result.json)
- [Offline fixture and verifier self-tests](pi5b2-2026-09-13-activation/offline-preflight.json)
- [Live read-only preflight](pi5b2-2026-09-13-activation/host-preflight.json)
- [Parent verifier](pi5b2-2026-09-13-activation/parent-verifier.json)
- [Child verifier](pi5b2-2026-09-13-activation/child-verifier.json)
- [Quota mismatch and actual verifier outcomes](pi5b2-2026-09-13-activation/diagnosis.json)
- [Postrun consistency and cleanup checks](pi5b2-2026-09-13-activation/postrun-checks.json)

Next action: review and separately authorize a narrow quota observation identity
repair with offline regression coverage. A subsequent real attempt requires a
new explicit parent/child worker budget. PR #36 must remain DRAFT.
