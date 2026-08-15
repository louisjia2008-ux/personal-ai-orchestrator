# ACP Feasibility Spike — Test Plan

Tracking issue: #1

## Purpose

This spike answers one question: **can the initial worker set be safely supervised through a common ACP-oriented boundary on the target Mac mini?**

This is not production architecture. All write experiments must use disposable repositories/worktrees.

## Environment record

Before testing, capture into `spikes/acp/results/environment.md`:

- macOS version and architecture;
- Python version;
- Node/npm/npx versions;
- `uv` version;
- Git version;
- OpenHands version/commit if used;
- OpenCode version;
- exact Codex ACP wrapper package + resolved version;
- exact Claude ACP wrapper package + resolved version;
- authentication method detected for each provider (never copy tokens into results);
- test repository base SHA.

## Test matrix

| ID | Worker | Mode | Expected result |
|---|---|---|---|
| ACP-001 | Codex | read-only | Session initializes, prompt runs, updates stream, no repo mutation. |
| ACP-002 | Claude Code | read-only | Session initializes, prompt runs, updates stream, no repo mutation. |
| ACP-003 | OpenCode/MiniMax | read-only | Session initializes, prompt runs, updates stream, no repo mutation. |
| ACP-004 | OpenCode/MiniMax | edit + test | Only disposable task worktree changes; deterministic test passes. |
| ACP-005 | Codex | cancel | Cancellation terminates/settles session and leaves no uncontrolled child worker. |
| ACP-006 | Claude Code | cancel | Same cancellation invariant. |
| ACP-007 | OpenCode/MiniMax | cancel | Same cancellation invariant. |
| ACP-008 | all | auth | Existing provider-native login is reused; no credential copied into repository/results. |
| ACP-009 | all | permission | Permission-request behavior is recorded; external policy remains authoritative. |
| ACP-010 | all | telemetry | Available session usage/cost/progress data is captured without treating it as weekly quota truth. |

## Read-only task

Use the same deterministic task for each worker:

1. Inspect a disposable repository.
2. Report current branch and base commit.
3. Identify one specified function/file and summarize its purpose.
4. Do not modify files.

Record:

- start/end timestamps;
- command used;
- resolved adapter version;
- stop reason;
- event/update sequence summary;
- repository status before/after;
- errors/retries;
- whether cancellation was required.

## Edit task

OpenCode/MiniMax must receive a small fixture task with an unambiguous test, for example adding a pure function and one unit test in a disposable sample project.

Before worker launch, record:

```text
BASE_SHA=<sha>
MAIN_STATUS=<git status --porcelain>
WORKTREE_PATH=<disposable path>
```

After completion:

- run the trusted test independently;
- run `git diff --check` independently;
- list changed files;
- confirm main repository base SHA and worktree status are unchanged outside the disposable task worktree.

## Cancellation test

For each worker:

1. launch a task that lasts long enough to cancel;
2. request ACP/session cancellation;
3. measure time to stop/settle;
4. inspect child processes;
5. verify no new filesystem changes occur after cancellation is acknowledged;
6. record whether additional OS-level process termination was necessary.

ACP cancellation alone is not assumed to be a sufficient process-supervision boundary.

## Permission test

Explicitly exercise an action likely to trigger a permission request in a disposable environment.

Record:

- which component requested permission;
- whether the client surfaced it;
- whether any reused runtime auto-approved it;
- whether the host can still block the underlying filesystem/process action externally.

A successful auto-approval reproduction is **not** a safety pass; the pass condition is that the external boundary prevents that behavior from becoming authoritative.

## Result structure

Create:

```text
spikes/acp/results/
  environment.md
  codex.md
  claude.md
  opencode-minimax.md
  cancellation.md
  permissions.md
  telemetry.md
  FINAL_SPIKE_REPORT.md
```

Do not commit credentials, auth files, raw tokens, or secret-bearing logs.

## Final report contract

`FINAL_SPIKE_REPORT.md` must include:

- `FINAL_STATUS`: `GO`, `NO_GO`, or `PARTIAL`;
- tested versions;
- results for ACP-001 through ACP-010;
- exact blockers;
- security observations;
- main-repository isolation evidence;
- cancellation evidence;
- telemetry availability;
- adapter versions recommended for pinning;
- explicit list of what remains unproven.

## Gate

P0 may begin only after the project owner accepts the spike evidence. A worker's own claim that the spike passed is insufficient.
