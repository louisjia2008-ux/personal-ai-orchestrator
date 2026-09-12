# PI-2 Real Mac Discovery Acceptance - 2026-09-12 (complete)

Status: `PI_2_REAL_MAC_METADATA_ACCEPTANCE_COMPLETE`

## Summary

PI-2 introduces Pi as a *metadata-only* discovery source without changing
any existing authority surface. The `discover_pi()` implementation was
driven end-to-end on the target Mac against the real Pi 0.85.1 binary
and the real stored ZAI authentication. The implementation produced a
sanitized `PiDiscoveryResult` with `state=DISCOVERED`, `provider_count=1`,
and seven SKUs from the real ZAI catalog — without ever invoking an LLM
prompt, burning model quota, or exposing a credential value.

PI-2 has no product-wiring implications: the new `ModelRegistry` is
runtime-scoped (`runtime_id='pi'`) and every newly discovered
`ExecutionTarget` carries `execution_verified=False`. Product-level
selection between OpenCode and Pi remains a later wiring phase (out of
PI-2 scope).

## What was verified on the real target Mac

| Step                                                                  | Result                                                |
| --------------------------------------------------------------------- | ----------------------------------------------------- |
| `command -v pi`                                                       | `/Users/<user>/.local/state/fnm_multishells/.../bin/pi` |
| `pi --version`                                                        | `0.85.1`                                              |
| `pi auth check --provider zai --json --no-refresh`                    | `{"status":"ready","provider":"zai","authType":"api_key"}` exit 0 |
| Real `discover_pi()` invocation (no fake substitution)                | `PiDiscoveryCycleOutcome(error_code=None)`, result `PiDiscoveryResult(state=DISCOVERED, providers=(<1 record>,), pi_version='0.85.1')` |
| Discovered `runtime_provider_id`                                      | `zai`                                                 |
| Discovered `provider_id`                                              | `zai-coding-plan`                                     |
| Discovered `auth_status`                                              | `READY`                                               |
| Discovered model SKUs                                                 | `glm-4.7, glm-5-turbo, glm-5.2, glm-5.2-highspeed, glm-5.3, glm-5.3-flash, glm-5.3-highspeed` |
| `glm-5.3` present in catalog                                          | YES                                                   |
| `build_pi_registry()` providers                                       | `['zai-coding-plan']`                                 |
| `build_pi_registry()` models                                          | 7 entries, all prefixed `zai-coding-plan/`             |
| `build_pi_registry()` execution targets                               | 7 entries, all prefixed `pi-zai-coding-plan-`         |
| First `ExecutionTarget.runtime_id`                                    | `pi`                                                  |
| First `ExecutionTarget.runtime_provider_id`                           | `zai`                                                 |
| First `ExecutionTarget.execution_verified`                            | `false`                                               |
| `catalog_snapshot_ids`                                                | `['pi-discovery:2026-09-12T14:21:37.574057+00:00']`   |
| Metadata-only (no LLM prompt, no model quota burn)                    | YES                                                   |
| Credential env-var filtering (PI-2 harness)                           | 13 canary credential env vars in parent env → 0 leaked into the harness subprocess env (verified end-to-end with `/usr/bin/env`) |

## Direct `discover_pi()` invocation (sanitized)

The harness:

```python
# /Volumes/Taoruide外接/pi2-real-acceptance/pi2_real_discovery.py
from personal_ai_orchestrator.pi_provider_discovery import (
    discover_pi, build_pi_registry,
)
outcome = discover_pi()
# serialized via outcome.result.to_dict() (which calls assert_sanitized
# on the payload before returning) + build_pi_registry(outcome.result)
```

Returned outcome (full sanitized JSON, 2117 bytes; written to
`/tmp/pi2_real_outcome.json`):

```text
PI_2_DISCOVERY_OUTCOME_BEGIN
{
  "catalog_snapshot_ids": [
    "pi-discovery:2026-09-12T14:21:37.574057+00:00"
  ],
  "error_code": null,
  "error_message": null,
  "first_execution_target_execution_verified": false,
  "first_execution_target_runtime_id": "pi",
  "first_execution_target_runtime_provider_id": "zai",
  "registry_execution_targets": [
    "pi-zai-coding-plan-glm-4.7",
    "pi-zai-coding-plan-glm-5-turbo",
    "pi-zai-coding-plan-glm-5.2",
    "pi-zai-coding-plan-glm-5.2-highspeed",
    "pi-zai-coding-plan-glm-5.3",
    "pi-zai-coding-plan-glm-5.3-flash",
    "pi-zai-coding-plan-glm-5.3-highspeed"
  ],
  "registry_models": [
    "zai-coding-plan/glm-4.7",
    "zai-coding-plan/glm-5-turbo",
    "zai-coding-plan/glm-5.2",
    "zai-coding-plan/glm-5.2-highspeed",
    "zai-coding-plan/glm-5.3",
    "zai-coding-plan/glm-5.3-flash",
    "zai-coding-plan/glm-5.3-highspeed"
  ],
  "registry_providers": [
    "zai-coding-plan"
  ],
  "result_dict": {
    "configured_family_count": 1,
    "discovery_state": "DISCOVERED",
    "execution_target_count": 7,
    "generated_at": "2026-09-12T14:21:37.574057+00:00",
    "last_error_code": null,
    "provider_count": 1,
    "providers": [
      {
        "auth_kind": "api_key",
        "auth_reason": null,
        "auth_status": "READY",
        "catalog_discovered": true,
        "display_name": "GLM / Z.AI",
        "env_variables_present": [],
        "evidence_source": "PI_CLI_INSPECTION",
        "execution_verified": false,
        "model_skus": [
          "glm-4.7",
          "glm-5-turbo",
          "glm-5.2",
          "glm-5.2-highspeed",
          "glm-5.3",
          "glm-5.3-flash",
          "glm-5.3-highspeed"
        ],
        "observed_at": "2026-09-12T14:21:37.574057+00:00",
        "pool_kind": "windowed",
        "provider_id": "zai-coding-plan",
        "runtime_id": "pi",
        "runtime_provider_id": "zai"
      }
    ],
    "runtime_id": "pi",
    "runtime_path": "/Users/<user>/.local/state/fnm_multishells/6286_1788487146278/bin/pi",
    "runtime_version": "0.85.1",
    "schema_version": 1,
    "source_method": "pi_cli_inspection"
  }
}
PI_2_DISCOVERY_OUTCOME_END
```

## Identity boundary preserved

The PAO commercial/quota identity is **NOT** collapsed into Pi runtime
identity. The mapping table contains exactly one entry, which is the
PI-1-verified mapping:

```python
PI_PROVIDER_SPECS = (
  PiProviderSpec(
    pao_provider_id="zai-coding-plan",
    pi_provider_id="zai",
    display_name="GLM / Z.AI",
    env_variables=("ZAI_API_KEY",),
    auth_kind="api_key",
    pool_kind="windowed",
  ),
)
```

`build_pi_registry()` produces a normal `ModelRegistry` whose
`ExecutionTarget` rows keep both identities:

```text
provider_id:        "zai-coding-plan"          (PAO commercial/quota surface)
runtime_id:         "pi"                       (Pi runtime identity)
runtime_provider_id:"zai"                      (Pi provider id passed to CLI)
model_sku_id:       "zai-coding-plan/glm-5.3"  (composed PAO id + discovered SKU)
execution_verified: false                      (default — metadata-only)
```

## Subprocess isolation flags actually used

PI-2 runs each metadata-only Pi CLI invocation with the existing
hardened discovery wrapper (`_run_opencode`):

```text
pi --version

pi auth check --provider zai --json --no-refresh

pi --offline --no-approve --no-extensions --no-skills \
   --no-prompt-templates --no-context-files --list-models zai
```

All three invocations inherit an explicit allowlisted env built by
`_build_subprocess_env(parent_env)`. No shell, no project approval,
no extension/skills/prompt-template/context-file discovery, no startup
network refresh. The wrapper's argv path is the absolute resolved `pi`
binary; the wall-clock cap is 15 s and the stdout cap is 256 KiB.

## Credential-safety acceptance

A canary subprocess probe
(`/Volumes/Taoruide外接/pi2-real-acceptance/credential_isolation_probe.py`)
forged a parent env containing 13 known credential env-var names with
recognisable `canary_*` values, then ran `_build_subprocess_env` (the
same helper PI-2 uses) and spawned `/usr/bin/env` with the result.

```text
DISCOVERY_ENV_BLOCKLIST_SIZE: 33

step1_filter_only:
  filtered_keys:   [HOME, PATH]
  filtered_leaks:  {}

step2_subprocess_actually_receives_clean_env:
  child_returncode: 0
  child_env_count:  2
  child_env_keys:   [HOME=/tmp, PATH=/usr/bin:/bin]
  canary_leaks:     []
```

This proves end-to-end that the 33-variable blocklist actually reaches
the subprocess layer: zero canary credential env vars survived
`_build_subprocess_env`, and zero canary credential env vars were
visible to a child process spawned with the PI-2 harness env.

The serialized discovery outcome (above) was scanned with the same
secret patterns used by `_redact` (`sk-*`, `Bearer *`, `AKIA*` etc.):
**0 hits**. The PI-2 module's `assert_sanitized` is applied to every
`PiProviderDiscovery.to_dict()` payload and to every `PiDiscoveryResult.to_dict()`
payload before it leaves the module.

No real owner key, token, or `~/.pi/agent/auth.json` content was read,
copied, logged, or committed. `auth.json` size was inspected only
(382 bytes on this attempt, after the owner configured ZAI auth in
the previous PI-1 attempt cycle); the file contents were never opened.

## Negative gates still in force

PI-2 does not modify the OpenCode path. The 4 existing PI-2 unit tests
in `tests/test_pi_provider_discovery.py` exercise the negative contract
on a fake Pi binary:

- `_parse_pi_auth_check` rejects any payload that includes
  `credential`, `credentials`, `apiKey`, `api_key`, `token`,
  `bearerToken`
- `_parse_pi_auth_check` rejects any unexpected `provider` value
- `_parse_pi_auth_check` rejects any value still matching a secret
  shape (via `assert_sanitized`)
- `_parse_pi_model_table` ignores lines whose provider id does not
  match the curated `expected_provider`
- `_parse_pi_model_table` rejects path-escape model ids (`../../secret`)
- `_parse_pi_model_table` rejects empty/header/non-row lines
- `discover_pi()` does NOT call `--list-models` when auth is `NOT_READY`
  or `UNKNOWN`
- `discover_pi()` does NOT promote a non-zero-exit auth check to READY
- `discover_pi()` returns a typed `PI_CLI_NOT_FOUND` error when no Pi
  binary is reachable
- `build_pi_registry()` always sets `execution_verified=False` (this
  was the requested property — the curated PAO/Runtime identity
  mapping is metadata only)

These are independent of the real Pi subprocess invocation above; they
remain green on the same HEAD.

## Sanitized baseline sanity

Run from the PI-2 acceptance worktree
(`/Volumes/Taoruide外接/pi2-mac-discovery`,
branch `pi2-mac-discovery`,
tracking `origin/feat/pi-provider-discovery-02`,
HEAD `31267c18d35972c4abda33cb820fef82e5a9a342`):

```text
# Targeted PI-2 tests
PYTHONPATH=src python -m pytest tests/test_pi_provider_discovery.py -q
  -> 14 passed in 0.11s

# Lint
python -m ruff check .
  -> All checks passed!

# Full test suite (attempt 1)
PYTHONPATH=src python -m pytest -q
  -> 4 failed, 1022 passed in 105.39s (0:01:45)
     tests/test_daemon_shutdown.py::test_sigint_shuts_daemon_down_cleanly_without_traceback
     tests/test_daemon_tick_integration.py::test_daemon_tick_advances_last_tick_at_between_health_polls
     tests/test_daemon_tick_integration.py::test_daemon_health_poll_does_not_require_credential_handlers
     tests/test_product_daemon.py::test_product_daemon_bootstraps_runtime_and_serves_control_plane
     Error: ControlPlaneUnavailable: control plane unavailable at /tmp/pao-product-6xsfnvgh/.../control.sock

# Re-run of the 4 daemon tests on the exact same HEAD
PYTHONPATH=src python -m pytest <4 tests> -q
  -> 4 passed in 9.16s

# Diff hygiene from origin/main
git diff --check origin/main...HEAD
  -> clean

# OpenCode adapter regression (integrations/opencode)
npx tsc --noEmit
  -> clean
node --test decision_contract.test.ts
  -> 17 passed, 0 failed
```

The 4 daemon-test failures on attempt 1 are the documented flaky
subprocess-socket races (the tests spawn a real daemon subprocess and
race its control-plane socket readiness). They are unrelated to PI-2 —
none of the failing tests reference `pi_provider_discovery`,
`_build_subprocess_env`, `_DISCOVERY_ENV_BLOCKLIST`,
`PiDiscoveryResult`, or any other PI-2 surface. Per spec, the failures
were re-run once on the exact same HEAD and all 4 passed. No source
patch was made.

Target environment:

```text
macOS:        ProductName: macOS; ProductVersion: 26.5.1; BuildVersion: 25F80
Darwin:       Darwin Kernel Version 25.5.0; arm64
Python:       3.13.13 (system venv)
Node:         v22.22.0 (fnm)
npm:          10.9.4
opencode:     1.18.30 (host-installed; NOT used by PI-2)
pi:           0.85.1 INSTALLED, zai auth READY (auth.json: 382 bytes)
```

## Files in origin/feat/pi-provider-discovery-02 (PI-2 surface)

- `src/personal_ai_orchestrator/pi_provider_discovery.py` (NEW; 569 lines)
- `tests/test_pi_provider_discovery.py` (NEW; 383 lines)
- `docs/acceptance/PI2_REAL_MAC_DISCOVERY_2026-09-12.md` (NEW; this file)

No existing PAO source file was modified in PI-2.

```text
git diff --stat origin/main...origin/feat/pi-provider-discovery-02
  src/personal_ai_orchestrator/pi_provider_discovery.py   | 569 ++++++++
  tests/test_pi_provider_discovery.py                     | 383 +++++
  docs/acceptance/PI2_REAL_MAC_DISCOVERY_2026-09-12.md    | (this file)
```

PR #30 status flips from
`PI_2_CODE_SPIKE_GREEN_TARGET_MAC_METADATA_ACCEPTANCE_PENDING`
to `PI_2_REAL_MAC_METADATA_ACCEPTANCE_COMPLETE`. The PR remains DRAFT
until the owner decides to merge.

## Security / cleanliness

- PAO main repo HEAD + working tree: UNCHANGED
  (`feat/control-center-console`,
  HEAD `ca323346b02514708fe533123c6dc23cb0f5b9a6`)
- pi1-mac-acceptance worktree: UNCHANGED
  (`pi1-mac-acceptance`,
  HEAD `55518e15d8c7ef2f89d730ab3ff8ea65f4aecb21`)
- No `~/.pi/agent/auth.json` mutation attempted; only file size inspected
- No credential value, env var contents, bearer token, or API key was
  read, copied, logged, or committed
- No orphan Pi process, RUNNING task, or writer lock at end of run
- No LLM prompt was invoked and no model quota was burned
- The Pi guard is still NOT an OS sandbox (PI-1 already documented this
  in PR #29; PI-2 inherits the same disclaimer)

## Recommended next phase (out of PI-2 scope)

- Product-level wiring (let user select Pi-vs-OpenCode per task;
  QuotaGovernor / Verifier / Scheduler / Safety Kernel semantics
  unchanged).
- Re-run PI-2 acceptance whenever `pi --version` advances, to catch
  CLI-flag drift between Pi and `_parse_pi_auth_check` /
  `_parse_pi_model_table`.
- Future PI-* phases may extend `PI_PROVIDER_SPECS` only after each
  new PAO/Runtime pair has its own PI-1-style real acceptance. PI-2
  ships with the single entry that PI-1 already proved.
- The disposable harness scripts
  (`/Volumes/Taoruide外接/pi2-real-acceptance/pi2_real_discovery.py`,
  `/Volumes/Taoruide外接/pi2-real-acceptance/credential_isolation_probe.py`)
  are out-of-tree artifacts and NOT part of the PR diff. The owner
  may delete them after merge.