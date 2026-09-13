# PI-5B2.1 delegation tool activation fix

Status: PI_5B2_1_TOOL_ACTIVATION_FIX_COMPLETE_REAL_ACCEPTANCE_PENDING

Baseline: `db5012887999a473222722da5a9de793322fc850` on PR #36,
`feat/pi-delegation-execution-05b2`. The prior real Mac acceptance remains
BLOCKED_TOOL_ALLOWLIST. This fix does not replace or reinterpret that evidence.

## Verified cause and narrow implementation

The prior parent argv explicitly loaded the trusted PI-5 extension but kept
`--tools read,edit,write,grep,find,ls`. Pi 0.85.1 applies this list to registered
custom tools as well. The evidence records zero delegation calls and the parent
reporting the tool unavailable. The previous synthetic Node E2E called the tool
directly, and a Python test incorrectly expected the enabled/off lists to match.

`should_pao_delegate_be_model_visible` is the canonical host policy decision:
the host flag must be enabled, dispatch must have recognized parent authority,
and the durable submission must not identify a delegated child. A child remains
ineligible even if subsequently retried through owner authority. This decision
controls both explicit extension seeding and the effective runtime flag.

The command builder appends exactly one `pao_delegate` to the ordinary six tools
only for that authorized parent. The disabled invocation remains byte-for-byte
unchanged. `--no-extensions` and the explicit worktree guard remain in place;
bash, powershell, and webfetch remain excluded.

Before invoking the process supervisor, the Pi adapter checks that the seeded
extension exists and exactly matches the host-rendered socket client, including
its host-generated socket path, and that the broker socket exists. It checks the
final generated argv's tool list and extension reference. Conflicting enabled
`extra_args` tool/extension controls are rejected instead of overriding policy.

A failed check raises `PiDelegationActivationError`, records a durable
`PI_DELEGATION_ACTIVATION_UNAVAILABLE` audit event with a fixed `reason_code`,
and follows existing pre-worker failure/cleanup handling. No worker or run row
is created. This covers missing dependencies, missing/tampered client source,
wrong socket binding, conflicting configuration, and missing model visibility.
The ordinary dispatch failure remains `WORKER_SPAWN_FAILED`; the typed activation
diagnostic is retained separately in the task audit.

## Offline evidence and security scope

Local focused validation: 76 tests passed across activation, PI-5 contract,
broker, child execution/verifier, lifecycle, and existing Pi runtime/adapter
tests; 12 additional byte-compatibility/authority-schema tests passed. Total:
**88 passed, 0 failed**. Changed-file Ruff and diff hygiene passed. No broad
Python, Swift, or OpenCode suite was manually rerun.

The installed Pi 0.85.1 SDK regression parses the generated argv and loads the
actual trusted extension using an isolated resource loader. It uses in-memory
auth storage, disables model catalog refresh, blocks network access, and never
submits a prompt or invokes the tool. It checks the actual session's active tools:

- disabled: the six original tools;
- authorized parent: the six original tools plus one `pao_delegate`;
- extension installed but not authorized: the six original tools;
- model calls: 0.

CI installs the pinned Pi SDK version and supplies `PAO_PI_SDK_ROOT` to this
regression. Local environments without that explicit SDK path skip only this
installed-runtime test; all fake-worker and policy tests remain available.

Negative activation tests assert zero supervisor starts, zero run rows, BLOCKED
task state, writer release, socket/temp-root cleanup, and a structured diagnostic.
Existing fake-child tests exercise a globally enabled parent setting and prove
the child receives neither the extension nor `pao_delegate` in its tool list.

The request schema and broker were not changed. Extra fields for executable,
provider credential, repo/worktree path, verifier command, shell command,
nesting depth, provider/model/runtime, and target are rejected. Free-text intent
requests do not gain host execution authority. Target selection, quota admission,
child creation, worktree/writer ownership, verifier commands, and final state
remain host-owned. Socket activation still waits for durable parent RUNNING;
visibility alone cannot bypass broker admission. The socket client's emitted
source is unchanged by extracting its renderer for preflight comparison.

No auth-file contents or credentials were inspected. The staged patch was scanned
for credential-shaped literals; no secrets or unrelated generated files were
included. No real parent or child worker ran in this fix task.

## Next real acceptance requires explicit authorization

REAL_ACCEPTANCE_STATUS: NOT_RERUN_REQUIRES_EXPLICIT_WORKER_BUDGET_AUTHORIZATION

PR #36 must remain DRAFT and must not be merged. A subsequent real acceptance
needs a new explicit worker budget (normally at most one parent and one child),
fresh read-only quota admission, a disposable fixture, frozen semantic verifiers,
and no automatic retries. Offline tests do not establish any of these real gates:

1. DELEGATION_REQUEST_COUNT >= 1; a durable CHILD_TASK_ID is created.
2. HOST_CHILD_TARGET_SELECTION executes; admitted CHILD_TARGET is host-selected,
   with CHILD_AUTHORITY=DELEGATED_CHILD and legal source state READY.
3. REAL_CHILD_WORKER_PROCESSES >= 1; parent/child worktrees and writer identities
   are distinct, and the single-writer invariant is maintained.
4. CHILD_DELEGATION_DISABLED=PASS; GRANDCHILD_COUNT=0.
5. SHARED_QUOTA_GATE=PASS at actual child admission; no target fallback.
6. CHILD_VERIFIER=PASS and CHILD_FINAL_STATE=VERIFIED.
7. Parent remains in a legitimate non-terminal state while the child is processed;
   child success does not set parent completion.
8. Parent independently obtains PARENT_VERIFIER=PASS and PARENT_FINAL_STATE=VERIFIED.
9. AUTO_FALLBACK=NO; BROKER_CLEANUP=PASS; writers released; RUNNING_ROWS=0;
   active run rows=0; ORPHAN_PROCESSES=0; fixture main HEAD/status unchanged.
