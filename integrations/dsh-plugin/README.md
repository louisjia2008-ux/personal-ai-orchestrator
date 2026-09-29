# PAO plugins for DeepSeek Harness

This bundle contains two independent Cordis plugins:

- `pao-dsh-dispatch`: the existing Jev quota-aware `agent/request` router;
- `pao-dsh-dispatch/telegram`: an opt-in Telegram controller for one local
  DeepSeek Harness workspace.

The Telegram controller uses the public Harness `ctx.agents` API: it creates or
resumes an Agent, submits a normal user follow-up, observes committed visible
assistant text, and uses the typed user-cancel operation. It does not spawn a
shell or bypass the Harness tool, sandbox, or approval services. This follows
the current DeepSeek Harness plugin and Agent contracts documented in its
[architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
and [`dsh-agent` reference](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/README.md).

## Install

Install the bundle into the intended profile:

```bash
dsh plugin --profile <profile-name> add github:louisjia2008-ux/personal-ai-orchestrator
```

The bundle ships the Telegram row with `enabled: false`. Installation alone
does not connect to Telegram.

## Enable the Telegram controller

Create a bot with BotFather, then keep its token outside Git and outside the
Harness patch file:

```bash
export TELEGRAM_BOT_TOKEN='...'
```

Override the complete `pao-telegram-controller` row in the selected profile's
`cordis.patch.yml` (Harness patch rows replace, rather than merge, config):

```yaml
- id: pao-telegram-controller
  name: pao-dsh-dispatch/telegram
  config:
    enabled: true
    tokenEnv: TELEGRAM_BOT_TOKEN
    allowedUserIds: [123456789]
    allowedChatIds: [123456789]
    cwd: /absolute/path/to/the/only/allowed/workspace
    workspaceLabel: my-workspace
    pollTimeoutSeconds: 25
    ignorePendingOnFirstStart: true
```

Restart that profile. On first activation the controller advances past queued
pre-activation updates instead of executing stale commands. It uses Telegram
long polling and deliberately does not delete or replace an existing webhook;
`getUpdates` will fail closed until the webhook is removed by the bot owner.

Only one long-polling consumer may own a bot's update queue. Use a separate bot
token if another PAO/Telegram process already polls the same bot.

Optional fields:

```yaml
    provider: deepseek
    model: deepseek-v4-flash
    reasoningEffort: medium
    maxTokens: 8192
    agentPreset: coder
    maxPromptChars: 12000
    maxReplyChars: 16000
    stateFile: /absolute/private/path/pao-telegram-controller.json
```

Omit provider/model/preset fields to inherit the profile's normal Harness
selection. Confirm model IDs against the installed Harness catalog; this plugin
does not invent or silently substitute a model.

## Telegram commands

```text
/dsh run <natural-language task>
/dsh status
/dsh cancel
/dsh new
/dsh help
```

`/dsh new` accepts no path. Telegram cannot change `cwd`, provider credentials,
tool policy, sandbox policy, or approval policy. Arbitrary Telegram text and
unknown `/dsh` subcommands are ignored/rejected rather than treated as shell
input.

## Security boundary

- Activation requires both a non-empty user-ID allowlist and chat-ID allowlist.
- The bot token is read from one named environment variable and never written
  to state, output, or logs.
- State contains only the Telegram update offset and chat-to-Harness-session
  mapping. It is written atomically with mode `0600`.
- The working directory is resolved locally at startup and cannot be supplied
  through Telegram.
- Pre-activation updates are ignored by default, preventing a stale queued
  message from becoming a command after installation.
- Reasoning deltas and local error details are not sent to Telegram. Only
  committed visible assistant text is relayed, with bounded message and output
  sizes.
- Harness approvals remain Harness approvals. This plugin does not offer a
  Telegram command that grants a permission or weakens a sandbox.

Telegram is still a remote control surface: an allowlisted prompt can ask the
Harness to use whatever tools the selected local profile already permits, and
visible model output is copied to Telegram. Use a dedicated private bot and a
least-privilege Harness profile; do not point it at a broader workspace than
you are willing to control remotely.

## Verification

```bash
npm --prefix integrations/dsh-plugin test
```

The unit contract covers command parsing, dual allowlists, fixed-cwd creation,
typed cancellation, reasoning suppression, credential-free state, token-safe
errors, and Telegram message-size bounds. A real DSH composition and a real
Telegram account are separate acceptance gates; no live bot connection is made
by this repository's tests.

The package loader, default-disabled bundle, and enabled lifecycle have also
been composition-tested against a local DeepSeek Harness `0.1.2-alpha.1`
checkout with an offline fake Bot API. Harness plugin APIs are pre-stable, so
repeat that composition check after upgrading Harness.
