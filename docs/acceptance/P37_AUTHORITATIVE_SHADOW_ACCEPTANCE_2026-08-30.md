# P3.7 Authoritative Shadow Acceptance - 2026-08-30

Status: `P3_7_AUTHORITATIVE_SHADOW_READY_REAL_EXECUTION_BLOCKED`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## Baseline

```text
BRANCH: integration/end-to-end-shadow-safety
BASELINE_HEAD: b4cfc2f23defcefdcaa1fd5f9919cf0a6103e8f5
PR: #21
PR_STATE: OPEN / DRAFT
CI_AT_BASELINE: test SUCCESS, opencode-adapter SUCCESS
```

## P3.6 Findings

```text
P36_FINDING_A: CONFIRMED
P36_FINDING_B: CONFIRMED
P36_FINDING_C: CONFIRMED
```

Finding A was confirmed because `scripts/p36_shadow_campaign.py` bootstrapped campaign state,
probed credential-safe provider metadata, summarized existing observation files and exited. It did
not automatically observe real task execution.

Finding B was confirmed because previous reset-cycle counting trusted strings from
`ShadowObservation.reset_cycle_ids` without requiring durable persisted `ResetCycleReference`
records.

Finding C was confirmed because review eligibility was computed from global observation/reset
counts while provider/pool/task-family groups were informational only.

## Repairs

Implemented in P3.7:

- append-only durable `ResetCycleReference` storage;
- append conflict rejection for duplicate reset IDs with different content;
- observation append validation for missing reset references;
- observation append validation that reset reference provider/quota pool matches the observation;
- explicit reset source classes: `SYNTHETIC_TEST`, `PROVIDER_EXACT`, `PROVIDER_ESTIMATED`,
  `LOCALLY_INFERRED`, `UNKNOWN`;
- real reset-cycle counting only for provider-exact, exact-confidence, known-reset-time references;
- synthetic and unknown reset cycles remain visible in summaries but cannot count toward real
  longitudinal acceptance;
- campaign acceptance policy snapshots persist `minimum_observations`,
  `minimum_real_reset_cycles` and `require_zero_regressions`;
- campaign status semantics now distinguish `BOOTSTRAPPED`, `COLLECTING`, `PAUSED` and
  `COMPLETED`;
- SHADOW routing can write durable pending observation correlations when the host supplies the
  actual retained execution target;
- deterministic verifier finalization can convert a pending correlation into an immutable
  `ShadowObservation` after Safety Kernel verifier truth is applied;
- cohort-scoped eligibility groups by provider, quota pool, actual execution target and task
  family.

## Evidence Boundary

```text
QUALITY_OBSERVATIONS: 0 real local campaign observations
REAL_RESET_CYCLES_OBSERVED: 0
SYNTHETIC_RESET_CYCLES_CAN_COUNT: NO
UNKNOWN_CYCLES_CAN_COUNT: NO
SHADOW_REVIEW_ELIGIBLE: false
ACTIVE_ELIGIBLE_COHORTS: none in the real campaign
OWNER_APPROVAL: absent
PRODUCTION_ACTIVE: disabled
```

Quality Shadow evidence may be collected with unknown quota/reset metadata, but unknown reset
metadata cannot satisfy the quota-reset production gate. If MiniMax or Z.AI expose no supported
machine-readable reset surface, their longitudinal quota-aware ACTIVE acceptance remains blocked.

## Tests

Local automated evidence recorded during P3.7:

```text
Ruff: PASS
pytest: 154 passed
OpenCode adapter typecheck/tests: PASS
Target Mac acceptance: PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED
```

Coverage added or hardened for:

- arbitrary reset strings cannot satisfy Shadow acceptance;
- missing reset references fail closed;
- duplicate reset references with conflicting content are rejected;
- unknown reset cycles do not count as real reset cycles;
- synthetic reset cycles do not count as real reset cycles;
- reset reference provider mismatch fails closed;
- reset reference quota pool mismatch fails closed;
- provider/pool/target/task-family cohort isolation;
- campaign policy immutability for summary thresholds;
- automatic pending Shadow correlation from SHADOW route;
- verifier-backed pending finalization into immutable observation;
- Shadow review eligibility still cannot create owner approval.

## Still Pending

```text
FIRST_REAL_END_TO_END_OBSERVATION: NOT EXECUTED
REAL_LONGITUDINAL_RESET_TIME: PENDING
TARGET_MAC_REAL_PROVIDER_ACCEPTANCE: LOCAL STRUCTURAL ACCEPTANCE ONLY
PRODUCTION_ACTIVE_APPROVAL: NOT REQUESTED
```

No fake reset cycles, fake elapsed time, credential scraping or owner approval were created.
