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
| Config | `/Users/<user>/Library/Application Support/Personal AI Orchestrator/runtime.json` |
| State DB | `/Users/<user>/Library/Application Support/Personal AI Orchestrator/state.sqlite3` |
| Runtime state root | `/Users/<user>/Library/Application Support/Personal AI Orchestrator/runtime-state` |
| Control socket | `/Users/<user>/Library/Caches/Personal AI Orchestrator/control.sock` |

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
