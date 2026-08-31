# P3.8 First Real Shadow Observation - 2026-08-30

Status: `P3_8_FIRST_REAL_SHADOW_OBSERVATION_ACCEPTED`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## Baseline

```text
BRANCH: integration/end-to-end-shadow-safety
BASELINE_HEAD: b7671c5d16391c38195f044866d909c6762ad864
PR: #21
PR_STATE: OPEN / DRAFT
CI_AT_BASELINE: test SUCCESS, opencode-adapter SUCCESS
```

## Worker Selection

Credential-safe worker probes were performed without reading, copying or displaying provider
secrets.

```text
OPENCODE_CLI: available, version 1.18.23
OPENCODE_MINIMAX_CATALOG: available
OPENCODE_MINIMAX_EXISTING_AUTH: unusable, provider returned 401 invalid api key
CODEX_CLI: available, version codex-cli 0.132.0
CODEX_EXISTING_AUTH: usable
CLAUDE_CLI: available, not selected because Codex was usable
SELECTED_REAL_WORKER: codex-cli
PROVIDER: openai
ACTUAL_EXECUTION_TARGET: codex-cli-gpt-5.5
AUTH_REUSED_WITHOUT_SECRET_READ: YES
```

## Real Shadow Run

The acceptance run created a disposable Git repository with one intentionally failing
`unittest`-verified function. A real Codex CLI worker fixed only `src/tiny_math.py`; the host-owned
deterministic verifier then re-ran the trusted command.

```text
DISPOSABLE_REPO: /var/folders/pt/tn46s3216rg366nxx7g7ng940000gn/T/pao-p38-real-shadow-4bvwb7kp
BASE_SHA: d726ec5bfea38b6ff98a4d3112e53ba6f2bf6223
DISPOSABLE_BRANCH: master
TASK_ID: p38-real-shadow-7287b7e34d22
RUN_ID: run-08b2f6a872d6
TASK_FAMILY: worker
ROUTING_REQUEST_ID: route-9b542f213c1d
ROUTING_DECISION_ID: route-06cca2f09f28ec0601352846
PENDING_ID: pending-route-06cca2f09f28ec0601352846
CATALOG_SNAPSHOT_ID: catalog-p38-real-codex
POLICY_SNAPSHOT_ID: policy-0deba1d617d944730cabeb62
QUOTA_SNAPSHOT_IDS: quota-p38-codex-unknown
WOULD_SELECT_TARGET: none
ACTUAL_RETAINED_TARGET: codex-cli-gpt-5.5
```

`WOULD_SELECT_TARGET` is intentionally empty because the scheduler had only UNKNOWN quota
confidence. P3.8 uses the retained actual worker identity for Shadow collection while preserving
the quota gate truth.

## Verifier Evidence

```text
REAL_WORKER_EXECUTION: exit 0
WORKER_REPORTED_COMPLETION_STATE: FINISHED
TRUSTED_VERIFIER_PROFILE: p38-disposable-unittest
TRUSTED_VERIFIER_COMMAND: python3 -B -m unittest discover -s tests
VERIFIER_RESULT: VERIFIED
VERIFIER_EVIDENCE_ID: verify-2ac646548adbfea20e176981
CHANGED_FILES: src/tiny_math.py
```

## Shadow Evidence Boundary

```text
FIRST_REAL_SHADOW_OBSERVATION: shadow-79b324d8b775bb0d2e80e221
QUALITY_OBSERVATIONS_BEFORE: 0
QUALITY_OBSERVATIONS_AFTER: 1
CAMPAIGN_STATUS_BEFORE: BOOTSTRAPPED
CAMPAIGN_STATUS_AFTER: COLLECTING
QUOTA_CONFIDENCE: UNKNOWN
RESET_METADATA: UNKNOWN_OR_ABSENT
REAL_RESET_CYCLES_CONTRIBUTED: 0
REAL_RESET_CYCLES_TOTAL: 0
SHADOW_REVIEW_ELIGIBLE: false
OWNER_APPROVAL: ABSENT
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
```

The observation proves the real end-to-end collection path:

- real SHADOW routing decision with immutable catalog/policy/quota references;
- actual retained worker target recorded independently of the scheduler recommendation;
- real worker process execution through existing authenticated Codex CLI;
- host-owned verifier finalization into append-only Shadow evidence;
- campaign state transition from `BOOTSTRAPPED` to `COLLECTING`.

It does not prove longitudinal reset-cycle acceptance. Unknown quota and absent reset metadata are
visible on the observation, contribute zero real reset cycles and keep Shadow review eligibility
false.

## Local Verification

```text
Ruff: PASS
pytest: 158 passed
OpenCode adapter typecheck/tests: PASS, 10/10
git diff --check: PASS
Target Mac acceptance: PASS_LOCAL_P0_P1_ROUTING_PROVIDER_AND_LONGITUDINAL_SHADOW_NOT_EXECUTED
P3.8 real Shadow execution script: PASS
```

No production ACTIVE routing, owner approval, merge, ready-for-review transition, force push,
credential scraping or fake reset-cycle evidence was created.
