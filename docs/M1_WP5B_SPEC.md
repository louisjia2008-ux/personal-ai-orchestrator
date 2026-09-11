# M1 WP5b · SUPERVISED_AUTO macOS control surfaces

> Baseline: `main@523a0ae8d327222e780a628448c2e3f011f8b9ae` after Phase A integration.
> WP5a-2 backend authority is accepted and frozen. WP5b is a client/UI work package.

## 0. Goal

Make the existing host-owned `SUPERVISED_AUTO` lifecycle understandable and controllable from the native macOS app without creating any new execution authority.

The owner must be able to see a live AUTO lifecycle, understand whether it is waiting for acknowledgement or counting down, veto it, dispatch it immediately, switch the global mode between `MANUAL` and `SUPERVISED_AUTO`, configure project-level auto policy, and stop supervised autonomy from the menu bar.

## 1. Frozen authority boundary

WP5b MUST NOT:

- change Safety Kernel state-machine semantics;
- create a new dispatch path;
- bypass task `state_version` optimistic concurrency;
- mutate `RoutingDecision` / shadow evidence directly;
- change plugin authority;
- enable Production `ACTIVE`;
- start WP6 / WP7.

Every mutation goes through the already-shipped typed control API.

## 2. Existing backend contracts to consume

### Task AUTO fields

`TaskView` already exposes:

- `auto_decision_id` — lifecycle/cycle id `auto-{task_id}-v{state_version-at-planning}`; it is **not** `RoutingDecision.decision_id`;
- `auto_grace_deadline_at`;
- `auto_acked_at`;
- `auto_reason`.

The stale Swift comment that calls `auto_decision_id` the frozen `RoutingDecision.decision_id` must be corrected in this WP.

### Task AUTO actions

- `POST /v1/tasks/{id}/auto/ack`
  - body: `{ "task_state_version": Int }`
  - first ACK requires exact version;
  - already-ACKed retry is idempotent and must not extend the deadline.

- `POST /v1/tasks/{id}/auto/veto`
  - body: `{ "request_id": String, "task_state_version": Int }`
  - backend returns the task to `READY`, forces task scheduling policy to `MANUAL`, and performs the accepted crash-safe cleanup.

- `POST /v1/tasks/{id}/auto/dispatch-now`
  - body: `{ "task_state_version": Int }`
  - valid only for the current `AUTO_GRACE` lifecycle; backend owns re-admission and dispatch.

The Swift client must fetch/use authoritative task state/version and surface 409/stale outcomes as refreshed state, not as locally invented success.

### Global scheduling mode

`GET /v1/settings/scheduling` returns the existing `SchedulingSettingsView`, including `mode` / `selectable_modes`.

`PUT /v1/settings/scheduling` requires `default_scheduling_policy` and accepts optional `mode`.

Therefore a Swift `setSchedulingMode` call MUST preserve the current daemon-provided `default_scheduling_policy`; it must not overwrite the policy with a client default.

WP5b exposes owner-selectable `MANUAL` and `SUPERVISED_AUTO`. `ACTIVE` may be displayed as backend state/capability but MUST NOT be presented as an ordinary enable button in this WP. The existing backend activation gate remains authoritative.

### Project policy

`PUT /v1/projects/{id}/settings` body is the complete tuple:

- `supervised_auto_allowed: Bool`
- `unattended_allowed: Bool`
- `grace_seconds: Int`

The backend validates `grace_seconds` and disabling `supervised_auto_allowed` aborts that project's current AUTO lifecycles fail-closed.

The client must submit the complete current tuple and must not silently reset sibling values when changing one field.

## 3. Task-detail AUTO surface

Add a single prominent supervised-auto status surface to Task Detail for `AUTO_PLANNED` / `AUTO_GRACE` tasks.

It must show only authoritative data:

- lifecycle state;
- frozen selected target when available from the routing read model (never guess a target);
- acknowledgement state;
- grace deadline;
- remaining time derived from the daemon deadline and a local display clock;
- `auto_reason` when useful.

Presentation states:

1. `AUTO_PLANNED` — planning is frozen / promotion pending; VETO allowed, no fake countdown.
2. `AUTO_GRACE` + `auto_grace_deadline_at == nil` — waiting for owner ACK; show ACK, VETO, DISPATCH NOW as allowed by backend contract.
3. `AUTO_GRACE` + deadline — live countdown; show VETO and DISPATCH NOW. ACK must not be shown as a way to extend the deadline.
4. deadline locally reaches zero before refresh — render `dispatch pending / refreshing`, disable duplicate mutation controls, refresh authoritative state; do not locally claim RUNNING.

AUTO actions must have per-action in-flight state so rapid clicks cannot launch duplicate client requests.

## 4. Menu-bar emergency stop

Add a clearly separated `急停自动执行 / Stop supervised auto` control when the global mode is `SUPERVISED_AUTO`.

Its implementation is **not** a new kill switch endpoint. It performs the existing global scheduling update to `mode = MANUAL`, preserving the current default scheduling policy. The backend's accepted mode-change abort semantics remain the only authority.

The menu bar must show the current global scheduling mode. It must never claim the stop succeeded until the daemon response confirms `MANUAL`.

## 5. Settings UI

### Global

Add a mode card/picker:

- `MANUAL` — selectable;
- `SUPERVISED_AUTO` — selectable;
- `ACTIVE` — informational / gated, not normally selectable in WP5b.

Explain that `SUPERVISED_AUTO` plans and waits for the configured grace/ack policy, while Production ACTIVE remains separately gated.

### Project

For each registered project expose:

- Allow supervised auto;
- Allow unattended countdown;
- Grace period seconds.

Rules:

- editing one setting preserves the other two current daemon values;
- `unattended_allowed` and grace controls may be visually disabled while supervised auto is off, but their stored values must not be silently changed;
- disabling supervised auto must warn that current AUTO lifecycles for that project will be aborted by the daemon;
- errors remain sanitized daemon error codes/details.

## 6. Client/store additions

Expected additions are narrowly client-side:

- typed `PAOControlClient` methods for ACK / VETO / DISPATCH NOW;
- typed scheduling-mode update preserving `default_scheduling_policy`;
- typed project supervised-auto settings update;
- `OrchestratorStore` operations and structured notices/in-flight state;
- refresh after every mutation;
- treat `AUTO_PLANNED` / `AUTO_GRACE` as high-attention states for refresh cadence while visible.

Do not persist a second client-side source of truth.

## 7. Countdown logic

Countdown rendering must be deterministic and testable:

- parse `auto_grace_deadline_at` using the existing timestamp parser;
- remaining = `max(0, deadline - now)`;
- never derive authority from remaining time;
- local zero only triggers presentation + refresh; it does not mutate task state;
- use a 1-second UI clock only while an AUTO grace surface is visible; do not globally redraw unrelated pages every second.

Prefer a small pure presentation helper/model with unit tests rather than embedding date arithmetic in a large SwiftUI body.

## 8. Localization / accessibility

Add en + zh-Hans keys for:

- supervised-auto mode names/help;
- waiting-for-ack;
- countdown / expired-refreshing;
- ACK / VETO / DISPATCH NOW;
- emergency stop + confirmation/error text;
- project toggles and grace help;
- ACTIVE gated explanation.

All actionable icon-only controls need accessibility labels. Countdown must expose a stable textual accessibility value.

## 9. TestDaemon and tests

TestDaemon must support canned responses for:

- GET + PUT scheduling settings with mode;
- PUT project supervised-auto settings;
- AUTO ACK;
- AUTO VETO;
- AUTO DISPATCH NOW;
- representative 409 stale/state conflicts.

Required Swift tests:

- current + legacy decoding remains compatible;
- `auto_decision_id` contract comment/fixtures align with lifecycle id semantics;
- mode update preserves current default policy;
- emergency stop sends `MANUAL`, not a local-only toggle;
- project setting edit preserves sibling fields;
- ACK request version;
- VETO request id + version;
- DISPATCH NOW version;
- waiting-for-ACK presentation;
- countdown math before / at / after deadline;
- stale 409 produces no false-success UI and triggers authoritative refresh;
- ACTIVE is not exposed as ordinary owner-enable action;
- existing owner-dispatch / resources / routing / localization tests remain green.

## 10. Expected file scope

Likely 10–18 files, primarily under:

- `macos/PAOMenuBar/Sources/PAOControlKit/`
- `macos/PAOMenuBar/Sources/PAOMenuBar/Tasks/`
- `macos/PAOMenuBar/Sources/PAOMenuBar/`
- `macos/PAOMenuBar/Tests/PAOControlKitTests/`
- localization files;
- this spec / integration-status docs.

If implementation requires backend Safety Kernel/executor changes or exceeds 24 files, stop and report scope before proceeding.

## 11. Verification gate

Before merge:

- `swift test --package-path macos/PAOMenuBar` PASS;
- existing Python suite remains green if any shared contract file is touched;
- OpenCode adapter contract remains unchanged/green;
- `git diff --check` PASS;
- no credential material;
- no `plugin.ts` authority change;
- Production ACTIVE remains `DISABLED_BY_DESIGN` absent activation authority;
- human visual acceptance is required for the new countdown / settings / menu-bar surfaces before WP5b is declared product-complete.

No merge and no WP6 until independent WP5b review accepts the branch.
