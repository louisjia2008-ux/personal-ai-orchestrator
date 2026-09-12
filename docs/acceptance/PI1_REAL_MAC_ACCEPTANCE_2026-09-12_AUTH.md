# PI-1 Real Mac Acceptance - 2026-09-12 (auth blocker)

Status: `PI_1_REAL_MAC_ACCEPTANCE_BLOCKED_PROVIDER_AUTH`

## Summary

The Pi CLI installation blocker is resolved (Pi 0.85.1 is now installed at
the host), but a new hard blocker surfaced during real Mac acceptance: the
PAO target provider surface (`zai-coding-plan` -> Pi `zai/glm-5.3`) is not
authenticated on the target host. `pi auth check --provider zai` returns
`{"status":"not_ready","provider":"zai","reason":"credentials_not_configured"}`,
no `ZAI_API_KEY` / `ZAI_CODING_CN_API_KEY` env var is set, and
`~/.pi/agent/auth.json` is empty (size 2 bytes, never read).

Per spec section 3, PI-1 acceptance must STOP with
`PI_1_REAL_MAC_ACCEPTANCE_BLOCKED_PROVIDER_AUTH`. No further PI-1
acceptance steps (real model identity gate, real worktree-guard escape,
real PAO end-to-end) were executed because every one of them requires an
authenticated model invocation.

## What WAS verified (read-only, no quota burned)

| Step                                                      | Result                                                |
| --------------------------------------------------------- | ----------------------------------------------------- |
| `command -v pi`                                           | `/Users/louisjia/.local/state/fnm_multishells/.../bin/pi` |
| `pi --version`                                            | `0.85.1`                                              |
| `pi --help`                                               | all PI-1 flags exist: `--mode json`, `--no-session`, `--no-approve`, `--no-extensions`, `-e`, `--no-skills`, `--no-prompt-templates`, `--no-context-files`, `--tools`, `--model`, `--provider`, `--thinking`, `--list-models` |
| `pi auth --help`                                          | safe readiness commands: `print-api-key`, `print-bearer-token`, `check` |
| `pi auth check --provider zai --json --no-refresh`        | `{"status":"not_ready","provider":"zai","reason":"credentials_not_configured"}` exit 1 |
| `pi auth check --provider zai-coding-cn --json --no-refresh` | `{"status":"not_ready","provider":"zai-coding-cn","reason":"credentials_not_configured"}` exit 1 |
| `pi auth check --provider deepseek --json --no-refresh`   | `{"status":"ready","provider":"deepseek","authType":"api_key"}` exit 0 |
| `pi auth check --provider minimax --json --no-refresh`    | `{"status":"ready","provider":"minimax","authType":"api_key"}` exit 0 |
| `pi --list-models`                                        | only `deepseek` (3 variants) and `minimax` (3 variants); `zai` provider hidden because not configured |
| `pi --list-models glm-5.3`                                | `No models matching "glm-5.3"` (zai catalog hidden)    |
| `pi --list-models zai`                                    | `No models matching "zai"`                            |
| `pi --list-models coding`                                 | `No models matching "coding"`                         |

## EXPECTED / ACTUAL / IMPACT for the model identity gate

EXPECTED (per spec):
```text
provider_id   = "zai-coding-plan"
model_sku_id  = "zai-coding-plan/glm-5.3"
pi_model_ref  = "zai/glm-5.3"
```

ACTUAL (per real Pi 0.85.1 catalog + auth state):
```text
provider_id   = "zai"            # alias OK; matches PI-1 PI_PROVIDER_ALIASES["zai-coding-plan"]
model_sku_id  = "glm-5.3"        # present in zai.json provider file but hidden by --list-models
pi_model_ref  = "zai/glm-5.3"    # SAME as expected; Pi only refuses to surface it
                                 # because configuredProviders excludes zai.
auth_state    = "not_ready"      # credentials_not_configured
auth_env_var  = none of ZAI_API_KEY, ZAI_CODING_CN_API_KEY is set in the host shell.
auth_file     = ~/.pi/agent/auth.json is 2 bytes ("{}"); no provider entry.
```

IMPACT:
```text
PI-1 model identity gate (final_provider="zai" && final_model="glm-5.3")
cannot be exercised until at least one of the documented ZAI auth
surfaces is configured. Until then every Pi invocation against
--provider zai fails at credential lookup, before the model is ever
called, so:
  - No real model completion event is emitted.
  - No final_provider / final_model / stopReason can be observed.
  - The deterministic verifier cannot reach VERIFIED.
No PI-1 source file needs to change for this gate; the canonical ID
"zai/glm-5.3" is correct for the target and matches the catalog.
```

## CLI contract (real Pi 0.85.1 help vs PI-1 argv)

| PI-1 flag in `build_pi_json_argv`           | Pi 0.85.1 help                                                                                                                                                              | Verdict |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `--mode json`                                | `--mode <mode>            Output mode: text (default), json, or rpc`                                                                                                          | MATCH   |
| `--no-session`                               | `--no-session             Don't save session (ephemeral)`                                                                                                                     | MATCH   |
| `--no-approve`                               | `--no-approve, -na        Ignore project-local files for this run`                                                                                                            | MATCH   |
| `--no-extensions`                            | `--no-extensions, -ne      Disable extension discovery (explicit -e paths still work)`                                                                                          | MATCH   |
| `-e <guard>`                                 | `--extension, -e <path>   Load an extension file (can be used multiple times)`                                                                                                 | MATCH   |
| `--no-skills`                                | `--no-skills, -ns         Disable skills discovery and loading`                                                                                                                | MATCH   |
| `--no-prompt-templates`                      | `--no-prompt-templates, -np Disable prompt template discovery and loading`                                                                                                    | MATCH   |
| `--no-context-files`                         | `--no-context-files, -nc  Disable AGENTS.md and CLAUDE.md discovery and loading`                                                                                              | MATCH   |
| `--tools read,edit,write,grep,find,ls`       | `--tools, -t <tools>      Comma-separated allowlist of tool names to enable; Applies to built-in, extension, and custom tools`                                                | MATCH (allowlist semantics confirmed by Pi source) |
| `--model provider/model`                     | `--model <pattern>        Model pattern or ID (supports "provider/id" and optional ":<thinking>")`                                                                              | MATCH   |
| no explicit `-p` / `--print`                 | Pi `resolveAppMode` returns `"json"` when `parsed.mode === "json"`, which routes to `runPrintMode({ mode: "json" })` and exits after the prompt's agent becomes idle            | MATCH   |

Conclusion: PI-1's `build_pi_json_argv` is correct for real Pi 0.85.1.
No PI-1 source patch is needed for the CLI contract.

## JSON event shape (real Pi 0.85.1)

A real Pi 0.85.1 invocation with the PI-1 security posture was
attempted against `--provider zai --model glm-5.3`. Pi emitted a real
JSON event line before exiting on auth failure:

```json
{"type":"session","version":3,"id":"01a095ab-ddb5-71d4-a389-57a4899da714","timestamp":"2026-09-12T12:51:03.733Z","cwd":"/Volumes/Taoruide外接/pi1-real-acceptance/fixture-repo"}
```

stderr (sanitized, no credential values):
```text
No API key found for zai.

Use /login to log into a provider via OAuth or API key. See:
  /Users/louisjia/.local/share/fnm/node-versions/v22.22.0/installation/lib/node_modules/@earendil-works/pi-coding-agent/docs/providers.md
  /Users/louisjia/.local/share/fnm/node-versions/v22.22.0/installation/lib/node_modules/@earendil-works/pi-coding-agent/docs/models.md
```

Exit code: `1` (process exited cleanly; no segment fault, no infinite hang).

This confirms Pi 0.85.1 emits the `session` JSON header event that the
PI-1 `summarize_pi_json_stream` validator expects as the first event
line (the validator's gate "event_count == 1 and event_type == 'session'"
would have been satisfied for this run). The auth-failure exit is
captured by PAO's existing run-watcher via the non-zero exit code; the
deterministic verifier already maps that to `BLOCKED` (not `VERIFIED`)
without needing a real worker `agent_end` event.

## What was NOT done (and why)

Every remaining PI-1 step is blocked on a real authenticated
`zai/glm-5.3` invocation. They are explicitly NOT executed in this
record:

- Real provider/model identity gate on a real `agent_end`
- Real `stopReason == "stop"` gate on the final assistant message
- Real worktree-guard escape test (against an external sentinel)
- Real PAO end-to-end dispatch through `PiOwnerDispatchExecutor`
- Real success / BLOCKED path exercise against the live runtime
- Real provider-error / 429 path exercise against the live runtime

Fake-runtime regression coverage (the 12 PI-1 tests plus the full
1012-test suite) remains authoritative for the bounded behavior that
does NOT require a real worker invocation.

## EXACT owner action required (do NOT print/copy credentials)

The owner must perform exactly one of the following on the target Mac
shell **before** any next PI-1 executor resumes:

1. **Recommended — Global ZAI Coding Plan API key** (per the
   `ZAI_API_KEY` env var documented by Pi):
   ```text
   export ZAI_API_KEY=<redacted>
   ```
   After setting, verify with:
   ```text
   pi auth check --provider zai --json --no-refresh
   ```
   The expected output is:
   ```json
   {"status":"ready","provider":"zai","authType":"api_key"}
   ```

2. **Alternative — China region ZAI Coding Plan API key** (per
   `ZAI_CODING_CN_API_KEY` env var documented by Pi):
   ```text
   export ZAI_CODING_CN_API_KEY=<redacted>
   ```
   After setting, verify with:
   ```text
   pi auth check --provider zai-coding-cn --json --no-refresh
   ```
   The expected output is:
   ```json
   {"status":"ready","provider":"zai-coding-cn","authType":"api_key"}
   ```

3. **OAuth alternative** (per Pi `/login`): the owner may instead run
   `pi /login` (or `pi auth login` if available) interactively and
   follow the provider OAuth flow. After login, verify with the same
   `pi auth check --provider zai --json --no-refresh` command.

After auth is configured, the next executor should resume from
"Section 5. Direct Real Pi JSON Smoke Test" using the exact fixture
repo path
`/Volumes/Taoruide外接/pi1-real-acceptance/fixture-repo`
already created in this attempt.

## Sanitized baseline sanity (no Pi involved)

Run from the acceptance worktree
(`/Volumes/Taoruide外接/pi1-mac-acceptance`,
branch `pi1-mac-acceptance`,
tracking `origin/feat/pi-runtime-spike-01`,
HEAD `89dbe35c8a4a6968986218fd86bc7a4a9b9e293b`):

```text
python -m ruff check .            # All checks passed!
python -m pytest tests/test_pi_runtime.py tests/test_pi_dispatch_executor.py -q
                                  # 12 passed
python -m pytest -q               # 1012 passed
git diff --check origin/main...HEAD
                                  # clean

# OpenCode adapter regression (integrations/opencode):
npm install --no-audit --no-fund  # 100 packages installed
npx tsc --noEmit                  # clean
node --test decision_contract.test.ts
                                  # 17 passed
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
pi:           0.85.1 INSTALLED @ fnm_multishells bin
              (BLOCKED: zai auth not configured)
```

## Security posture preserved

- PAO main repo HEAD + working tree: UNCHANGED
  (still on `feat/control-center-console`,
  HEAD `ca323346b02514708fe533123c6dc23cb0f5b9a6`)
- PI-1 source surface (`pi_runtime.py`, `pi_dispatch_executor.py`,
  `tests/test_pi_runtime.py`,
  `tests/test_pi_dispatch_executor.py`): UNCHANGED in this attempt
- New files added to `origin/feat/pi-runtime-spike-01` in this attempt:
  none (this blocker is recorded by rewriting PR #29 body only)
- Disposable fixture repo created at
  `/Volumes/Taoruide外接/pi1-real-acceptance/fixture-repo`
  with one empty `README.md` and one initial commit
  (`12d7d5e init: PI-1 fixture`); never used by a real Pi worker run
- No `hello.txt` was ever created (real worker run never executed)
- No external sentinel file was ever created (real worktree-guard
  escape never executed)
- No host-owned guard was overwritten
- No auth file content was read or copied; only its size was reported
- No Pi worker process, RUNNING row, or writer lock was taken
- No `~/.pi/agent/auth.json` mutation attempted
- The Pi guard is still NOT an OS sandbox (documented in PR #29)

## Recommended next phase (BLOCKED continuation)

Once the owner configures the ZAI auth surface and confirms
`pi auth check --provider zai --json --no-refresh` returns
`"status":"ready"`, the next executor should:

1. Re-run `pi --list-models zai` and confirm `glm-5.3` appears in the
   catalog (Pi filters `--list-models` to configured providers; the
   `zai.json` provider file already declares `glm-5.3`).
2. Re-run the direct Pi JSON smoke test in the existing disposable
   fixture `/Volumes/Taoruide外接/pi1-real-acceptance/fixture-repo`,
   instructing Pi to create `hello.txt` containing the exact string
   `PI_REAL_RUNTIME_ACCEPTANCE`.
3. Validate the real JSON event shape:
   `session` → `agent_start` → (optional `tool_execution_start` /
   `tool_execution_end`) → `message_end{role:"assistant",
   provider:"zai", model:"glm-5.3", stopReason:"stop"}` → `agent_end`,
   no `extension_error`.
4. Run the worktree-guard escape test against the external sentinel
   `PI_GUARD_SENTINEL_DO_NOT_EXPOSE_7A91`. The guard file is at
   `.pao/pi-worktree-guard.ts` inside the task worktree; the sentinel
   lives outside it.
5. Run the real PAO end-to-end dispatch through
   `PiOwnerDispatchExecutor` and confirm a truthful deterministic
   `VERIFIED` on the same fixture repo.
6. Update PR #29 to `PI_1_REAL_MAC_ACCEPTANCE_COMPLETE` only after
   steps 1–5 all pass with a real Pi 0.85.1 process.

Until then, PR #29 status stays
`PI_1_REAL_MAC_ACCEPTANCE_BLOCKED_PROVIDER_AUTH`.