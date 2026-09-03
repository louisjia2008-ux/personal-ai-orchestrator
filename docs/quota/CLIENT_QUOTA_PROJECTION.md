# Client quota projection — a forecast, and never anything more

Implemented in `macos/PAOMenuBar/Sources/PAOControlKit/QuotaSeries.swift` and
`QuotaProjection.swift`. Tested in `QuotaSeriesTests.swift` and
`QuotaProjectionTests.swift`.

Status labels are defined in `QUOTA_DOMAIN_MODEL.md`.

> This is Dashboard explanation, not scheduling policy. Nothing computed here
> reaches the daemon, and nothing here may become a routing input. The daemon
> remains the sole owner of scheduler and quota decisions.

## What this answers

Three questions the provider answers none of:

1. How quickly has this window been consumed?
2. At that rate, what is likely to remain when it resets?
3. Is paid quota likely to expire unspent?

## Series identity — CONFIRMED

A *series* is the tuple the daemon itself keys its history by, in
`safety_kernel.record_quota_observation`:

```
(provider_id, quota_pool_id, window_id)
```

All three are required. A pool is only meaningful inside its provider, and a
window only inside its pool. GLM and MiniMax both report a window called `5h`;
joining them produces a line describing neither.

Before B4, the history chart drew every stored observation into one bar row
regardless of all three, and read the rows newest-first (`ORDER BY observed_at
DESC`) while drawing them oldest-first. Four providers depleting four pools
appeared as one sequence that rose and fell for reasons present nowhere in the
data.

## Reset-window segmentation — CONFIRMED

A reset is a discontinuity, not a data point. Readings are split into segments,
and every derived figure is computed inside exactly one segment.

A boundary falls between two consecutive readings when **either**:

1. their reported `reset_at` values differ, including known → unknown; or
2. the earlier reading's `reset_at` has been reached by the time of the later
   one — the window ended, whether or not the daemon has observed its
   replacement yet.

The rule reads **reset identity and elapsed time only, never the quota value**.
Reading the value would make segmentation circular with the burn rate computed
over the segments it produces: a rise would create a boundary, and the boundary
would then hide the rise.

## Normalization — CONFIRMED

- Readings are sorted ascending by `observed_at`.
- A reading is dropped, not repaired, when it carries `UNKNOWN` confidence, no
  `remaining_fraction`, an unparseable timestamp, or a fraction outside `0…1`.
  Clamping `1.4` to `1.0` would invent a value the provider never reported.
- Two readings claiming the same instant collapse to one, keeping the **lower**
  percentage. The daemon stores a row per distinct value tuple, so one instant
  can legitimately carry two disagreeing readings. The rule is deterministic
  regardless of the order the rows arrive in, and it errs in the only direction
  a quota surface may: never reporting more headroom than the evidence supports.

## Gaps — CONFIRMED

**A missing sample means NO OBSERVATION, not NO USAGE.** Nothing is
synthesized, interpolated, or zero-filled for an unobserved interval. The chart
plots real observation times on a real time axis, so a six-hour gap is six hours
of empty space with no mark in it, and every observation carries a point mark so
six readings cannot be mistaken for two.

## Burn rate — DERIVED

```
rate = (first.remaining − last.remaining) / (last.observed_at − first.observed_at)
```

- **Observation window**: the current reset segment, entire. Not a trailing
  sample of *n* readings — a reset window is the semantically correct interval,
  and using it avoids an arbitrary constant that would need justifying.
- **Endpoints, not a regression.** A least-squares fit over six points would be
  no more accurate about a future the owner controls, and could not be explained
  in the one line the interface has room for.
- **Unit**: fraction of capacity per second, rendered per hour.
- **Flat consumption** (`rate == 0`) is a measurement, not an absence. Refusing
  it would leave the owner unable to tell a quiet window from an unobserved one.
- **A rising balance yields no rate at all** — see refusals below.

## Projection — FORECAST

```
remaining_at_reset = clamp(remaining_now − rate × seconds_until_reset, 0…1)
```

- **Horizon**: the reset instant. Nothing is projected past it, because a reset
  ends the window the rate describes.
- **Clamping** into `0…1` is where exhaustion is detected: an extrapolation that
  would fall below zero means the balance runs out first.
- **Exhaustion** is claimed only when `remaining / rate ≤ seconds_until_reset`,
  and is dated from the **last reading**, not from now. Dating it from a later
  instant would silently grant the window free time.
- **Likely unused quota** is the projected remainder when no exhaustion is
  expected. Quota expected to still be there at reset is quota expected to
  expire unspent — the harvest signal, stated as the estimate it is.

### Minimum data rule

A projection is produced only when **all** of the following hold. Each failure
names itself, because they lead to different owner actions:

| Refusal | Condition |
| --- | --- |
| `NO_HISTORY` | No usable reading was ever recorded for the series. |
| `INSUFFICIENT_OBSERVATIONS` | Fewer than two readings in the current segment. A slope is defined by two points and undefined by one. |
| `NO_ELAPSED_TIME` | Zero span between first and last reading. Guards the division directly. |
| `REMAINING_INCREASED` | The balance rose. Extrapolating it forward would forecast a surplus no provider promised. |
| `RESET_UNKNOWN` | No reset instant, so no horizon. **The burn rate survives this**; only the projection is refused. |
| `RESET_ALREADY_PASSED` | The reported reset has passed. The readings describe a window that has ended. |

There is no silent fallback. A refusal renders its sentence where the number
would have been, never a zero — absent and zero mean opposite things, and
conflating them tells the owner they have run out when they have not.

## Presentation rules — CONFIRMED

- The projection is `EvidenceLevel.forecast`: prefixed `≈`, and
  `mayFillMeter == false`. A bar filled from a projection would claim to *be*
  the provider's reading.
- The burn rate is `EvidenceLevel.derived` and says so beside itself.
- Observed readings carry no qualifier. Labelling every figure would make the
  qualifier invisible.

## Known backend gap — temporal scarcity is not shown

`quota_observability.py` defines the authoritative scarcity model:

```
pace            = remaining_fraction / remaining_time_fraction
scarcity_class  = CRITICAL | CONSERVE | ON_PACE | SURPLUS | HARVEST
thresholds      = 0.5 / 0.8 / 1.2 / 1.5   (DEFAULT_SCARCITY_THRESHOLDS)
```

`remaining_time_fraction` requires `window_started_at` or `duration_seconds` on
the window snapshot. **The control API exposes none of the three** — not
`scarcity_class`, not `pace`, and not the window-start or duration fields the
pace is built from. `QuotaPlanWindowView` and `QuotaWindowHealthView` carry
`remaining_fraction` and `reset_at` only.

The Dashboard therefore shows **no scarcity classification and no routing
recommendation**. Reproducing the thresholds in the Swift client would put a
copy of scheduler policy on the far side of the architecture boundary, where it
would drift silently; a "prefer Claude" the scheduler does not share is worse
than no recommendation at all.

What is shown instead is the evidence the verdict would rest on — remaining,
reset horizon, burn rate, projected remainder — with an explicit statement that
the classification is not published. The gap is stated rather than left as a
silent hole.

**To close it**, the daemon would need to publish either `scarcity_class`
directly on the quota window view, or `window_started_at` / `duration_seconds`
so the client can compute `remaining_time_fraction`. The first is preferable:
the classification is policy, and policy belongs to the daemon.

## Relationship to equivalent capacity

`EquivalentCapacityEstimate` (see `EQUIVALENT_CAPACITY.md`) is a **task count**
for the limiting window, computed server-side. It is not a percentage and is
never combined with the balances or the projection above — the units do not
support it. The two are rendered in separate sections for that reason.
