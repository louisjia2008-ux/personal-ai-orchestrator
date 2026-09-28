# Public release checklist

This checklist is reproducible and intentionally separates source readiness
from changing repository visibility. Completing it does **not** authorize a
public release, signing identity, notarization, provider call, paid usage, or
Production ACTIVE.

## 1. License and community files

- [x] MIT `LICENSE` exists and package metadata names MIT.
- [x] License trade-offs and the decision are documented.
- [x] `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` exist.
- [x] Pull-request, bug-report, and feature-request templates exist.

## 2. Supported surfaces

- Python core/CLI: Python 3.12 (the CI contract).
- JavaScript adapters: Node.js 24 (the CI contract).
- Native macOS client: macOS 13+, Swift tools 5.9+.
- Linux: Python core and JavaScript adapter CI only; no native macOS UI.
- Windows: not currently supported or observed.
- Provider subscriptions, live quota endpoints, signing, notarization, and
  real Telegram delivery are external acceptance gates, not implied by CI.

## 3. Clean-clone verification

Run from a new clone with no copied virtual environment, runtime state, or auth
files:

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
```

On macOS 13+ also run:

```bash
cd macos/PAOMenuBar
swift build
swift test
```

Record the exact commit and CI run. A green job must contain real checkout,
install, build, and test steps; a pre-runner failure is not a test result.

## 4. Repository hygiene

- [x] Run a credential-pattern scan over the complete Git history, not only
  `HEAD`; every match in the current history is a visibly synthetic test canary.
- [x] Confirm no credentials, auth-store contents, `.env` files, private keys,
  runtime databases, sockets, logs, generated app bundles, or personal paths
  are tracked. Historical host roots were replaced with sanitized placeholders;
  remaining `/Users/<user>` and `/Users/example` values are explicit fixtures.
- [x] Review all evidence fixtures for raw prompts, provider responses, user
  content, and environment values; retain only sanitized evidence.
- [x] Confirm example provider configuration uses placeholders and cannot
  enable Production ACTIVE.
- [x] Inspect declared direct dependencies and record their license families in
  `THIRD_PARTY_NOTICES.md`; repeat against exact transitive versions for every
  distribution build.

Useful `HEAD` checks (history scanning still requires a dedicated scanner):

```bash
git status --short
git ls-files | rg '(^|/)(\.env|auth\.json|state\.sqlite3|control\.sock)$'
git grep -nE 'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|ghp_[A-Za-z0-9]{20,}'
git grep -nE '/Users/[^/]+|/Volumes/[^/]+'
```

Any match must be reviewed; test-only canaries must be visibly synthetic.

## 5. Release authority

- [ ] Maintainer explicitly approves the exact release commit.
- [ ] Repository visibility change is separately approved and performed by the
  owner only after every required box above is complete.
- [ ] Distribution signing/notarization is separately approved and verified.
- [ ] Release notes state unobserved platforms and external-provider gates.
- [ ] Production ACTIVE remains disabled unless its independent evidence and
  owner-approval gate passes.

Current status: **SOURCE CHECKLIST READY; VISIBILITY CHANGE NOT AUTHORIZED BY
THIS DOCUMENT.** The unchecked items above are release actions that require an
explicit owner decision for an exact commit, not defects in source readiness.
