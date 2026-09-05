# Target Mac Acceptance Gate

Status: `PASS_LOCAL_P0_P1_ROUTING_PROVIDER_OWNER_DISPATCH_AND_M0_TRUST_HARDENING`

GitHub CI can prove deterministic cross-platform code behavior, but it cannot prove the target
Mac's provider-native authentication, Keychain boundaries, process cleanup, filesystem isolation,
or behavior across real quota reset cycles. This gate records the remaining acceptance work
without weakening the no-secret-copy rule.

## 1. Freeze the candidate

Use Draft PR #21 / branch `integration/end-to-end-shadow-safety` at one exact commit. Record:

```text
git rev-parse HEAD
sw_vers
python3 --version
node --version
opencode --version
```

Do not run acceptance on `main` and do not merge before evidence is reviewed.

## 2. Reproduce deterministic CI locally

From the candidate checkout:

```text
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
npm --prefix integrations/opencode install
npm --prefix integrations/opencode run typecheck
git diff --check main...HEAD
```

All must pass on the target Mac. A Linux CI PASS is not a substitute for this step.

## 3. P0 disposable-worktree acceptance

Use a newly created disposable Git repository only. Verify:

- task submission is idempotent;
- WorktreeManager creates a task-specific worktree at the requested base SHA;
- the source checkout HEAD/content does not change during worker execution;
- a second writer cannot acquire the same task worktree;
- exact process cancellation terminates the owned process group and no unrelated process;
- worker non-zero exit -> `BLOCKED`;
- malformed worker result -> `BLOCKED`;
- delete/move the task worktree before reconciliation -> task becomes `BLOCKED`;
- kill/restart the daemon while a task is RUNNING -> startup reconciliation becomes `BLOCKED`;
- stale writer lock is released only after blocked/terminal state.

If the worker can modify the source/main checkout through any execution path, P0 fails. Do not
paper over this with a prompt instruction; fix the outer workspace/process isolation boundary.

### P0 owner-initiated dispatch (PASS on `feat/p4-final-ui-repair @ 63a524b`)

Acceptance evidence:

- `POST /v1/tasks/{id}/dispatch` creates the `owner_dispatches` row, atomically
  transitions `READY → RUNNING` via `SafetyKernelStore.start_dispatched_worker`,
  spawns the supervised worker thread, and finishes with `VERIFIED` only when
  the deterministic host verifier produces and persists an immutable
  `evidence_id`;
- the worker sandbox is the host-owned `worker-opencode.json` seeded by
  `product_daemon.ensure_execution_policies()`. Edits are scoped to the
  assigned worktree, bash and webfetch are denied;
- the main repo stays bit-identical across the run. `MAIN_REPO_MUTATED`
  fails the dispatch before any worker spawns;
- `POST /v1/tasks/{id}/dispatch/recommendation` ranks every dispatchable
  target by the task's archived policy; the chosen target flows through the
  same handler as the manual dispatch;
- the macOS app shows both the manual dispatch and the recommendation panel,
  and surfaces a completion banner plus a macOS local notification when the
  task reaches a terminal state and the window is not focused.

## 4. P1 deterministic-verifier acceptance

Against the disposable task worktree, inject each failure separately:

- failing targeted test;
- failing build command;
- `git diff --check` failure;
- changed file outside the allowlist;
- missing verifier evidence;
- unexpected verifier command exit.

None may reach `VERIFIED`. A genuine pass must produce and persist an immutable `evidence_id`.
Automatic retry is allowed only for an explicitly host-attested known flaky infrastructure failure
and only within the configured bounded retry count.

## 5. Start the real Shadow daemon

Prepare a credential-free JSON `RuntimeConfig` containing the frozen registry, task profiles,
runtime availability, telemetry inputs, and policy. It must not contain provider secrets.

Start:

```text
.venv/bin/python -m personal_ai_orchestrator.daemon \
  --config /path/to/runtime.json \
  --state-db /path/to/runtime-state/orchestrator.sqlite3 \
  --runtime-state-root /path/to/runtime-state \
  --host 127.0.0.1 \
  --port 8765
```

Verify:

- the daemon binds only to loopback;
- non-JSON requests are rejected;
- invalid/stale routing decisions fail closed in the OpenCode adapter;
- SHADOW records a recommendation but never switches the session model;
- daemon unavailable/timeout keeps the current OpenCode model;
- every returned decision is already persisted in the durable decision ledger;
- policy and quota snapshot references resolve to immutable evidence.

Static runtime configuration cannot authorize production ACTIVE.

### P0 M0 trust hardening (PASS on `fix/m0-trust`)

- A1: `ExecutionEvidenceJournal.latest_verified_for_target` falls back
  from UNKNOWN to historical VERIFIED. `execution_verified_stale` is
  exposed on `ExecutionTargetHealthView` and
  `DispatchRecommendationCandidate` so the UI can render the staleness.
- A2: `QuotaAvailabilityState.UNCERTAIN_LOCKED` fires after
  `UNCERTAIN_LOCKED_THRESHOLD` (=3) consecutive failed quota collections
  for the same target. A single `observe_success()` releases the lock
  atomically. Admission rejects with `QUOTA_UNKNOWN`.
- A3: `_emergency_repair` now persists `signal: 9` plus
  `emergency_repair: true` and a human-readable reason. The owner can
  finally tell SIGKILL from a clean failure.
- A4: `RunView.pid_alive` is computed by `os.kill(pid, 0)`; the Swift
  inspector renders "已退出" instead of a stale "running pid N".
- A5: `pytest-rerunfailures` retries the daemon SIGINT shutdown test up
  to 3 times.

### P0 M1 WP0 daemon tick (PASS on `feat/m1-wp0-daemon-tick`)

Acceptance evidence:

- Boot the bundled product daemon with
  `--tick-interval-seconds 0.5` (or rely on the `PAO_TICK_INTERVAL_SECONDS`
  env var, or the 5.0s default).
- Two `/v1/health` polls spaced at least two supervisor intervals apart
  must show `last_tick_at` monotonically advancing on each poll.
- `supervisor_steps` must list exactly one entry named `heartbeat` with
  `consecutive_failures == 0` and `in_backoff == false` for a healthy
  daemon. New steps added by later WP branches will appear under the
  same field without re-wiring `/v1/health`.
- SIGINT / SIGTERM still exit cleanly (covered by the existing
  `test_sigint_shuts_daemon_down_cleanly_without_traceback`).

### P0 M1 WP1 burn (PASS on `feat/m1-wp1-burn`)

Acceptance evidence:

- Refresh quota on a connected provider; every `QuotaPlanWindowView.windows[*]`
  carries a `burn` sub-object with `pressure` populated to one of the
  seven enum values (`UNMETERED` / `STALE` / `EXHAUSTED` / `STARVED` /
  `AHEAD` / `BEHIND` / `ON_TRACK`).
- The Quota page renders a pressure chip per plan window using the
  seven `quota.pressure.*` labels. STARVED renders as "Expiring
  unused" / "将过期未用" (never "Starved" / "快断流" — that label is
  wrong on this verdict's semantics).
- The provider card carries `source_pressure` matching the WEEKLY
  window's `burn.pressure`. When the plan has no WEEKLY window,
  `source_pressure` is `"UNMETERED"` and no chip renders. When the
  plan has no observation at all, `source_pressure` is `null`.
- A `DispatchRecommendationCandidate` for any target with a provider
  that carries a WEEKLY window carries a `source_pressure` string
  equal to the card-level field for that provider's plan. The
  recommendation panel renders a chip from this string.
- After a collector omits `window_started_at`, the matching plan
  window's `burn.window_start_inferred` is `true`. The UI may render
  this with a small "(estimated start)" affordance.
- A pre-WP1 daemon decodes cleanly on a WP1 app: every new field is
  `null` / absent; no blank page, no missing chip.

## 6. MiniMax real provider acceptance

Use the already hardened local Stage C contract tracked by PR #17 with the target Mac's **existing
provider-native OpenCode authentication**.

Before the run, discover a currently available MiniMax model through the supported OpenCode catalog.
The completion oracle must require:

- exact requested provider ID;
- exact requested model ID;
- `finish == stop`;
- exact disposable-repository-derived output;
- no assistant error;
- exact process cleanup / session deletion evidence.

For quota observability, call the implemented MiniMax read-only collector only through an approved
supported credential handoff. If no such handoff exists, keep
`AUTHENTICATION_INTEGRATION_BLOCKED`. **Do not read or copy the OpenCode auth store** merely to
turn the status green.

## 7. Z.AI / GLM real provider acceptance

After the observed plan quota/reset condition permits a clean test:

- rediscover an available model;
- run one hardened provider-native disposable completion;
- run the read-only quota observation through a supported credential surface;
- verify confidence/source semantics remain conservative;
- preserve `UNKNOWN`/`ESTIMATED` rather than fabricating exact remaining quota.

If provider/model availability is still blocked by plan state, record the exact bounded category and
leave the gate open.

## 8. Shadow evidence collection

For real useful tasks, append `ShadowObservation` records containing:

- manual execution target;
- scheduler recommendation;
- catalog/policy/quota snapshot references;
- quota before/after references;
- predicted/observed burn where measurable;
- verified outcome;
- regression flag;
- attempts/time-to-green;
- handoff count;
- reset-cycle identity.

Do not synthesize observations to reach a sample count. The evidence must span multiple real reset
cycles before review eligibility is considered.

## 9. Production ACTIVE authorization

`ActiveRoutingGate.authorized` must remain false until all of the following are accepted:

1. P0 Safety Kernel authority on the target Mac;
2. P1 deterministic verifier authority;
3. fail-closed adapter validation;
4. real Shadow evidence acceptance;
5. safe BYPASS validation;
6. a durable `PRODUCTION_ACTIVE_ROUTING` owner approval record.

The owner approval is immutable once resolved and is checked from the host-owned SQLite approval
store. No UI toggle, scheduler score, model output, or static config file may substitute for it.

## Final acceptance record

When all runtime-dependent gates are actually run, add one evidence document containing:

```text
CANDIDATE_HEAD:
TARGET_MACOS:
PYTHON:
NODE:
OPENCODE:
LOCAL_CI:
P0_CHAOS:
P1_VERIFIER:
MINIMAX_STAGE_C:
MINIMAX_QUOTA:
ZAI_STAGE_C:
ZAI_QUOTA:
SHADOW_RESET_CYCLES:
SHADOW_OBSERVATIONS:
SHADOW_REGRESSIONS:
BYPASS:
OWNER_APPROVAL_ID:
FINAL_STATUS: GO | PARTIAL | NO_GO
```

Until that record is evidence-backed, the correct status is `PARTIAL` and production ACTIVE remains
disabled.
