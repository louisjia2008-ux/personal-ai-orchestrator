# Local-client v2: explicit manual execution

This additive owner-only contract is served at `POST /v2/local-client` on the
existing permission-restricted Unix socket. The JSON CLI command remains
`pao-client-bridge deskpet --installation-id <local-id>`; an explicit
`protocol_version` selects v2 and never falls back to a v1 mutation.
Legacy task operations, Telegram approval behavior, and targetless MANUAL tasks
created by an AUTO veto retain their previous meaning. Policy PUTs now require
observed-generation preconditions; see [Shared policy v1](SHARED_POLICY_V1.md).

This slice is an execution capability, not a guarantee of eligibility or success.
It does not enable Production ACTIVE, change owner settings, tick the scheduler,
resolve approvals, grant worker permissions, bypass quota admission, retry an
attempt, weaken writer ownership, or clear cleanup quarantine. Owner-initiated
execution remains separate from Production ACTIVE. Existing host gates run.

## Request envelope and exact shapes

Every request has `protocol_version: 2`, `operation`, and `request_id`.
Only the operation-specific fields below are accepted. Extra fields, including
explicit null placeholders, are rejected except the two explicitly nullable
PROJECT_POLICY_SET fields described in [Shared policy v1](SHARED_POLICY_V1.md). IDs match
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. Identity values are 32 lowercase hexadecimal
characters. Versions are nonnegative integers; intent is 1–8192 characters.

- `CAPABILITIES`: no additional fields
- `CONTEXT`: no additional fields; same payload as CAPABILITIES
- `SUBMIT_V2`: `task_id`, `project_id`, `intent`, `execution_target_id`,
  `expected_store_id`, `expected_process_epoch`
- `START`: `task_id`, `task_state_version`, `execution_target_id`,
  `expected_store_id`, `expected_process_epoch`
- `DETAIL`: `task_id`
- `CONTINUATION`: `task_id`; read-only, manual copy/export of a safe summary
- `DISPATCH_STATUS`: the original START's `request_id`, `task_id`,
  `task_state_version`, `execution_target_id`, `expected_store_id`,
  `expected_process_epoch`
- `CANCEL`: `task_id`, `expected_store_id`, `expected_process_epoch`

Example explicit submission (identity placeholders must be replaced by the
values observed from CONTEXT):

```json
{
  "protocol_version": 2,
  "operation": "SUBMIT_V2",
  "request_id": "submit-example-1",
  "task_id": "task-example-1",
  "project_id": "project-example",
  "intent": "Fix the requested button behavior",
  "execution_target_id": "target-example",
  "expected_store_id": "11111111111111111111111111111111",
  "expected_process_epoch": "22222222222222222222222222222222"
}
```

Submission pins MANUAL and the explicit registered target. It does not start a
worker. Initial state is **SUBMITTED**, version **0**. START is valid from
SUBMITTED or READY, subject to existing host gates. The existing initiator
transitions SUBMITTED to READY when it reserves and validates a dispatch.

```json
{
  "protocol_version": 2,
  "operation": "START",
  "request_id": "start-example-1",
  "task_id": "task-example-1",
  "task_state_version": 0,
  "execution_target_id": "target-example",
  "expected_store_id": "11111111111111111111111111111111",
  "expected_process_epoch": "22222222222222222222222222222222"
}
```

The immutable START tuple must be persisted before sending and never regenerated
because a response is lost. Reconciliation changes only `operation` to
`DISPATCH_STATUS`. It never launches or retries a worker. A daemon-side duplicate
START retains the existing idempotent dispatch semantics; this is not a client
retry policy. A response's current `task.state_version` never replaces the
original dispatch's `task_state_version`.

## Response envelope

Every successful response has exactly:

```json
{
  "protocol_version": 2,
  "request_id": "read-example",
  "operation": "CONTEXT",
  "store_id": "11111111111111111111111111111111",
  "process_epoch": "22222222222222222222222222222222",
  "payload": {}
}
```

The store identity is random durable metadata, atomically initialized and stable
across connections/restarts. It conveys no additional authority. The process
epoch changes with each daemon process and is reused across request-local service
instances. Mutations compare both expected identities on the handling host before
reservation or effects. A stale store fails with `store_identity_mismatch`; a
stale mutation epoch fails with `process_epoch_mismatch` (HTTP 409).

DISPATCH_STATUS requires matching store and the exact original dispatch tuple;
an earlier epoch is accepted for read-only observation and the response reports
the current epoch. Earlier-epoch RESERVED/STARTED records have
`recovery_required: true`. An absent record returns `dispatch_not_found` (404).
Neither outcome authorizes resending START. Clients must verify response identity,
request, operation and task/dispatch identity before applying a view; this also
applies to DETAIL, whose request itself is read-only and unbound.

HTTP errors are sanitized `{"error":"code"}` with the HTTP status. The CLI
renders `{"error":"code","status":409}` for host errors and exits nonzero;
local schema/transport errors remain sanitized bridge errors. No free-form
exception or provider error text is part of the v2 response.

## Payload allowlists

CAPABILITIES and CONTEXT return:

```json
{
  "capabilities": [
    "manual-submit-v2", "owner-start-v2", "safe-detail-v2",
    "dispatch-status-v2", "manual-cancel-v2", "host-identity-v2",
    "result-delivery-v1", "manual-continuation-v1"
  ],
  "owner_start": {
    "available": true,
    "owner_enabled": true,
    "executor_available": true,
    "blocking_reasons": []
  },
  "projects": [{"project_id":"project-example","storage_availability":"ONLINE"}],
  "execution_targets": [{
    "execution_target_id":"target-example",
    "runtime_available":true,
    "launch_verified":true
  }],
  "truncated": {"projects":false,"execution_targets":false}
}
```

Catalogs are capped at 100 each. These reads do not discover providers, refresh
projects, or probe project filesystems. Project availability is the last stored
observation, checked afresh by submission/dispatch. Availability values are
ONLINE, OFFLINE, MISSING, INVALID_REPOSITORY. Owner gate reasons are
`owner_initiated_execution_disabled` and `dispatch_executor_unavailable`.
An available owner gate and a listed target are not proof that execution will
pass launch verification, provider connection, quota, permissions or writer gates.

SUBMIT_V2 returns `{"task": SafeTask}`. CANCEL returns
`{"task": SafeTask,"cancelled_now":true}`. CANCEL retains task-level cancellation
and supervisor semantics; it is not an attempt/approval mutation.
V2 START and CANCEL require MANUAL plus a nonempty bound target. Independently,
all host launch boundaries enforce equality whenever a task explicitly binds a
manual target. Legacy targetless-MANUAL dispatch remains valid.

SafeTask contains only `task_id`, `request_id` (original submit request),
`project_id` (nullable), `state`, `state_version`, `scheduling_policy` (nullable),
`manual_execution_target_id` (nullable for observational legacy views),
`created_at`, `updated_at`. It contains no intent, filesystem path, raw result,
worker transcript, credential material or PID.

START and DISPATCH_STATUS return:

```json
{
  "dispatch_id":"owner-dispatch-start-example-1",
  "request_id":"start-example-1",
  "task_id":"task-example-1",
  "task_state_version":0,
  "execution_target_id":"target-example",
  "authority":"OWNER_INITIATED_EXECUTION",
  "status":"RESERVED",
  "accepted":true,
  "failure_code":null,
  "task":{},
  "recovery_required":false
}
```

`task` is SafeTask, with current state/version; dispatch `task_state_version` is
immutable original admission data. Status is RESERVED, STARTED, BLOCKED,
CANCELLED or FINISHED. `accepted` reflects absence of a failure code, not worker
completion or answer quality. Failure codes are bounded uppercase machine tokens;
free-form failure reasons are omitted.
`recovery_required` also remains true when startup reconciliation closed the
dispatch with `STARTUP_RECOVERY_REQUIRED`, including observations in the current
process epoch. This observation never retries the dispatch.

DETAIL is a dedicated bounded SQL read snapshot, not a stripped raw task detail:

```json
{
  "task":{},
  "runs":[{"run_id":"run-example","status":"FINISHED","started_at":"2026-10-06T00:00:00+00:00","finished_at":"2026-10-06T00:00:01+00:00"}],
  "approvals":[{"approval_id":"approval-example","kind":"HIGH_RISK_EXECUTION","status":"PENDING","created_at":"2026-10-06T00:00:00+00:00","resolved_at":null}],
  "verification":{"status":"NOT_OBSERVED","evidence_id":null},
  "answer_completeness":"MISSING",
  "result":{"schema_version":1,"kind":"HOST_VERIFICATION_REPORT"},
  "truncated":{"runs":false,"approvals":false}
}
```

Runs and approvals are newest-first and capped at 50 each. Machine tokens are
uppercase and at most 80 characters; timestamps are bounded ISO-shaped strings
at most 40 characters. `finished_at` and `resolved_at` may be null. The abbreviated
`result` above is expanded below. Verification status is
NOT_OBSERVED, IN_PROGRESS, VERIFIED_EVIDENCE_UNAVAILABLE, VERIFIED,
FAILED_VERIFICATION, or EVIDENCE_CONFLICT. The existing host verification journal
and the same `TASK_STATE_CHANGED` verification transition used by legacy REPORT
remain the source of truth. Legacy REPORT is unchanged. A worker exit zero,
FINISHED dispatch, or VERIFIED task alone never establishes complete delivery.

## Result delivery v1

`DETAIL.payload.result` is a bounded host-verification report, not provider prose
or a claim that every part of the user's goal was completed. It always has:

```json
{
  "schema_version":1,
  "kind":"HOST_VERIFICATION_REPORT",
  "binding":{
    "store_id":"11111111111111111111111111111111",
    "task_id":"task-example",
    "run_id":"run-example",
    "base_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "result_commit":null,
    "submission_event_id":1,
    "verification_event_id":8,
    "verification_evidence_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "task_state":"VERIFIED",
  "goal":"Fix the requested button behavior",
  "goal_redacted":false,
  "completeness":"COMPLETE",
  "verification":{"status":"VERIFIED","evidence_id":"verify-aaaaaaaaaaaaaaaaaaaaaaaa"},
  "changed_files":["src/button.py"],
  "tests":[{"name":"unit tests","status":"PASSED"}],
  "artifacts":[{
    "kind":"VERIFICATION_EVIDENCE",
    "reference":"pao:verification:verify-aaaaaaaaaaaaaaaaaaaaaaaa",
    "sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }],
  "limitations":["RESULT_COMMIT_NOT_RECORDED","HOST_REPORT_ONLY"],
  "truncated":{"goal":false,"changed_files":false,"tests":false},
  "result_id":"result-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

The digests above are illustrative. `result_id` is `result-` plus SHA-256 of the
whole report excluding `result_id`, encoded with Python's canonical JSON form:
sorted keys, separators `(',', ':')`, `ensure_ascii=True`, UTF-8, no trailing
newline. The exact evidence file bytes have a separate SHA-256, attested in the
same durable audit transaction that accepted the passing verification. Delivery
requires that digest to match the journal file; modifying content while retaining
the old evidence ID cannot establish completeness. Older verified records without
this attestation remain MISSING, and legacy REPORT remains available unchanged.

The binding identifies a particular store/task/run/base commit, audit transitions
and evidence content. References are opaque PAO identities, not filesystem paths
or download URLs. Reading computes a projection; it does not create a new result
store. To retain an older snapshot, save the returned report and digest together.
Consumers must compare the expected task/store and recompute the digest before
applying a saved result. A newer observation can legitimately have a different
digest; do not substitute its binding into an older export. These are integrity
references within the host-owned store, not signatures or a trust boundary against
someone who can rewrite both the database and journal.

Completeness values, also returned as `DETAIL.payload.answer_completeness`:

- `MISSING`: no bound, readable host verification report is available. Includes
  running/verifying work, a missing journal/file, absent audit evidence, invalid
  evidence, missing or mismatched digest attestation, or a mismatched
  task/run/base-commit binding.
- `TRUNCATED`: an otherwise valid report has safe-export omissions or exceeds a
  bound; an oversized evidence file is also TRUNCATED, with no asserted pass.
- `FAILED`: task is BLOCKED/FAILED/CANCELLED, verification failed, or the persisted
  evidence conflicts with a passing transition. Failed verification has no
  deliverable artifact reference unless it is explicitly bound by the source
  record; the existing legacy failure transition does not record such a reference.
  Task-level failure is distinct from `FAILED_VERIFICATION`: the latter requires
  a digest-attested verifier outcome for the current run. An interruption while
  VERIFYING or a known main-repository safety block leaves verification
  NOT_OBSERVED, with `VERIFICATION_OUTCOME_NOT_ESTABLISHED` in limitations.
- `COMPLETE`: all bounded fields of a consistent, passing host-verification report
  were delivered. This says nothing about unconfigured tests, native acceptance,
  signing, provider acceptance, merging, publication or fulfillment of arbitrary
  natural-language intent. `tests` are configured host verifier stages, which can
  include hygiene checks. A passed hygiene stage is not a unit-test claim.

Complete delivery requires the task's current VERIFIED/COMPLETED state; a valid
full 40- or 64-hex base commit matching the original submission audit; the latest
run and its matching ordered RUN_STARTED/RUN_FINISHED audits; a subsequent
VERIFYING-to-VERIFIED audit with a valid evidence ID and attested digest; and the
matching journal entry with at least one passing stage, no failed stage and no
unexpected paths. An older pass is never attached to a newer run. `result_commit`
remains null because the source records do not attest a resulting code commit.
No project filesystem or Git command is consulted by this read.

Limits: evidence files at most 1 MiB; 100 changed files, each at most 256
characters; 50 named checks, each name at most 80 characters; goal at most 1024
characters. Audit JSON parsing is capped at 65,536 characters per record, with at
most 200 recent state transitions considered. The reader rejects journal-file
and journal-directory symlinks and non-regular evidence files. Unsafe, absolute,
traversing or credential-like names, controls and suspicious goal lines are
omitted or replaced with fixed redaction text. Missing fields and omissions
remain explicit. Arbitrary verifier argv, stdout, stderr, failure text, run
`result_json`, provider payloads and external session data are never exported.
Scrubbing is conservative, not a general-purpose secret detector; review any
export before manually sharing it outside PAO.

## Manual continuation

`CONTINUATION` has the same request shape and identity-checking requirements as
DETAIL. It returns `{"task": SafeTask, "result": ResultV1, "continuation": SummaryV1}`.
The summary contains `schema_version:1`, `result_id`, the exact result `binding`,
`goal`, `completed`, `pending`, `evidence`, `next_step`, `manual_only:true`, a plain
`text` string for copying, and `summary_id`. The summary ID is `continuation-`
plus the same canonical SHA-256 convention over the summary excluding its ID.
Its text has Goal, Completed, Pending, Evidence and Next step sections.

Only the scrubbed PAO task intent, host-verification facts, immutable references
and fixed guidance enter this export. A failed or missing result states that
review is pending; it does not invent completed work. The operation reads one DB
snapshot, has no side effects, and performs no provider call, launch, retry,
automatic resume or external sharing. A CLI client can copy the returned
`payload.continuation.text` or save the JSON envelope for manual continuation.
No external auth store, hidden Codex sessions or private session data are read.

## Explicit interrupted-task recovery

`manual-recovery-v1` and `recovery-status-v1` advertise two additional operations.
Both require exactly `request_id`, `task_id`, `task_state_version`,
`prior_dispatch_id`, `expected_store_id`, and `expected_process_epoch` alongside
the protocol version and operation. The version is the current interrupted
BLOCKED generation, not the old START source version.

`RECOVER` requires the current host epoch and enabled owner-execution authority.
It rejects a live supervised execution and delegates to the transactional,
proof-gated [recovery contract](OWNER_DISPATCH_RECOVERY_CONTRACT_2026-10-13.md).
It only prepares an eligible explicitly MANUAL task as READY. It does not call
an executor or change policy, target, permissions, ACTIVE authority, writer locks
or cleanup facts. A separate new START must pass every normal admission check.
Unavailable provider/target/quota authority is never bypassed by recovery.

The response is `{"recovery": HistoricalReceipt, "current_task": SafeTask,
"launch_performed": false, "next_step": "REVIEW_CURRENT_TASK_BEFORE_EXPLICIT_START"}`.
The receipt includes the original and recovered task versions, exact previous
dispatch/attempt identity, request/store/epoch, and timestamp. Its recovered
version is historical, even if the current task is later CANCELLED or RUNNING.

`RECOVERY_STATUS` is read-only and accepts the original epoch after a restart.
It compares the complete original tuple and returns the receipt and current
task separately. A missing receipt is `recovery_not_found`; a conflicting tuple
is `conflicting_recovery_request_id`. A lost response never authorizes a new
recovery ID or an automatic START. `DISPATCH_STATUS` keeps the old interrupted
dispatch BLOCKED; it includes `recovery_request_id` after successful recovery,
and no longer presents that historical interruption as recovery-required after
recovery or task cancellation. A recovery-required flag alone is never evidence
that cleanup is confirmed or that recovery admission will pass.

These are host API/CLI contracts. This change does not add a native recovery
button or claim that a Swift client can already display these new fields.

## Validation boundary

The test suite uses file-backed stores, fake executors/workers and a real HTTP
handler exercised over an in-memory accepted connection. Real Unix-socket,
macOS/Electron runtime, provider runtime, signing and release checks are separate
acceptance gates and must not be implied by these tests.

The additive result/continuation host contract is covered by HTTP-handler and CLI
tests without sockets. Native UI integration now consumes the same v2 result/continuation/recovery
contract and versioned policy APIs. Native build, Swift tests and target-Mac
acceptance remain separate required gates; source or host tests alone do not
prove the native interface works on a Mac.

## Shared policy and resources

See [Shared policy and resource contract v1](SHARED_POLICY_V1.md) for the additive
capabilities, exact operation shapes, native HTTP CAS migration, safe snapshots
and durable recovery candidate. These settings capabilities do not broaden
manual execution or approval authority.
