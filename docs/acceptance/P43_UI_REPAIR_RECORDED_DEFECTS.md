# Recorded defects — final UI repair round (2026-09-03)

This round was scoped to layout, hierarchy and spacing. Three data defects were
found while reviewing the real build against real daemon output. All three sit
inside the frozen areas (quota collection, quota evidence semantics, provider
identity), so they are recorded here and **not changed**.

Evidence below is the live `GET /v1/quota` response from the daemon at
`af709d7`, read on 2026-09-03.

---

## 1. Every quota binding is rendered twice

**Where** `macos/PAOMenuBar/Sources/PAOControlKit/ResourceCollection.swift`,
`ResourceSnapshot.quotaBindings`.

`quotaBindings` merges two sources and de-duplicates on `QuotaSeriesIdentity`
(`providerId` + `quotaPoolId` + `windowId`). The two sources disagree about
`providerId` for the same window, so the key never collides and both copies
survive:

| source | providerId | poolId | windowId |
|---|---|---|---|
| `plan.windows` | `minimax` (the plan's owner) | `minimax-token-plan-cn` | `5h`, `weekly` |
| `quota.quotaPools` | `minimax-cn` (the resource being viewed) | `minimax-token-plan-cn` | `5h`, `weekly` |

The daemon reports **one** pool with **two** windows for `minimax-cn`
(FIVE_HOUR 96%, WEEKLY 54%). Resources renders four cards: the same two
readings, twice, with identical numbers and reset times.

**Why it is not fixed here** `providerId` in `QuotaSeriesIdentity` is also the
key the history chart and the projection panel bind a card to its series with.
De-duplicating on `(poolId, windowId)` changes which series each card resolves
to, which is provider-identity and quota-evidence semantics.

## 2. Quota is never re-read on its own

**Where** `src/personal_ai_orchestrator/quota_refresh.py`,
`src/personal_ai_orchestrator/daemon.py`.

`QuotaRefreshService.refresh()` has exactly one caller: the
`POST /v1/quota/refresh` handler (`control_api.py`). Nothing in the daemon
schedules it — there is no periodic collector, at ten minutes or at any other
interval. Quota is observed only when the owner presses 刷新额度, and the
dashboard's own one-minute tick only re-renders countdowns against the last
observation; it does not fetch one.

A periodic read would be daemon and scheduler work, and it has a safety
dimension (`assert_read_only_collector`, per-provider rate limits) rather than
being a UI setting.

## 3. GLM / Z.AI shows a stale reading, not a post-reset one

The provider's last **successful** observation is 07:45:28Z. Every refresh since
has failed:

```
observed_at:         2026-09-03T07:45:28Z   <- the reading on screen
last_refresh_at:     2026-09-03T13:19:11Z
last_refresh_status: PROVIDER_ERROR
failure_reason:      COLLECTOR_FAULT
quota_state:         OBSERVED
```

Two separate things are being read as one:

- **WEEKLY** is at 0.33% and resets **2026-09-06T05:08Z**. It has not reset yet;
  0.33% is what the collection row rounds to `0%`, because the row shows the
  scarcest binding window.
- **FIVE_HOUR** was at 21% with `reset_at` 2026-09-03T12:07Z. That reset time
  has passed, so this reading is stale — but the collector cannot fetch a new
  one, and with no periodic refresh (defect 2) nothing retries.

The client is behaving as specified: it never fabricates a post-reset value, and
it marks the reading's age (`5 小时前观测`, in the caution colour). The defect is
the collector fault behind it, which needs the Z.AI collector's error to be
surfaced rather than reduced to `COLLECTOR_FAULT`.
