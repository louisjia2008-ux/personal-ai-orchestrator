# Current task

T004 — Implement minimal lock-safe SIGTERM repair

Acceptance:

- SIGTERM/SIGINT request shutdown without lock-backed handler work; ordinary-flow cleanup and focused lifecycle tests pass.

Verification:

- `uv run --offline --extra dev pytest -q tests/test_daemon_shutdown.py tests/test_product_daemon.py -k 'shutdown or sigterm or sigint or bootstraps_runtime'`

Intended file scope: `src/personal_ai_orchestrator/daemon.py`, `tests/test_daemon_shutdown.py`, and `.ralph/*` only.
