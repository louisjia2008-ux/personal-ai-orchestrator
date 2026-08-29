# Extended Provider Quota Observability Audit

Status: P3 initial-provider coverage extension

As of: 2026-08-30

This document extends `PROVIDER_QUOTA_OBSERVABILITY.md` so Issue #10 covers every initially
listed provider class. It deliberately distinguishes **historical API usage/cost** from
**remaining subscription allowance**. Exact usage is not relabelled as exact remaining quota.

## OpenAI / Codex ChatGPT plans

Official OpenAI guidance says Codex usage under ChatGPT plans is visible in the product usage
surface and, in an active Codex CLI session, through `/status`. The displayed surface can show
which allowance is exhausted, credit balance, and a reset time when available. OpenAI also
states that supported agentic features can share an allowance/credit pool.

The project does **not** currently treat that user-facing surface as a documented stable machine
quota API. Therefore automated precise remaining subscription quota is `UNKNOWN` until a
provider-supported structured surface is validated.

Source:
- https://help.openai.com/en/articles/11369540-using-codex-with-chatgpt

### OpenAI API

The official Usage API exposes organization usage, including completion usage, and the Costs
endpoint exposes spend. These are authoritative for historical API consumption/cost when called
with the required organization administration credential. They are **not** the ChatGPT/Codex
subscription remaining allowance.

Source:
- https://platform.openai.com/docs/api-reference/usage

## Anthropic / Claude Code plans

Anthropic documents that Claude subscription usage is shared across Claude surfaces including
Claude Code. Pro and Max plans have a five-hour session limit and a weekly limit; the account UI
shows reset information. Claude Code documents `/usage` for plan usage/rate-limit status.

No stable machine API for individual Pro/Max subscription remaining quota is adopted by this
project, so precise automated remaining allowance stays `UNKNOWN` rather than being scraped.

Sources:
- https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- https://support.claude.com/en/articles/14553413-claude-code-cheatsheet
- https://support.claude.com/en/articles/8325606-what-is-the-pro-plan

### Anthropic API

Anthropic's Admin API provides organization API usage/cost reporting. That is authoritative for
API consumption reporting but does not represent a Claude Pro/Max subscription allowance.

Source:
- https://docs.anthropic.com/en/api/admin-api/usage-cost/get-messages-usage-report

## DeepSeek API

DeepSeek documents `GET /user/balance`, which returns whether API balance is available plus
currency-specific total, granted, and topped-up balances. This is an exact PAYG monetary balance,
not a reset-window subscription percentage.

Source:
- https://api-docs.deepseek.com/api/get-user-balance/

## Local / unmetered runtimes

A locally owned model has no external provider billing quota. Its scheduling scarcity belongs to
runtime capacity/availability (GPU memory, thermal state, concurrency, latency), not a fabricated
quota percentage. The registry therefore records local/unmetered as configuration truth and keeps
provider quota windows empty.

## Acceptance interpretation

The initial provider audit is complete when the registry can answer the provider/plan identity
with documented source/scope/confidence. This does **not** mean every provider has an automatable
remaining-quota API:

- MiniMax Token Plan: provider API; exact explicit percentages when returned; live local auth probe
  still required.
- Z.AI Coding Plan: provider-published monitoring surface; conservative `ESTIMATED` semantics; live
  reset/auth probe still required.
- Codex ChatGPT plan: user-facing dashboard/CLI status; automated precise remaining quota
  `UNKNOWN`.
- Claude subscription plan: user-facing account/CLI status; automated precise remaining quota
  `UNKNOWN`.
- OpenAI/Anthropic APIs: exact historical API usage/cost, not subscription remaining allowance.
- DeepSeek API: exact PAYG monetary balance.
- Local runtime: unmetered configuration truth; runtime telemetry is a separate resource axis.

No scraping is authorized by this audit.
