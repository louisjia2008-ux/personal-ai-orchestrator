# P3.9.1 Unsupported Quota Policy Analysis - 2026-08-30

Status: `P3_9_1_UNSUPPORTED_QUOTA_POLICY_ANALYSIS_COMPLETE_NO_ACTIVE_AUTHORIZATION`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## CURRENT_CODE_PATH

The current Shadow and activation path is deliberately split:

- `RoutingService` can record SHADOW decisions and pending Shadow correlations while the actual
  retained worker still runs manually or under the host.
- `ShadowEvidenceJournal` finalizes observations only after durable Safety Kernel and verifier
  truth exists.
- `ResetCycleReference.counts_for_real_acceptance` returns true only for provider-exact,
  exact-confidence reset cycles with a known reset time.
- `activation_authority` and `activation.py` keep owner approval and evidence eligibility as
  separate gates.

P3.9.1 adds `quota_policy_analysis.py` as an explicit simulation layer for unsupported quota
surfaces. It does not relax the production activation path.

## CURRENT_GATE

The current production gate requires:

```text
minimum quality observations: 20
minimum real reset cycles: 2
reset-cycle authority: PROVIDER_EXACT + EXACT confidence + known reset_at
owner approval: required separately
production ACTIVE default: disabled
```

Quality observations collected with `UNKNOWN` quota confidence may be useful for model-quality
analysis, but they contribute zero real reset cycles.

## CODEX_CURRENT_QUOTA_SURFACE

The Codex CLI existing login can execute real tasks without reading local credential material, but
the campaign does not expose a stable provider-native exact subscription quota/reset surface.

P3.9.1 real campaign evidence:

```text
CAMPAIGN_ROOT: .personal-ai-orchestrator/p391-codex-shadow
REPORT: .personal-ai-orchestrator/p391-codex-shadow/p39-real-shadow-quality-campaign-report.json
GENERATED_AT_UTC: 2026-08-30T14:08:24.531893+00:00
GENERATED_AT_ASIA_SHANGHAI: 2026-08-30 22:08:24
SELECTED_VARIANTS: 20
TASK_FAMILIES: BUG_FIX, TEST_ADD, REFACTOR, MULTI_FILE_CHANGE
TOTAL_REAL_QUALITY_OBSERVATIONS: 20
TOTAL_QUALITY_ELIGIBLE_OBSERVATIONS: 8
VERIFIED_COUNT: 8
FAILED_OR_BLOCKED_COUNT: 12
REAL_RESET_CYCLES: 0
SHADOW_REVIEW_ELIGIBLE: false
```

The 12 blocked observations exited before verifier execution with Codex CLI usage-limit messages.
This is not model-quality failure evidence. The runner now classifies matching future exits as
`POLICY_BLOCK` / `POLICY_BLOCKED`.

## PERMANENT_BLOCK_ANALYSIS

For providers with no supported exact quota/reset surface, the current exact-only reset gate creates
a permanent production-ACTIVE dead end for that provider:

```text
PROVIDER_EXACT_SURFACE_AVAILABLE: false
CURRENT_POLICY_RESULT: ACTIVE_PERMANENTLY_BLOCKED_WITHOUT_PROVIDER_EXACT_SURFACE
```

This is an intentional safety outcome, not an implementation bug. The system may still collect
quality observations, but it cannot graduate to production ACTIVE because the reset-cycle gate can
never be satisfied by unsupported evidence.

## EXACT_ONLY_OPTION

Exact-only keeps the strongest authority boundary:

- only provider-native exact reset metadata can count;
- local success/failure timing does not become reset truth;
- usage-limit errors do not become reset-cycle completion evidence;
- production ACTIVE remains impossible for unsupported providers.

Tradeoff: Codex subscription routing can remain permanently blocked from ACTIVE even after many
successful quality observations.

## LOCALLY_MEASURED_OPTION

A locally measured conservative option can be useful for simulation-only planning:

- source: host-observed successes, exhaustion events and later recovery;
- confidence: at most `ESTIMATED`;
- reset-cycle source: `LOCALLY_INFERRED`;
- production effect: does not create real reset evidence or owner approval.

This option can inform local throttling, backoff or expected capacity, but it must not satisfy the
current production reset-cycle gate unless the product explicitly creates a separate owner-approved
activation tier.

## USER_DECLARED_OPTION

A user-declared window option can model user-provided subscription semantics:

- source: explicit user statement about reset timing or allowance window;
- confidence: at most `ESTIMATED`;
- reset-cycle source: `LOCALLY_INFERRED`;
- production effect: does not create real reset evidence or owner approval.

This can reduce local annoyance and make scheduling less wasteful, but it is not provider truth.

## SAFETY_COMPARISON

```text
EXACT_ONLY:
  safety: strongest
  liveness: unsupported providers may be permanently blocked
  false-positive ACTIVE risk: lowest

LOCALLY_MEASURED_CONSERVATIVE:
  safety: medium if kept simulation-only
  liveness: better local planning
  false-positive ACTIVE risk: unacceptable if treated as provider-exact

USER_DECLARED_WINDOW:
  safety: medium-low if kept simulation-only
  liveness: better human-aligned planning
  false-positive ACTIVE risk: unacceptable if treated as provider-exact
```

## RECOMMENDED_POLICY

Keep production ACTIVE exact-only for unsupported quota surfaces.

Add local policy lanes only as explicitly labeled non-production inputs:

```text
PRODUCTION_ACTIVE_GATE: EXACT_ONLY
LOCAL_SCHEDULING_HINTS: LOCALLY_MEASURED_CONSERVATIVE allowed
USER_EXPERIENCE_HINTS: USER_DECLARED_WINDOW allowed
RESET_CYCLES_FOR_ACCEPTANCE: provider-exact only
OWNER_APPROVAL: still separate and absent
```

This preserves the fail-closed acceptance contract while avoiding a blind spot: unsupported
providers can be useful in Shadow quality campaigns, but they remain ineligible for production
ACTIVE until exact provider evidence exists or the owner deliberately creates a different activation
policy.

## IMPLEMENTATION_IMPACT

P3.9.1 implementation impact:

- added a quota-policy simulation module with exact-only, locally measured and user-declared modes;
- prevented non-provider evidence from carrying `EXACT` confidence;
- added failure taxonomy fields to Shadow observations and summaries;
- split verified quality, model task failure, worker/process/auth/timeout/policy failures and
  verifier infrastructure failure counters;
- expanded the Codex-only P3.9 campaign matrix to 20 variants across 4 task families;
- kept report fields machine-readable for readiness, quality metrics, case catalog and blocking
  reasons.

## WHAT_WAS_NOT_CHANGED

P3.9.1 did not:

- add Claude as a model provider;
- create production ACTIVE authorization;
- create owner approval;
- fabricate provider-exact quota or reset evidence;
- count usage-limit messages as real reset-cycle evidence;
- read or print local credential files;
- repair MiniMax/Z.AI credentials;
- merge or mark the PR ready for review.
