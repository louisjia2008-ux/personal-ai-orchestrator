# GitHub issue closeout — 2026-09-29

This record closes the original PAO backlog without treating missing external
evidence as success. “Implemented” means the repository contract and its local
or CI verification are complete. “Superseded” means an explicit architecture
decision replaced the original proposal. Neither status enables Production
ACTIVE, performs a paid provider call, changes repository visibility, or proves
distribution signing/notarization.

## Resolution matrix

| Issue | Resolution | Evidence and retained boundary |
| --- | --- | --- |
| #1 ACP feasibility | **SUPERSEDED / NO-GO** | [Decision 0001](decisions/0001-common-acp-no-go.md) records that one common ACP lifecycle was never proved for all three workers. Production uses narrow host-owned adapters; Codex/Claude are not advertised as PAO execution adapters without their own evidence. |
| #2 Safety Kernel | **IMPLEMENTED** | SQLite/WAL task, run, workspace, approval and audit truth; versioned fail-closed state machine; idempotent submit; worktree/writer ownership; process supervision; startup reconciliation; and fault-injection coverage are implemented in `safety_kernel.py`, `worktree_manager.py`, and the full Python suite. |
| #3 deterministic verification | **IMPLEMENTED** | Host-owned argv-only verifier profiles, changed-file scope, `git diff --check`, immutable structured evidence and VERIFIED gating are implemented and covered by verifier/safety tests plus target-Mac evidence. |
| #4 multi-worker reviewer flow | **SUPERSEDED** | [Decision 0002](decisions/0002-review-flow-superseded.md) retains target/runtime plurality, one-writer isolation and mandatory host verification, while rejecting an unproved automatic reviewer. Explicit one-level delegation is the bounded second-worker path. |
| #5 quota governor | **IMPLEMENTED** | Provider collectors, normalized availability, UNKNOWN fail-closed behavior, reset/recovery state, reserve protection, no-unapproved-overage behavior and fake-clock/failure tests are present. Live provider truth remains separate from offline correctness. |
| #6 Telegram and DeskPet clients | **IMPLEMENTED** | `client_gateway.py`, `client_cli.py`, typed UDS operations and [client documentation](TELEGRAM_DESKPET_CLIENTS.md) provide submit/status/cancel/approve/report only. Tests prove shared state, retry idempotency, dual Telegram allowlists, client-crash independence, fixed approval resolution, notification transitions and rejection of argv/shell fields. Real Telegram delivery is **NOT OBSERVED** because no bot credential or external message was used. |
| #9 Model Registry / QuotaPool | **IMPLEMENTED** | Provider/account/plan/pool/SKU/runtime identities, shared-pool membership, secret references, schema validation, serialization and invalid-reference tests are present. |
| #10 quota observability audit | **IMPLEMENTED** | `quota_capabilities.py`, `provider_quota_observability.json` and the human-readable audit cover the initial provider families with source, scope and EXACT/ESTIMATED/UNKNOWN semantics. UNKNOWN is preserved rather than rendered as a precise remaining percentage. |
| #11 deterministic router | **IMPLEMENTED** | Capability/runtime/risk/quota filters, four routing policies, deterministic ranking, machine-readable explanations, snapshot refs and replay tests are present. No ML or prompt router was introduced. |
| #12 OpenCode V2 adapter spike | **SUPERSEDED BY PRODUCTION ADAPTER CONTRACT** | [Decision 0003](decisions/0003-opencode-spike-closeout.md) records the implemented thin adapter, safe BYPASS, SHADOW non-side-effect behavior, session-scoped switch surface and lease boundary. Historical live-provider evidence remains partial; this closeout does not claim a fresh MiniMax and Z.AI paid task. |
| #13 macOS control plane | **IMPLEMENTED FROM SOURCE** | The app contains menu bar, dashboard and embedded WidgetKit extension, reads typed daemon views, and packages the helper. The exact closeout build passed Xcode Release build, local code-sign verification for app/widget/helper, package identity injection and 456 Swift tests. Developer-ID distribution signing and notarization remain **NOT OBSERVED**. |
| #14 open-source release readiness | **SOURCE READY** | MIT is selected with a documented MIT/Apache-2.0/MPL-2.0 comparison. Community files, issue templates, supported-platform boundaries, dependency notices, install instructions, history credential review and personal-path sanitization are complete in the [release checklist](PUBLIC_RELEASE_CHECKLIST.md). Public visibility is intentionally not changed by this closeout. |
| #37 delegation policy/economics | **IMPLEMENTED THROUGH SHADOW EVIDENCE** | Deterministic ALLOW/DENY/SHADOW_ONLY decisions, reason codes, idempotency, recursion prohibition, downstream quota/verifier gates, append-only evidence and bounded real shadow campaigns are implemented. Automatic Production ACTIVE remains independently gated. |
| #56 execution freshness | **IMPLEMENTED** | Historical verification and current launch authority are separate projections. Age expiry and later demotion revoke actionable launch authority; static authoritative targets remain consistent; the recommender consumes the daemon projection. |
| #57 quota credential bridge | **IMPLEMENTED** | Owner-mediated Keychain authorization/revocation is isolated from Pi execution auth and sanitized in persistence/UI. Source and packaged-helper diagnostics proved the `OWNER_KEYCHAIN` source without printing a value or calling a provider; the disposable test item was deleted and absence rechecked. Collector success/failure behavior is covered offline; no live quota endpoint was called for closeout. |
| #58 recovered quota admission | **IMPLEMENTED** | `RECOVERED_OBSERVED` is admitted only by the SUPERVISED_AUTO quota gate after the existing exhaustion/recovery state machine produces it; all uncertain/exhausted states remain fail-closed and regression tests cover the transition. |
| #59 GitHub runner allocation | **RESOLVED** | Main workflow run `36446726922` allocated real Ubuntu/macOS runners and executed checkout, install, lint/format, full pytest, adapter, `swift build`, and `swift test` steps successfully. Required checks were not weakened. |
| #60 exact daemon ownership | **IMPLEMENTED** | The macOS lifecycle controller derives the serving PID from the UDS kernel peer, validates instance/executable identity and signals only the owned lifecycle. Tests cover dead/stale/reused/mismatched identity and restart. The exact packaged helper additionally passed five alternating parent/child SIGTERM cycles with one signal each, clean socket removal, SQLite `quick_check=ok`, zero active runs/writers and no surviving parent/child. |

## Exact local verification

Code/package commit: `dec5af150ebcdde17666e6a0403a1281e3c1f0ce`.

- Python full suite: **1339 passed, 1 skipped**, zero failures. The suite uses a
  short `/private/tmp` base because macOS AF_UNIX paths have a platform length
  limit.
- Swift: **456 passed**, zero failures.
- OpenCode adapter: TypeScript check plus **17 passed**.
- DSH plugin: **9 passed**; Pi DSH: **7 passed**.
- Ruff lint and format checks: passed; `git diff --check`: passed.
- Release app build: passed; app, embedded Widget extension and packaged helper
  passed local code-sign validation and carried the exact Git build identity.
- Packaged credential diagnostic: present/source projection passed with no
  credential output and no provider call; disposable Keychain item removed.
- Packaged shutdown: **5/5 passed** (three parent routes, two child routes), no
  SIGKILL, no orphan, no stale socket, clean SQLite state after every cycle.

## Explicitly unclaimed

- no repository visibility change, GitHub Release, Developer-ID distribution
  signing, notarization, Windows support or cross-platform UI acceptance;
- no real Telegram delivery, provider purchase, paid overage or fresh live
  MiniMax/Z.AI execution/quota call;
- no common-ACP pass for Codex + Claude Code + OpenCode;
- no automatic reviewer runtime and no Production ACTIVE authorization.
