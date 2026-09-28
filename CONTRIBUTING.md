# Contributing

Thank you for helping improve Personal AI Orchestrator. The project is
pre-alpha and its safety boundaries are part of the product contract.

## Before opening a change

1. Search existing issues and pull requests.
2. Open an issue first for changes to the Safety Kernel, credential handling,
   approvals, paid usage, worktree ownership, or Production ACTIVE behavior.
3. Keep provider transport, routing policy, host verification, durable safety
   state, and client presentation in their existing layers.

## Development setup

Supported development environments are Python 3.12, Node.js 24 for adapter
tests, and—only for the native app—macOS 13 or newer with Swift 5.9 or newer.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pytest -q

npm --prefix integrations/opencode install
npm --prefix integrations/opencode run typecheck
npm --prefix integrations/dsh-plugin test
npm --prefix integrations/pi-dsh test

cd macos/PAOMenuBar
swift build
swift test
```

The macOS commands are required only when a change affects the native client or
shared client contracts. Do not run provider/model acceptance merely to make a
pull request green; live calls require an explicit, bounded acceptance plan.

## Pull requests

- Work on a focused branch; never push directly to protected `main`.
- Keep one writer per worktree.
- Include tests for behavior changes and update relevant documentation.
- Run `git diff --check` and the relevant commands above.
- Preserve `UNKNOWN`, stale, and not-observed states. Do not convert missing
  external evidence into success.
- Never include credentials, auth-store contents, raw provider payloads,
  private filesystem paths, runtime databases, sockets, or generated bundles.
- State which gates were actually observed: unit/CI, target Mac, signing,
  provider runtime, realtime behavior, execution, and release are separate.

By contributing, you agree that your contribution is licensed under the
repository's MIT License.
