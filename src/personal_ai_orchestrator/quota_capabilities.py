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
    ("minimax", "token-plan"): QuotaObservabilityCapability(
        provider_id="minimax",
        plan_id="token-plan",
        official_api=True,
        endpoint="https://www.minimax.io/v1/token_plan/remains",
        authentication="Bearer Token Plan subscription key",
        existing_opencode_auth_reusable_without_secret_read=False,
        requires_independent_credential_surface=True,
        automation_allowed=True,
        rate_limit=(
            "Dynamic RPM/TPM throttling; provider says throttles typically reset "
            "within ~1 minute"
        ),
        quota_windows=("5-hour rolling", "weekly"),
        scope="Token Plan shared quota with provider-returned model remains records",
        remaining_semantics=(
            "Provider API exposes explicit remaining percentage fields when available"
        ),
        confidence=EvidenceConfidence.EXACT,
        source_uris=(
            "https://platform.minimax.io/subscribe/token-plan",
            "https://www.minimax.io/v1/token_plan/remains",
            "https://github.com/MiniMax-AI/cli",
            "https://platform.minimax.io/protocol/paid-agreement",
        ),
        runtime_status="AUTHENTICATION_INTEGRATION_BLOCKED",
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
