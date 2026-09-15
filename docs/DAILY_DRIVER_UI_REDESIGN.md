# PAO Daily Driver UI Redesign

Status: DESIGN CONTRACT — implementation not yet accepted

Baseline: `main@d42878b668e5ee572d57df1460f51338046d9d6c`

## Product goal

Turn Personal AI Orchestrator from an engineering/admin dashboard into a native macOS daily-driver for one owner. The primary workflow is:

`Open app -> understand system readiness -> submit/select task -> see chosen execution target and quota impact -> supervise or veto -> watch verified completion.`

The UI is a client. It must not move scheduling, quota, Safety Kernel, verifier, dispatch, or approval authority into Swift presentation code.

## Design principles

1. **Task first, infrastructure second.** The first screen answers: what is running, what needs attention, and can I safely start work?
2. **Progressive disclosure.** Provider internals, execution verification evidence, snapshot IDs, reason codes, process metadata, and raw gates belong behind disclosure/inspector/Advanced surfaces.
3. **Native macOS hierarchy.** Prefer split views, grouped lists/forms, toolbar actions, system materials, dividers, badges, inspectors and sheets over a wall of bordered cards.
4. **One primary action per context.** New Task is the global primary action. Task detail may expose Dispatch/ACK/Veto when authoritative state permits. Infrastructure pages do not compete with task actions.
5. **Human-readable first, machine truth preserved.** Translate internal states into concise owner-facing language while keeping exact raw state/evidence accessible one level deeper.
6. **Quota is commercial resource truth.** Never present shared plan quota as independent per-model balances. Provider -> plan/pool -> execution target hierarchy remains intact.
7. **No fabricated certainty.** UNKNOWN, ESTIMATED, stale execution evidence, unavailable scheduling mode, and blocked targets remain visibly distinct.
8. **Safety controls are visually stable.** Veto, emergency stop, cancellation, and MANUAL fallback never move unpredictably or masquerade as success before daemon confirmation.

## Information architecture

Keep five top-level destinations, but redefine their product roles:

### 1. Home

Purpose: daily operating surface, not analytics dashboard.

Top region:
- System status: Ready / Needs attention / Paused.
- Scheduling mode: MANUAL or SUPERVISED AUTO; ACTIVE only as gated status.
- One prominent `New Task` action.

Primary content:
- **Now** — currently running / grace / verifying task. Show intent, target, stage, elapsed time and the one relevant action.
- **Needs Attention** — blocked tasks, stale provider auth, quota exhaustion/uncertainty, verification failures. Maximum 3–5 rows before `Show all`.
- **Capacity** — MiniMax and GLM plan/pool summaries with remaining window(s), reset time, confidence and reserve state. This is deliberately plan/pool level, not a model-card grid.
- **Recent** — last verified/blocked tasks as a compact activity feed.

Remove from Home:
- engineering KPI tile wall;
- large generic charts unless they answer a current operational question;
- raw daemon event codes;
- provider/model inventory tables.

### 2. Tasks

Purpose: the main workspace.

Use a two-column collection/detail workspace plus optional inspector:
- collection: search, state filter, compact task rows;
- detail: task intent, lifecycle timeline, selected target, quota rationale, verification outcome and context-sensitive actions;
- inspector: IDs, worktree, run/process metadata, immutable evidence and audit details.

Task detail hierarchy:
1. intent + current state;
2. primary action / supervision card;
3. execution target + concise `Why this target` explanation;
4. progress timeline: Planned -> Waiting -> Running -> Verifying -> Verified/Blocked;
5. result/evidence summary;
6. technical details under disclosure or inspector.

### 3. Resources

Purpose: answer `What AI capacity do I have right now?`

Replace long provider/model rows with a hierarchical master/detail experience:
- left/master: provider or commercial plan groups (MiniMax, GLM/Z.AI, Codex, etc.);
- detail header: auth health, quota health, next reset, confidence;
- quota windows: 5h / weekly / balance as appropriate;
- execution targets: only usable/relevant targets first;
- `Unavailable / unverified targets` collapsed by default;
- technical source/snapshot information under Advanced.

The owner should not have to read repeated `UNVERIFIED` rows to learn whether MiniMax can run a task.

### 4. Activity

Purpose: operational history and audit, not a second dashboard.

Default view is human-readable task/run events grouped by day. Raw event type is secondary metadata. Add filters for Task / Routing / Quota / Safety when needed.

### 5. Settings

Purpose: owner policy, not backend gate dump.

Use native grouped settings/form sections:
- General — launch behavior, notifications, language where supported;
- Automation — MANUAL / SUPERVISED AUTO, grace behavior, emergency fallback;
- Projects — per-project supervised-auto/unattended/grace configuration;
- Resource Policy — routing preset, reserves/protection, paid-usage policy;
- Advanced — daemon/build identity, raw production ACTIVE gates, provider diagnostics, technical reset/recovery controls.

Production ACTIVE should read as a concise owner-facing locked state, e.g. `Full automation is not available yet because production evidence requirements are not satisfied.` Raw P0/P1/gate reason codes live under Advanced disclosure.

## Visual system

### Window and layout
- Keep native `NavigationSplitView`.
- Sidebar width remains stable across destinations.
- Content should use available width; do not globally cap dashboard/table pages to a narrow column.
- Settings alone may use a centered readable form width.
- Minimum supported window must remain usable without clipping primary actions.

### Type hierarchy
- Window toolbar/navigation title: system title behavior.
- Primary task/status headline: `.title2` / `.title3` depending on context.
- Section heading: `.headline`.
- Body: `.body` / `.callout`.
- Metadata: `.caption` / monospaced only for IDs and machine values.
- Do not use tiny/light typography as the default information-density solution.

### Surfaces
- Prefer one grouped surface with dividers over many nested bordered cards.
- Use cards only for genuinely independent status/action modules.
- Avoid card-inside-card.
- Use semantic color only for state: success, warning, blocked/destructive, selected/accent.
- UNKNOWN/ESTIMATED use labels/badges plus text, not color alone.

### Spacing
Use a 4pt grid. Recommended semantic tokens:
- 4: micro
- 8: tight
- 12: inner
- 16: element
- 24: section
- 32: major section

## Home wireframe

```text
┌ Sidebar ───────┬─────────────────────────────────────────────────────┐
│ Home           │ Home                              [ + New Task ]   │
│ Tasks          │ Ready · SUPERVISED AUTO                             │
│ Resources      │                                                     │
│ Activity       │ NOW                                                 │
│ Settings       │ ┌─────────────────────────────────────────────────┐ │
│                │ │ Fix PinTrace club duplication                  │ │
│                │ │ MiniMax M3 · Running · 04:12                   │ │
│                │ │ Worktree isolated · verifier pending           │ │
│                │ └─────────────────────────────────────────────────┘ │
│                │                                                     │
│                │ NEEDS ATTENTION                                     │
│                │  GLM weekly quota low                 Reset 18h    │
│                │  1 blocked task                       Review  >    │
│                │                                                     │
│                │ CAPACITY                                            │
│                │  MiniMax Token Plan  5h 82% · week 41% · Exact    │
│                │  GLM Coding Plan    5h 67% · week 18% · Exact     │
│                │                                                     │
│                │ RECENT                                              │
│                │  ✓ Refactor routing policy             8 min ago  │
│                │  ✓ Add verifier tests                  31 min ago  │
└────────────────┴─────────────────────────────────────────────────────┘
```

## Task wireframe

```text
┌ Task list ──────────────┬────────────────────────────────────────────┐
│ Search                  │ Fix PinTrace club duplication             │
│ Running 1 · Ready 4     │ RUNNING · MiniMax M3                      │
│                         │                                            │
│ ● PinTrace duplication  │ [ Cancel ]                                 │
│ ○ PAO UI closeout       │                                            │
│ ○ DeskPet Live2D        │ Progress                                   │
│                         │ Planned ✓  Running ●  Verifying ○          │
│                         │                                            │
│                         │ Why MiniMax M3                              │
│                         │ Healthy shared plan quota; task fits       │
│                         │ reserve; no higher-risk escalation needed. │
│                         │                                            │
│                         │ Verification                               │
│                         │ Waiting for deterministic verifier         │
│                         │                                            │
│                         │ ▸ Technical details                        │
└─────────────────────────┴────────────────────────────────────────────┘
```

## Resources wireframe

```text
┌ Resources ──────────────┬────────────────────────────────────────────┐
│ MiniMax Token Plan      │ MiniMax Token Plan              Healthy   │
│ GLM Coding Plan         │ Auth ready · Exact quota                  │
│ Codex                   │                                            │
│ Local                   │ Quota                                      │
│                         │ 5-hour     82%       resets 2h 14m         │
│                         │ Weekly     41%       resets Sun            │
│                         │ Reserve    15%                              │
│                         │                                            │
│                         │ Available targets                          │
│                         │ MiniMax M3 via Pi               Verified   │
│                         │ MiniMax M3 via OpenCode         Verified   │
│                         │                                            │
│                         │ ▸ Unavailable / unverified (4)             │
│                         │ ▸ Advanced quota evidence                  │
└─────────────────────────┴────────────────────────────────────────────┘
```

## Settings wireframe

```text
Automation
  Scheduling mode      [ Manual | Supervised Auto ]
  Full automation      Locked — evidence requirements not yet met

Supervised Auto
  Default grace        10 seconds
  Require acknowledgement for attended tasks     On
  Emergency fallback   Switch to Manual

Projects
  PinTrace             Supervised Auto · 10s · attended
  PAO                  Supervised Auto · 10s · attended

Resource Policy
  Routing preset       Balanced
  Protect reserves     On
  Paid overage         Off

Advanced ▸
```

## Implementation order

### Round 1 — Shell + Home
- freeze navigation to five product destinations;
- implement the new Home hierarchy;
- remove KPI/card-wall/admin-dashboard presentation;
- preserve all existing navigation intents and authoritative data sources;
- build Release and stop for owner screenshots at normal and wide window sizes.

### Round 2 — Tasks
- simplify task rows and detail hierarchy;
- add lifecycle timeline and concise target rationale;
- move technical IDs/process/worktree/evidence into inspector/disclosure;
- integrate current-main SUPERVISED_AUTO controls without reviving the stale PR branch wholesale;
- build and stop for owner screenshots.

### Round 3 — Resources
- introduce provider/plan master-detail hierarchy;
- quota windows first, execution targets second;
- collapse unavailable/unverified targets;
- retain exact/estimated/unknown semantics and shared-pool truth;
- build and stop for owner screenshots.

### Round 4 — Settings + Activity + Menu Bar
- native grouped settings;
- owner-facing ACTIVE lock explanation + Advanced raw gates;
- human-readable activity feed;
- menu bar reduced to status, current task, quota warning, mode/emergency stop, New Task/Open Dashboard;
- build and stop for final human acceptance.

### Round 5 — Daily-driver acceptance
- current-main MiniMax and GLM real task smoke;
- MANUAL and SUPERVISED_AUTO flows;
- veto/cancel/emergency-stop;
- restart/recovery;
- no orphan process, writer leak, main-repo mutation, or fabricated UI state;
- final owner visual/interaction acceptance.

## Non-goals during redesign

- no new provider;
- no new harness;
- no scheduler redesign;
- no Safety Kernel/verifier semantic change;
- no Production ACTIVE enablement;
- no Telegram/DeskPet integration;
- no public-release/license work;
- no adaptive/ML routing;
- no migration of stale PR #28 by merge/rebase as a whole.

## Acceptance standard

The redesign is accepted when the owner can open the app and, without reading backend terminology, answer within a few seconds:

1. Is PAO ready to work?
2. What is running or waiting for me?
3. How much useful MiniMax/GLM capacity remains?
4. Why did PAO choose this execution target?
5. What action can I take now?
6. Did deterministic verification actually pass?

The exact backend truth must remain available, but it should not dominate the default product surface.