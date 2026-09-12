# PI-1 Real Mac Acceptance - 2026-09-12

Status: `PI_1_REAL_MAC_ACCEPTANCE_BLOCKED_PI_NOT_INSTALLED`

## Summary

PI-1 code spike (Draft PR #29, branch `feat/pi-runtime-spike-01`,
HEAD `44747c0af52c5a28e5b7ef420526aea8411eb7e8`) is code-green and CI-green
under the existing fake-runtime contract, but the real target-Mac acceptance
gap cannot be closed because **the Pi CLI binary is not installed on the
target Mac** and the installation rule forbids autonomous global installation.

This record is the BLOCKED evidence. No real Pi worker was launched.

## What was checked

The Pi binary was searched for in every plausible host location before
recording BLOCKED:

| Surface                                                    | Result                          |
| ---------------------------------------------------------- | ------------------------------- |
| `command -v pi` (PATH)                                     | `command not found: pi`         |
| `which pi`                                                | `pi not found`                  |
| `/opt/pi/bin/pi` (test placeholder)                        | no such directory               |
| `/usr/local/bin/pi`                                       | no such file                    |
| `/opt/homebrew/bin/pi` (Homebrew)                         | no such file                    |
| `/opt/homebrew/Cellar/pi*` (Homebrew Cellar)              | no matching Cellar              |
| `/opt/homebrew/lib/node_modules/pi*` (Homebrew node pkgs) | only `pnpm`, `npm`, `agent-browser`, `openclaw` |
| `npm list -g --depth=0`                                   | only `@anthropic-ai/claude-code`, `clawhub`, `corepack`, `npm` |
| `~/.local/share/fnm/node-versions/.../bin/pi`             | not present                     |
| `~/.local/bin/pi`                                         | only `heretic`, `nano-pdf`      |
| `~/.local/state/fnm_multishells/**/pi`                    | no match                        |
| `~/.opencode/bin/pi`                                      | only `opencode`                 |
| `~/.pi`, `~/.pi-coding`, `~/.pi-mono`, `~/.pi-coding-agent` | none of these directories exist |
| `/Applications/*Pi*`                                      | no match                        |
| `~/Downloads/*pi*`                                        | no match                        |
| Existing project worktrees (`pao-*`, `personal-ai-orchestrator`) | no Pi runtime shipped    |
| Existing transient caches (`~/.npm/_npx/**/pi*`)           | no `pi-coding-agent` cached     |
| Existing OS packages / `/opt/*` (`find /opt -name 'pi*'`)  | only unrelated `pinentry*`, `pip*`, `pixman` etc. |

The only `pi`-prefixed binaries on the system are unrelated GPG
`pinentry` helpers and the Python `pip`/`pipx` packages. The only
globally-installed Node binaries are `agent-browser`, `openclaw`,
`pnpm`, `npm`. No Pi binary, no `@earendil-works/pi-coding-agent`
package, and no Pi configuration directory exist on the host.

## What official installation method appears required

The Pi project (maintained at `earendil-works/pi`, monorepo `pi-mono`)
ships the interactive coding-agent CLI as the npm package
`@earendil-works/pi-coding-agent`. Its published install paths are:

- `npm install -g @earendil-works/pi-coding-agent`
  (the standard global CLI install), or
- a versioned standalone binary built from the GitHub release source
  archive via `./scripts/build-binaries.sh --offline-model-data
  --platform linux-x64 --out "$PWD/out"` (the same script used to
  produce the official standalone binaries).

The host's per-user Node version is `v22.22.0` (fnm). npm and pnpm are
both available. The Mac has the toolchain to install Pi; nothing on the
host is preventing installation other than the PI-1 acceptance rule
itself.

## Exact owner action needed

The PI-1 acceptance rules forbid autonomous sudo, Homebrew mutation,
and unverified global npm installs. The owner must perform exactly one
of the following before PI-1 real Mac acceptance can proceed:

1. **Recommended — global npm install** (per the upstream Pi README):
   ```text
   npm install -g @earendil-works/pi-coding-agent
   ```
   Then verify with `pi --version` and re-run PI-1 acceptance. The
   resulting `pi` binary on PATH is what PI-1 expects
   (`PiRuntimeConfig.pi_bin` defaults to `"pi"`).

2. **Alternative — vendored standalone binary**: download the GitHub
   release source archive, run the official build script to produce
   the platform binary, and set `pi_bin` to that binary's absolute
   path when configuring the host executor.

The chosen `pi` binary must be reachable via `which pi` or be supplied
as an absolute `pi_bin` path, and it must be able to authenticate
against the existing `zai-coding-plan` provider surface (the GLM
account already authorized on the host). PI-1 does NOT install Pi
itself; the host owner authorizes the install.

## What was NOT done (and why)

The following PI-1 acceptance steps are explicitly NOT executed in
this record because Pi is not installed and every one of them requires
a real Pi process:

- Direct Pi JSON-mode invocation in a disposable fixture repo
- Real protocol-event stream capture
- Real provider/model identity gate (actual `provider`/`model` field
  verification against the PAO-selected target)
- Real `stopReason == "stop"` gate on the terminal assistant message
- Real worktree-guard escape attempt (against an external sentinel)
- Real PAO end-to-end dispatch through `PiOwnerDispatchExecutor`
- Real success / BLOCKED path exercise against the live runtime
- Real provider-error / 429 path exercise against the live runtime

The fake-runtime regression suite
(`tests/test_pi_runtime.py`, `tests/test_pi_dispatch_executor.py`)
remains the only Pi behavior tested here, and it has not changed.

## Sanitized baseline sanity (no Pi involved)

Run from the acceptance worktree
(`/Volumes/Taoruide外接/pi1-mac-acceptance`,
branch `pi1-mac-acceptance`, tracking
`origin/feat/pi-runtime-spike-01`,
HEAD `44747c0af52c5a28e5b7ef420526aea8411eb7e8`):

```text
python -m pip install -e .[dev]   # OK
python -m ruff check .            # All checks passed!
python -m pytest tests/test_pi_runtime.py tests/test_pi_dispatch_executor.py -q
                                  # 12 passed
python -m pytest -q               # 1012 passed
git diff --check origin/main...HEAD
                                  # clean
```

Target environment:

```text
macOS:        ProductName: macOS; ProductVersion: 26.5.1; BuildVersion: 25F80
Darwin:       Darwin Kernel Version 25.5.0; arm64
Python:       3.13.13 (system venv)
Node:         v22.22.0 (fnm)
npm:          10.9.4
pnpm:         installed via Homebrew
opencode:     1.18.30 (host-installed, NOT used by PI-1)
pi:           NOT INSTALLED (this record's blocker)
```

## Security posture preserved

Because no real Pi process ever ran:

- PAO main repo HEAD + working tree: unchanged
- No fixture repository was created
- No disposable sentinel file was written
- No worker write occurred (no task worktree existed)
- No host-owned guard was modified
- No orphan Pi process, writer lock, RUNNING row, or credential
  leak can have occurred
- No Pi binary path or auth metadata was captured in this record

## Architectural boundary status

PAO authority was NOT exercised because the host-side runtime was
absent. The frozen PI-1 surface
(`pi_runtime.py`, `pi_dispatch_executor.py`,
`tests/test_pi_runtime.py`,
`tests/test_pi_dispatch_executor.py`) was not modified in this
attempt; no Scheduler / Safety Kernel / Verifier / Quota Governor /
WP5b UI change was made. PR #29 remains a Draft.

## Recommended next phase (BLOCKED continuation)

Once the owner installs Pi and confirms `pi --version` succeeds, the
next executor should:

1. Re-run the Pi binary inspection from this record's
   "What was checked" table.
2. Compare `pi --help` against the PI-1 argv in
   `pi_runtime.build_pi_json_argv` and record any flag differences.
3. If a real Pi CLI flag differs from the PI-1 adapter expectation,
   patch ONLY `pi_runtime.py` (no PI-1 scope expansion).
4. Run the direct Pi JSON smoke test in a disposable fixture repo.
5. Run the worktree-guard acceptance against an external sentinel.
6. Run the real PAO end-to-end acceptance.
8. Update PR #29 to `PI_1_REAL_MAC_ACCEPTANCE_COMPLETE` only when a
   real Pi process reached a truthful deterministic VERIFIED result.

Until then, PR #29 status stays `PI_1_REAL_MAC_ACCEPTANCE_BLOCKED_PI_NOT_INSTALLED`.