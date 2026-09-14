# Current task

T003 — Add deterministic old-behavior reproduction boundary

Acceptance:

- A disposable regression test proves control-only SIGTERM must exit cleanly within a bound and always cleans its owned subprocess on failure.

Verification:

- `.venv/bin/pytest -q tests/test_daemon_shutdown.py -k sigterm`

Intended file scope: `tests/test_daemon_shutdown.py` and `.ralph/*` only.
