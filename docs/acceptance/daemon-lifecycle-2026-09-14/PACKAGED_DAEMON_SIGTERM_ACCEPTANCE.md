# Packaged daemon SIGTERM acceptance — 2026-09-14

Status: PASS

## Immutable build under test

- Source/build commit: `b1d6a660be074ddff085f485835f8ec6e419e4d8`
- Helper SHA-256: `ad00de62d72b1103109a2521f1cdbc920be19b89bf9dd3714ac697f814e0f887`
- Artifact: the actual PyInstaller one-file `Personal AI Orchestrator.app/Contents/Helpers/pao-daemon`, not `python -m`.
- State: one disposable home reused across all five cycles.

## Required signal matrix

- Outer PyInstaller parent: PASS in cycles 1, 3, and 5.
- Serving embedded-Python child: PASS in cycles 2 and 4.
- Exactly one SIGTERM was sent in each cycle.
- Process-group signalling: not used.
- SIGKILL: not used.

Every cycle reached real `/v1/health` status `ok`, found exactly one direct serving child, proved that child was the sole socket owner, and then proved after SIGTERM:

- child absent;
- parent absent with return code 0;
- no socket owner;
- socket pathname removed;
- the same SQLite database opens with `PRAGMA quick_check = ok`;
- `RUNNING_ROWS = 0`, `ACTIVE_RUNS = 0`, and `HELD_WRITERS = 0`;
- the exact helper immediately starts and serves health again in the next cycle.

Machine-readable evidence is in `packaged-sigterm.json`.

## Readiness-bound note

An initial 20-second-bound attempt stopped before signalling because the cold disposable bootstrap did not serve health within that observation window. That process then exited without intervention and left no daemon process. Its unmodified record is preserved as `packaged-startup-attempt-1.json`. The formal five-cycle gate used a 60-second per-cycle upper bound; the successful cold first cycle completed startup plus shutdown in 12.952 seconds, and warm cycles completed in 6.834–6.994 seconds.

The readiness-bound attempt is not counted as a SIGTERM failure or as an acceptance cycle because no signal was sent and health was never reached.
