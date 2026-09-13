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

## HOST_RECONCILIATION

Current reconciliation status:
**PI_5B3G_PREFLIGHT_BLOCKED_EXECUTION_REVERIFICATION_REQUIRED**.
The original blocked preflight above and its eight JSON files are preserved
unchanged. This continuation has a zero-model-call authorization.

### Actual host diagnosis and deployment

The original socket listener was PID **37671**, with PyInstaller bootloader
parent **37669**, started September 4. Its argv contained only the frozen
helper in the old `pao-p4-full-dashboard` app bundle; cwd was the product
Application Support directory. The daemon's `/v1/build` returned `unknown`.
The enclosing app carried build SHA
`bd82dba299fd54472e39f12971251a34ce2a927f`; this app metadata is not a proven
daemon revision. Read-only inspection of the helper's embedded Python archive
confirmed that `delegation_campaign`, `delegation_campaign_control`,
`delegation_campaign_runtime`, and `pi_provider_registry_manager` were absent.
Classification: **HOST_DEPLOYMENT_STALE**.

The live product was launched from an old bundled build, not the current
evidence branch or an editable Python package. Its zero-argument product
entrypoint resolves the normal layout below; these path constants were
confirmed in the frozen archive, and the cwd/database/socket were corroborated
by exact-PID inspection. No PAO-specific user LaunchAgent was found in the
user LaunchAgents inventory.

| Product path | Before and after |
| --- | --- |
| Config | `/Users/louisjia/Library/Application Support/Personal AI Orchestrator/runtime.json` |
| State DB | `/Users/louisjia/Library/Application Support/Personal AI Orchestrator/state.sqlite3` |
| Runtime state root | `/Users/louisjia/Library/Application Support/Personal AI Orchestrator/runtime-state` |
| Control socket | `/Users/louisjia/Library/Caches/Personal AI Orchestrator/control.sock` |

A clean detached production worktree was created on the external disk at
`/Volumes/Taoruide外接/AI LLM TOOLS/pao-product-main-64a45a3`, pinned to merged
main `64a45a3adaafb42cac2c6a55a3ac0db31c06f059`. The existing
`scripts/build_and_run_macos_dashboard.sh` performed the normal Release
build/package/sign/launch path using an existing project-local packaging venv,
with the current main source explicitly on `PYTHONPATH`. No global install,
source repair, or policy edit was made. The build script terminated only the
identified PAO helper/bootloader PIDs and launched the exact new bundle.

The new GUI PID is **71928**; daemon listener PID **71946**, bootloader
**71945**. Both app and daemon report `64a45a3adaafb42cac2c6a55a3ac0db31c06f059`.
The new frozen archive includes all four previously missing modules and
`CampaignAwareDelegationChildPort`. The main worktree remained clean after
the build. Absolute executable paths, hashes and argv are in the new JSON
evidence. Old installations and checkout contents were not deleted.

### State provenance and supported discovery

The canonical successful PI-3 journal still exists at
`/private/tmp/pao-pi3-local-evidence-20260912/pi3/runtime`, including
`exec-verify-e6ad947964ec813e48c6de6b`. The original PI-3 acceptance helper
explicitly constructed its own ControlPlaneServer, disposable fixture, database
and this runtime root. It was not the installed product daemon. Thus the
cause is **A: different runtime roots**, specifically isolated acceptance
versus the installed product, rather than proven state loss or schema failure.

The product's existing legacy migration record is dated September 2, before
the PI-3 acceptance. The known legacy product root has no Pi snapshot. The
located PI-3 evidence is legitimate acceptance evidence, but does not establish
a previous installed-product state root eligible for migration. No supported
acceptance-to-production authority import was identified. No VERIFIED row was
copied, edited, manufactured, or migrated.

On normal boot, `product_daemon.resolve_dynamic_registry` called the existing
`PiProviderRegistryManager.bootstrap_if_missing` path. Its metadata-only
discovery persisted the previously missing product Pi registry. Discovery
correctly left execution verification false. It created no task or worker.

| Read-only preflight | Before | After |
| --- | --- | --- |
| Campaign endpoint | HTTP 404 | HTTP 200, OFF, consumed=0 |
| Exact parent target present | NO | YES |
| Runtime / runtime provider | unavailable in projection | `pi` / `minimax-cn` |
| Model SKU | absent | `minimax-cn-coding-plan/MiniMax-M3` |
| Execution verified | NO | NO |
| Runtime available | not established | YES |
| Pi metadata | 0.85.1, auth READY, M3 present | Rechecked; same |
| Exact launch validation | target unavailable | Rejected: `execution target has not been runtime-verified` |

One provider-scoped read-only quota refresh used the live product API and
existing collector. At **2026-09-13T11:53:50.418273Z**, provider
`minimax-cn-coding-plan` resolved through the production canonical resolver to
pool `minimax-token-plan-cn`. Snapshot
`quota-d53f8021-128a-4e00-89c9-bed75081b980` was **AVAILABLE / EXACT**,
with 5-hour remaining fraction **0.88** and weekly **0.33**. The live projection
reported refresh SUCCESS; no active shared-pool blocker was present. Quota
was launchable at that observation time. This does not override the failed
execution-verification gate and must be refreshed before future execution.

### Budget, verification, and remaining action

REAL_MODEL_CALLS = **0**; REAL_WORKERS = **0**; CAMPAIGN_STARTED = **NO**.
No campaign start POST, fixture creation, dispatch, fallback, or inference
probe was performed. The final campaign remains OFF. Database task/run/workspace
counts stayed **34 / 7 / 7** across deployment and preflight. RUNNING rows,
active run rows and held writers remained **0 / 0 / 0**.

SOURCE_CODE_CHANGED = **NO**. Normal Release build, package/sign verification,
live host identity, endpoint and discovery checks passed. Local validation is
limited to evidence consistency, preservation of historical evidence,
`assert_sanitized` for committed JSON, and `git diff --check`. No full pytest,
Swift test suite, OpenCode regression, or full Ruff run was performed.

Evidence is appended under `pi5b3g-2026-09-13/host-reconciliation/`.
The only remaining observed preflight blocker is exact-target execution
verification in the product journal. One explicitly authorized real verification
call through the normal production verification mechanism would be required;
it would be separate from campaign observations. No such call is authorized or
performed by this reconciliation task. PR #53 stays Draft; do not merge or
start the real campaign from this result.

## EXACT_TARGET_PRODUCT_REVERIFICATION

The owner separately authorized exactly one real exact-target verification
call, with zero retries and no campaign startup. This section preserves both
previous blocked-preflight and host-reconciliation evidence unchanged.

Evidence branch baseline: `c704abb8e85ba34b760c9003470b0b59c0dee000`.
Production code remains merged main
`64a45a3adaafb42cac2c6a55a3ac0db31c06f059`.

### Before the call

The live product reported the expected production commit. Campaign GET returned
HTTP 200 / OFF / consumed=0. The exact target was present, enabled and runtime
available, with `runtime_id=pi`, `runtime_provider_id=minimax-cn`, and model SKU
`minimax-cn-coding-plan/MiniMax-M3`. Pi version was 0.85.1; the metadata-only
auth check returned READY and the catalog included MiniMax-M3.

The product `ExecutionEvidenceJournal` had no evidence for this exact target.
Normal `validate_execution_target_launch` rejected it solely with
`execution target has not been runtime-verified`.

One live-host provider-scoped read-only quota refresh returned SUCCESS and
AVAILABLE / EXACT in canonical pool `minimax-token-plan-cn`, snapshot
`quota-519fee87-5e6f-40b3-8307-4040df47fcea`. Remaining fractions were 0.99 for
the five-hour window and 0.33 for the weekly window. There was no active shared
pool blocker. No model call was used to discover quota.

### Canonical verification and separate budget

The unchanged production `run_pi_execution_probe` ran against the existing
target and the actual product runtime-state root:
`/Users/louisjia/Library/Application Support/Personal AI Orchestrator/runtime-state`.
The probe retained the canonical model translation, trusted worktree guard,
`build_worker_env`, `ProcessSupervisor`, bounded stream capture, Pi JSON
summary validation, and `ExecutionEvidenceJournal.append` authority.

A repository-external observer recorded only sanitized process/protocol facts
and enforced exclusive durable attempt/process markers. It did not replace
the probe validator or construct a VERIFIED row. The probe used a disposable
external-disk workspace, not an owner repository. An isolated Pi settings
directory disabled session retries, provider retries (`maxRetries=0`), and
automatic compaction; Pi's own SettingsManager confirmed those values before
inference. Its auth path was only a temporary reference to the existing CLI
auth store; credential values were neither read by the observer nor copied.
The settings directory and reference were removed after the worker exited.

| Verification fact | Observed result |
| --- | --- |
| Exact target | `pi-minimax-cn-coding-plan-MiniMax-M3` |
| Real verification attempts / Pi worker processes | 1 / 1 |
| Worker PID / process group | 82386 / 82386 |
| OS exit | 0 |
| Pi JSON protocol | VALID |
| Actual runtime / provider / model | `pi` / `minimax-cn` / `MiniMax-M3` |
| Final stop reason | `stop` |
| Extension errors | 0 |
| Tool start / end count | 0 / 0 |
| Expected canonical probe marker | PRESENT |
| Output truncation | NO |
| Durable evidence ID | `exec-verify-0abd3c01892bb7e04f07d276` |
| Evidence observation time | 2026-09-13T12:12:16.193526Z |
| Evidence result / reason | VERIFIED / REAL_PI_WORKER_PROBE_SUCCEEDED |
| Automatic retries / fallback | 0 / NO |

The canonical marker was
`PERSONAL-AI-ORCHESTRATOR-PI-EXECUTION-PROBE-OK`. The worker's tool allowlist
excluded bash, powershell, webfetch and pao_delegate. No delegation broker or
child was created. Raw streams were held only in bounded memory for the
canonical validator and were not persisted or committed.

### Post-verification gate and cleanup

The live product providers projection now reports execution_verified=true
and runtime_available=true for the exact target. The normal launch validator
passes using the newly journaled product evidence. Auth was rechecked as READY.
The same persisted quota snapshot remained current and launchable during the
post-verification check (about 36 seconds old), with no shared-pool blocker.
No second quota refresh or quota-consumption attribution was performed.

Campaign GET remains HTTP 200 / OFF / consumed=0. Campaign parent workers,
child workers and total workers used are all **0**; its original maximums
remain **3 / 3 / 6**, with three observations still unused. The single
re-verification worker is accounted separately. No Observation 1 was launched.

The exact verification PID and its owned process group were both absent after
exit; orphan processes=0. Product task/run/workspace counts stayed 34/7/7.
RUNNING rows, active run rows and held writers remained 0/0/0. Production source
HEAD and clean status were unchanged. No credential values were exposed.

### Evidence validation and status

Sanitized evidence is in `pi5b3g-2026-09-13/exact-target-reverification/`:
preflight, verification result, post-verification preflight, cleanup, retry
policy and separate worker budget. Local validation covers evidence
consistency, existing `assert_sanitized` on all committed JSON, preservation
of previous evidence bytes, and `git diff --check`. No full local pytest,
Swift tests, OpenCode regression, or full Ruff was run; source code did not
change. Exact-head CI for the evidence commit is recorded on PR #53.

Runtime verification and post-verification preflight succeeded. Once that
evidence commit's exact-head CI is green, the applicable status is
`PI_5B3G_EXACT_TARGET_REVERIFICATION_COMPLETE_CAMPAIGN_READY`.
This is readiness at the recorded observation time, not a campaign execution
or enforcement approval. Any subsequent campaign must repeat current quota
and host checks. PR #53 remains OPEN / DRAFT and must not be merged. The real
campaign is not started by this task.
