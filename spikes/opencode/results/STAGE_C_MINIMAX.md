# OpenCode Stage C — MiniMax: PASS

Real provider-native runtime proof against the authenticated MiniMax coding plan.
Sanitized machine evidence: [`stage_c_minimax_evidence.json`](./stage_c_minimax_evidence.json).

## Identity

| Field | Value |
| --- | --- |
| Provider display name | MiniMax Token Plan (minimaxi.com) |
| Provider id | `minimax-cn-coding-plan` |
| Model id | `MiniMax-M2.5` |
| Model variant | `default` (OpenCode-normalized) |
| OpenCode | 1.18.23 |
| Auth present | YES (credential type: api) |
| Credentials exposed | NO |

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Auth metadata present | PASS | provider in `auth.json` credentials block |
| Model in real catalog | PASS | `minimax-cn-coding-plan/MiniMax-M2.5` |
| SHADOW non-invasive | PASS | adapter action `RECORD_ONLY`; session model stayed `null` |
| ACTIVE selects real model | PASS | adapter action `SWITCH_MODEL`; session A → `minimax-cn-coding-plan/MiniMax-M2.5` |
| Second session unchanged | PASS | session B model `null`, cost 0, tokens 0 |
| Real completion | PASS | `finish=stop`; text = fixture H1 |
| Fixture unchanged | PASS | `git status --short` empty; `git diff --check` clean |
| Daemon session accounting | PASS | 1 SHADOW + 1 ACTIVE, both session A; `active_route_count=1`; B never routed |
| Cancellation | PASS | in-flight interrupt → `finish=error`, marker `Provider turn interrupted`, server healthy |
| Credential leak | NONE | no auth file read/copied/logged |

## Real completion

- Prompt: "Respond with exactly this line and nothing else, no preamble: # OpenCode Stage C Disposable Fixture"
- Response text: `# OpenCode Stage C Disposable Fixture` (matches the fixture H1 exactly)
- `finish`: `stop`
- Tokens: `input=184, output=31, reasoning=0, cache={read:2727, write:313}`
- Cost: `0` (subscription/token-plan; OpenCode reported 0)
- Quota remaining: `UNKNOWN` (OpenCode does not expose plan quota)
- Provider errors: none

## Path proven

```
fake routing daemon (credential-free)
  -> RoutingRequest/RoutingDecision
  -> resolve_adapter_outcome()  ->  SWITCH_MODEL(minimax-cn-coding-plan/MiniMax-M2.5)
  -> POST /api/session/A/model  (session.next.model.switched)
  -> POST /api/session/A/prompt -> real MiniMax completion
  -> session B provably untouched
```

The orchestrator never received provider credentials; OpenCode owned auth throughout.
