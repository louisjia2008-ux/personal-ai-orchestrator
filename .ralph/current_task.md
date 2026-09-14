# Current task

T007 — Prove isolated packaged signal matrix and restart cycles

Acceptance:

- The real bundled one-file helper passes outer-parent and serving-child SIGTERM routes, ordinary cleanup, and at least three complete start/health/shutdown/restart cycles in a disposable home.

Verification:

- Packaged acceptance evidence has zero failed required checks, no SIGKILL, and no surviving process/socket/database ownership.

Intended file scope: a reusable packaged acceptance harness plus `docs/acceptance/*` and `.ralph/*`; canonical live state remains untouched.
