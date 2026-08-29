"""Audited provider quota-observability capabilities for the P3 MVP."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from personal_ai_orchestrator.model_registry import EvidenceConfidence


class QuotaObservabilityCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    plan_id: str
    official_api: bool
    endpoint: str | None
    authentication: str
    existing_opencode_auth_reusable_without_secret_read: bool
    requires_independent_credential_surface: bool
    automation_allowed: bool | None
    rate_limit: str | None
    quota_windows: tuple[str, ...]
    scope: str
    remaining_semantics: str
    confidence: EvidenceConfidence
    source_uris: tuple[str, ...]
    runtime_status: str


_CAPABILITIES = {
    ("anthropic", "api"): QuotaObservabilityCapability(
        provider_id="anthropic",
        plan_id="api",
        official_api=True,
        endpoint="https://api.anthropic.com/v1/organizations/usage_report/messages",
        authentication="Anthropic Admin API key",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=True,
        rate_limit=None,
        quota_windows=(),
        scope="Anthropic organization API usage/cost, not Claude subscription allowance",
        remaining_semantics=(
            "Admin API reports exact historical API usage/cost; it is not a remaining Claude "
            "Pro/Max/Team subscription quota surface"
        ),
        confidence=EvidenceConfidence.UNKNOWN,
        source_uris=(
            "https://docs.anthropic.com/en/api/admin-api/usage-cost/get-messages-usage-report",
        ),
        runtime_status="AUDITED_COLLECTOR_DEFERRED",
    ),
    ("anthropic", "claude-plan"): QuotaObservabilityCapability(
        provider_id="anthropic",
        plan_id="claude-plan",
        official_api=False,
        endpoint=None,
        authentication="Claude plan login used by Claude Code",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=False,
        automation_allowed=None,
        rate_limit=None,
        quota_windows=("5-hour session", "weekly"),
        scope="Claude/Claude Code shared subscription usage allowance",
        remaining_semantics=(
            "Provider UI and Claude Code /usage expose plan status/reset information, but no "
            "documented stable machine API is used as production quota authority"
        ),
        confidence=EvidenceConfidence.UNKNOWN,
        source_uris=(
            "https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan",
            "https://support.claude.com/en/articles/14553413-claude-code-cheatsheet",
            "https://support.claude.com/en/articles/8325606-what-is-the-pro-plan",
        ),
        runtime_status="MANUAL_OR_CLI_STATUS_ONLY",
    ),
    ("deepseek", "api"): QuotaObservabilityCapability(
        provider_id="deepseek",
        plan_id="api",
        official_api=True,
        endpoint="https://api.deepseek.com/user/balance",
        authentication="DeepSeek API key",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=True,
        rate_limit=None,
        quota_windows=(),
        scope="DeepSeek pay-as-you-go account balance",
        remaining_semantics=(
            "Provider balance API returns exact available monetary balance by currency; this is "
            "PAYG balance, not a reset-window subscription percentage"
        ),
        confidence=EvidenceConfidence.EXACT,
        source_uris=("https://api-docs.deepseek.com/api/get-user-balance/",),
        runtime_status="AUDITED_COLLECTOR_DEFERRED",
    ),
    ("local", "unmetered"): QuotaObservabilityCapability(
        provider_id="local",
        plan_id="unmetered",
        official_api=False,
        endpoint=None,
        authentication="Local runtime ownership/configuration",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=False,
        automation_allowed=True,
        rate_limit=None,
        quota_windows=(),
        scope="Locally owned runtime with no provider billing quota",
        remaining_semantics="No provider quota exists; capacity is runtime availability, not remaining quota",
        confidence=EvidenceConfidence.EXACT,
        source_uris=(),
        runtime_status="UNMETERED_CONFIG_TRUTH",
    ),
    ("minimax", "token-plan"): QuotaObservabilityCapability(
        provider_id="minimax",
        plan_id="token-plan",
        official_api=True,
        endpoint="https://api.minimaxi.com/v1/token_plan/remains",
        authentication="Bearer Token Plan subscription key (CN region)",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=True,
        rate_limit=(
            "Dynamic RPM/TPM throttling; provider says throttles typically reset within ~1 minute"
        ),
        quota_windows=("5-hour rolling", "weekly"),
        scope="CN Token Plan shared quota; current MVP provider is minimax-cn-coding-plan",
        remaining_semantics="Provider API exposes explicit remaining percentage fields when available",
        confidence=EvidenceConfidence.EXACT,
        source_uris=(
            "https://platform.minimaxi.com/subscribe/token-plan",
            "https://github.com/MiniMax-AI/cli",
            "https://platform.minimaxi.com/protocol/paid-agreement",
            "https://platform.minimax.io/subscribe/token-plan",
        ),
        runtime_status="AUTHENTICATION_INTEGRATION_BLOCKED",
    ),
    ("openai", "api"): QuotaObservabilityCapability(
        provider_id="openai",
        plan_id="api",
        official_api=True,
        endpoint="https://api.openai.com/v1/organization/usage/completions",
        authentication="OpenAI organization admin key for Usage/Costs API",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=True,
        rate_limit=None,
        quota_windows=(),
        scope="OpenAI organization API usage/cost, not ChatGPT/Codex plan allowance",
        remaining_semantics=(
            "Usage and Costs APIs provide exact historical API consumption/spend; they do not "
            "represent remaining ChatGPT/Codex subscription allowance"
        ),
        confidence=EvidenceConfidence.UNKNOWN,
        source_uris=("https://platform.openai.com/docs/api-reference/usage",),
        runtime_status="AUDITED_COLLECTOR_DEFERRED",
    ),
    ("openai", "codex-chatgpt-plan"): QuotaObservabilityCapability(
        provider_id="openai",
        plan_id="codex-chatgpt-plan",
        official_api=False,
        endpoint=None,
        authentication="ChatGPT account login used by Codex",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=False,
        automation_allowed=None,
        rate_limit=None,
        quota_windows=("account-displayed allowance/reset windows",),
        scope="ChatGPT agentic allowance shared by Codex and other supported agentic features",
        remaining_semantics=(
            "Settings/usage dashboard and Codex CLI /status can show exhausted allowance, credit "
            "balance and reset time; no documented stable plan-quota API is used here"
        ),
        confidence=EvidenceConfidence.UNKNOWN,
        source_uris=("https://help.openai.com/en/articles/11369540-using-codex-with-chatgpt",),
        runtime_status="MANUAL_OR_CLI_STATUS_ONLY",
    ),
    ("zai", "coding-plan"): QuotaObservabilityCapability(
        provider_id="zai",
        plan_id="coding-plan",
        official_api=True,
        endpoint="https://api.z.ai/api/monitor/usage/quota/limit",
        authentication="Authorization token used by provider-published Coding Plan usage tooling",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=None,
        rate_limit=None,
        quota_windows=("5-hour", "weekly", "monthly MCP"),
        scope="Coding Plan remote quota/usage",
        remaining_semantics=(
            "Official plugin exposes quota percentage but raw remaining-vs-used semantics are not "
            "stable enough for an EXACT remaining interpretation"
        ),
        confidence=EvidenceConfidence.ESTIMATED,
        source_uris=(
            "https://zcode.z.ai/en/docs/usage-stats",
            "https://zcode.z.ai/en/docs/configuration",
            "https://github.com/zai-org/zai-coding-plugins",
        ),
        runtime_status="DEFERRED_PENDING_QUOTA_RESET_OR_AUTH",
    ),
}


def quota_observability(provider_id: str, plan_id: str) -> QuotaObservabilityCapability:
    """Return audited quota observability for one provider/plan identity."""

    try:
        return _CAPABILITIES[(provider_id, plan_id)]
    except KeyError as exc:
        raise LookupError(f"no quota observability audit for {provider_id}/{plan_id}") from exc


def all_quota_observability() -> tuple[QuotaObservabilityCapability, ...]:
    return tuple(_CAPABILITIES[key] for key in sorted(_CAPABILITIES))
