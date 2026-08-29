# MiniMax Quota Observation

Status: `AUTHENTICATION_INTEGRATION_BLOCKED`

As of: 2026-08-29T01:08:00Z

## Official surface

- Provider: MiniMax
- OpenCode provider: `minimax-cn-coding-plan`
- Plan/pool: Token Plan / CN region
- Official read-only endpoint for current MVP region: `GET https://api.minimaxi.com/v1/token_plan/remains`
- Global Token Plan page separately documents: `GET https://www.minimax.io/v1/token_plan/remains`
- Authentication: Bearer Token Plan subscription key
- Quota windows: 5-hour rolling + weekly
- Region evidence: official MiniMax CLI documents `api.minimaxi.com` for CN and builds quota requests with `/v1/token_plan/remains`

## Runtime probe

A live quota request was **not** sent in this execution surface.

Reason: the existing MiniMax credential is owned by OpenCode on the user's machine, while the connected GitHub execution surface has no supported credential handoff. Reading or copying the OpenCode auth store is explicitly forbidden, and no new credential may be requested merely to make the probe pass.

`MINIMAX_RUNTIME_QUOTA_PROBE = AUTHENTICATION_INTEGRATION_BLOCKED`

## Sanitized observation

- remaining: `UNKNOWN`
- reset: `UNKNOWN`
- pace: `UNKNOWN`
- effective pace: `UNKNOWN`
- confidence: `UNKNOWN` for this runtime observation
- credentials read: NO
- credentials logged: NO
- raw provider response committed: NO
- completion quota consumed for this probe: NO

The collector implementation and mocked normalization tests are authoritative for code behavior; this file does not claim runtime acceptance.
