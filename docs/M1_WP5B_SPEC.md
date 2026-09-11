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

---

## 12. Delivery record (WP5b implementation)

Status: technical implementation complete; **human visual acceptance
required** before the WP is declared product-complete (§11).

### Surfaces implemented

- **Typed client** (`ControlClient.swift`): `autoAck` / `autoVeto` /
  `autoDispatchNow` / `setSchedulingMode(mode, defaultSchedulingPolicy)`
  (the authoritative policy is always echoed back — never a client
  default) / `setProjectSupervisedAutoSettings` (complete tuple).
  Response models verified against `control_api.py`: ACK/VETO →
  `TaskView`, DISPATCH-NOW → `DispatchTaskView`, mode →
  `SchedulingSettingsView`, project → `ProjectView`.
- **Pure presentation** (`SupervisedAutoPresentation.swift`, new):
  `AutoSupervisionPresentation` derives phase (inactive / planned /
  waitingAck / countingDown / expiredRefreshing), deadline,
  `secondsRemaining` (`max(0, deadline − now)`, rounded up, never
  negative), and canAck / canVeto / canDispatchNow offers. Local expiry
  never claims RUNNING. Also `ManualDispatchPolicy` (SUBMITTED/READY
  only), `MenuBarAutoStop.shouldOffer` (SUPERVISED_AUTO only),
  `AutomationModeCatalog` (MANUAL + SUPERVISED_AUTO selectable; ACTIVE
  display-only).
- **Store operations** (`OrchestratorStore.swift`): `AutoControlAction`
  / `AutoControlNotice` (acknowledged / vetoed / dispatchRequested /
  staleState / blocked(code) / schedulingModeChanged /
  supervisedAutoStopped / projectAutoSettingsSaved / failed /
  malformed), per-task in-flight coalescing (no duplicate parallel
  vetoes; one `request_id` per veto operation), authoritative
  re-fetch before every mutation, authoritative reload after every
  answer (including 409s), `highAttentionTaskStates` adds AUTO_PLANNED
  / AUTO_GRACE to the fast refresh cadence.
- **Task surface** (`TaskAutoSupervisionSection.swift`, new): the one
  supervised-auto card in Task Detail — planned / waiting-ack /
  countdown / expired-refreshing states, frozen target from the
  authoritative routing read model (unknown shown truthfully),
  `auto_reason`, ACK/VETO/DISPATCH NOW with in-flight disable, a
  1-second `TimelineView` only while a deadline is actually displayed,
  one authoritative reload at local expiry. Manual dispatch panel uses
  `ManualDispatchPolicy` and never renders for AUTO states.
- **Menu bar** (`MenuBarContentView.swift`): current scheduling mode
  always visible; `急停自动执行` offered only in SUPERVISED_AUTO; its
  implementation is the daemon PUT to `mode=MANUAL` (policy preserved)
  and success is reported only from the returned settings.
- **Settings** (`DashboardView.swift`): automation-mode card (MANUAL /
  SUPERVISED_AUTO selectable; ACTIVE displayed with its gate
  explanation, lock symbol, and authoritative-state-only note).
- **Project settings** (`DashboardView.swift`): per-project supervised
  auto / unattended / grace editors; nil fields resolve from the
  authoritative project view so siblings are never silently reset;
  disabling supervised auto requires confirmation (the daemon aborts
  that project's AUTO lifecycles); grace input validated 1–86400 with
  the daemon's `invalid_grace_seconds` still surfacing verbatim.
- **Identifier doc fix** (`APIModels.swift`): `autoDecisionId` is
  documented as the lifecycle id `auto-{task_id}-v{state_version-at-
  planning}` (chain: auto id → `supervised-auto-{id}` request id →
  `route-{digest}` decision id; pending `pending_id == autoDecisionId`).
- **Localization**: ~50 new keys in en + zh-Hans catalogs and
  `L10n.requiredKeys`; no hard-coded user-visible English in the new
  surfaces. Icon-only controls carry accessibility labels; the
  countdown exposes a stable textual accessibility value.

### Test coverage

- `AutoGracePresentationTests.swift` (14): PRESENTATION-1..6, clock
  formatting, unknown-target truth, UI-1 (manual dispatch not offered
  for AUTO states), MENU-1, MODE-3, AUTO-field decode with lifecycle-id
  semantics.
- `SupervisedAutoControlTests.swift` (12): CLIENT-1..3 (exact wire
  bodies), MODE-1 (authoritative `default_scheduling_policy`
  preserved), MODE-2 (emergency stop = daemon PUT to MANUAL; failure
  never reported as success), PROJECT-1..3 (sibling preservation on
  the wire) + sanitized `invalid_grace_seconds`, RACE-1 (409 stale →
  no false success + authoritative reload; invalid-auto-state 409 the
  same), duplicate-ACK coalescing.
- TestDaemon: canned GET/PUT scheduling (incl. a SUPERVISED_AUTO body
  with a non-default policy), PUT project settings, ACK/VETO/
  DISPATCH-NOW fixtures, and 409 `stale_task_state_version` /
  `task_state_not_auto_grace` using the daemon's exact codes.
- Full suite: Swift 467 (441 + 26), Python 1000, OpenCode 17 — all
  green; `git diff --check` clean; no TODO/FIXME/HACK/
  NotImplementedError in touched code.

### Authority boundary confirmation

No Safety Kernel / executor / plugin change; no client-side state
transition; no new dispatch path; Production ACTIVE still
`DISABLED_BY_DESIGN` and not offered as an ordinary enable action.

### Known limitations / acceptance

- Human visual acceptance outstanding for: AUTO grace card (all four
  phases), Settings mode picker, project auto settings, menu-bar stop.
- App bundle built at `macos/PAOMenuBar/dist/Personal AI
  Orchestrator.app` from the final WP5b head for that acceptance pass.
