# P3.6 Live Provider And Shadow Acceptance - 2026-08-30

Status: `PROVIDER_SURFACES_UNKNOWN_SHADOW_CAMPAIGN_BOOTSTRAPPED_P37_HARDENED`

Production ACTIVE remains `DISABLED_BY_DESIGN`.

## Baseline

```text
BRANCH: integration/end-to-end-shadow-safety
BASELINE_HEAD: d28a3354c96eae68121ac0a6b5f6535a934e00af
CAMPAIGN_STATE: .personal-ai-orchestrator/p36-shadow/shadow-campaign.json
CAMPAIGN_REPORT: .personal-ai-orchestrator/p36-shadow/p36-shadow-campaign-report.json
```

The `.personal-ai-orchestrator/` runtime directory is ignored by Git. It is local durable campaign
state, not repository evidence to commit.

## Provider Surface Discovery

MiniMax Token Plan:

```text
AUTH_SURFACE: UNKNOWN
QUOTA_SURFACE: NONE_SUPPORTED through existing OpenCode CLI metadata
LIVE_RESULT: LIVE_UNKNOWN
EVIDENCE: opencode models minimax returned catalog entries including MiniMax-M3
LIMIT: model catalog visibility does not prove provider auth or remaining quota truth
```

Z.AI / GLM Coding Plan:

```text
AUTH_SURFACE: NONE_SUPPORTED locally
QUOTA_SURFACE: NONE_SUPPORTED locally
LIVE_RESULT: LIVE_UNKNOWN
EVIDENCE: opencode models zai returned Provider not found
```

OpenAI / Codex subscription:

```text
AUTH_SURFACE: UNKNOWN
QUOTA_SURFACE: NONE_SUPPORTED
LIVE_RESULT: LIVE_UNKNOWN
EVIDENCE: codex CLI is installed, but CLI availability is not machine-readable subscription quota
```

Anthropic / Claude subscription:

```text
AUTH_SURFACE: UNKNOWN
QUOTA_SURFACE: NONE_SUPPORTED
LIVE_RESULT: LIVE_UNKNOWN
EVIDENCE: claude CLI is installed, but CLI availability is not machine-readable subscription quota
```

DeepSeek PAYG:

```text
AUTH_SURFACE: AUTH_REQUIRED
QUOTA_SURFACE: BALANCE_ONLY
LIVE_RESULT: NOT_EXECUTED
LIMIT: PAYG balance is not reset-window subscription quota; no safe credential handoff supplied
```

Local runtime capacity:

```text
AUTH_SURFACE: PROVIDER_NATIVE_CLI
QUOTA_SURFACE: NONE_SUPPORTED
LIVE_RESULT: NOT_SUPPORTED
EVIDENCE: ollama CLI/runtime is available
LIMIT: local capacity is runtime availability, not provider quota
```

## Shadow Campaign

```text
CAMPAIGN_ID: recorded in .personal-ai-orchestrator/p36-shadow/shadow-campaign.json
CAMPAIGN_STARTED_AT: recorded in .personal-ai-orchestrator/p36-shadow/shadow-campaign.json
CAMPAIGN_STATUS: BOOTSTRAPPED
RESET_CYCLES_REQUIRED: 2
QUALITY_OBSERVATIONS: 0
REAL_RESET_CYCLES_OBSERVED: 0
SYNTHETIC_RESET_CYCLES: 0
UNKNOWN_RESET_OBSERVATIONS: 0
SHADOW_REVIEW_ELIGIBLE: false
```

The campaign state is bootstrapped and resumable with:

```text
.venv/bin/python scripts/p36_shadow_campaign.py
```

No real Shadow observations were synthesized. P3.7 corrected the terminology: a campaign is not
`COLLECTING` merely because this state file exists. Review eligibility remains blocked by:

- need at least 20 observations; have 0
- need at least 2 real reset cycles; have 0

## Acceptance Boundary

Implemented in this phase:

- credential-safe provider-surface evidence records;
- durable Shadow campaign state;
- deterministic grouped Shadow summaries by provider, quota pool and task family;
- explicit summary separation between `review_eligible` and production ACTIVE authorization;
- regression coverage for exact, estimated, unknown, auth-required and provider-error quota
  classifications;
- regression coverage proving Shadow review eligibility cannot create owner approval.

Still pending:

- automatic Shadow observation wiring must be enabled on a real execution path before the campaign
  can move from `BOOTSTRAPPED` to `COLLECTING`;
- supported credential handoff for MiniMax Token Plan quota API;
- supported credential handoff or local provider setup for Z.AI / GLM quota API;
- real Shadow observations across at least two reset cycles;
- explicit production ACTIVE owner approval, which must not be created by the campaign.

## P3.7 correction

P3.6 evidence must not be silently reinterpreted. P3.6 created provider-surface evidence and a
durable campaign bootstrap, but it did not prove automatic verified-task observation collection and
did not make reset-cycle IDs authoritative. P3.7 hardens those paths in code and tests; this
document remains a record of the original live-provider boundary.
