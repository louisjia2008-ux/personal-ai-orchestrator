#!/usr/bin/env python3
"""Bootstrap and summarize a Shadow campaign without claiming collection is active."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.provider_acceptance import (
    AuthSurface,
    LiveProviderResult,
    ProviderSurfaceEvidence,
    QuotaSurface,
    assert_sanitized,
)
from personal_ai_orchestrator.shadow_evidence import (
    ShadowAcceptancePolicy,
    ShadowCampaignState,
    ShadowCampaignStatus,
    ShadowEvidenceJournal,
)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def _probe(argv: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, TimeoutError):
        return False, "COMMAND_UNAVAILABLE_OR_TIMEOUT"
    return completed.returncode == 0, f"EXIT_{completed.returncode}"


def _provider_evidence(observed_at: datetime) -> tuple[ProviderSurfaceEvidence, ...]:
    opencode = shutil.which("opencode")
    claude = shutil.which("claude")
    codex = shutil.which("codex")
    ollama = shutil.which("ollama")
    minimax_models_ok = False
    zai_models_ok = False
    if opencode is not None:
        minimax_models_ok, _ = _probe([opencode, "models", "minimax"])
        zai_models_ok, _ = _probe([opencode, "models", "zai"])

    return (
        ProviderSurfaceEvidence(
            provider_id="minimax",
            plan_id="token-plan",
            auth_surface=AuthSurface.UNKNOWN,
            quota_surface=QuotaSurface.NONE_SUPPORTED,
            live_result=LiveProviderResult.LIVE_UNKNOWN,
            command_family="opencode models minimax",
            observed_at=observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics="OpenCode model catalog is visible but does not expose quota truth",
            reset_semantics="UNKNOWN",
            source_method="existing-cli-metadata",
            sanitized_status=(
                "OPENCODE_MINIMAX_MODELS_AVAILABLE"
                if minimax_models_ok
                else "OPENCODE_MINIMAX_MODELS_UNAVAILABLE"
            ),
        ),
        ProviderSurfaceEvidence(
            provider_id="zai",
            plan_id="coding-plan",
            auth_surface=AuthSurface.NONE_SUPPORTED if not zai_models_ok else AuthSurface.UNKNOWN,
            quota_surface=QuotaSurface.NONE_SUPPORTED,
            live_result=LiveProviderResult.LIVE_UNKNOWN,
            command_family="opencode models zai",
            observed_at=observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics="No supported machine-readable quota surface was available locally",
            reset_semantics="UNKNOWN",
            source_method="existing-cli-metadata",
            sanitized_status=(
                "OPENCODE_ZAI_PROVIDER_AVAILABLE"
                if zai_models_ok
                else "OPENCODE_ZAI_PROVIDER_NOT_FOUND"
            ),
        ),
        ProviderSurfaceEvidence(
            provider_id="openai",
            plan_id="codex-chatgpt-plan",
            auth_surface=AuthSurface.UNKNOWN if codex else AuthSurface.NONE_SUPPORTED,
            quota_surface=QuotaSurface.NONE_SUPPORTED,
            live_result=LiveProviderResult.LIVE_UNKNOWN,
            command_family="codex --version",
            observed_at=observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics="Codex CLI availability is not subscription remaining-quota truth",
            reset_semantics="UNKNOWN",
            source_method="existing-cli-metadata",
            sanitized_status="CODEX_CLI_AVAILABLE" if codex else "CODEX_CLI_NOT_FOUND",
        ),
        ProviderSurfaceEvidence(
            provider_id="anthropic",
            plan_id="claude-plan",
            auth_surface=AuthSurface.UNKNOWN if claude else AuthSurface.NONE_SUPPORTED,
            quota_surface=QuotaSurface.NONE_SUPPORTED,
            live_result=LiveProviderResult.LIVE_UNKNOWN,
            command_family="claude --version",
            observed_at=observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics="Claude CLI availability is not subscription remaining-quota truth",
            reset_semantics="UNKNOWN",
            source_method="existing-cli-metadata",
            sanitized_status="CLAUDE_CLI_AVAILABLE" if claude else "CLAUDE_CLI_NOT_FOUND",
        ),
        ProviderSurfaceEvidence(
            provider_id="deepseek",
            plan_id="api",
            auth_surface=AuthSurface.AUTH_REQUIRED,
            quota_surface=QuotaSurface.BALANCE_ONLY,
            live_result=LiveProviderResult.NOT_EXECUTED,
            command_family=None,
            observed_at=observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics=(
                "PAYG balance is not subscription quota; no safe credential handoff supplied"
            ),
            reset_semantics="not a reset-window subscription quota",
            source_method="static-audit-plus-local-command-discovery",
            sanitized_status="NO_SUPPORTED_CREDENTIAL_HANDOFF",
        ),
        ProviderSurfaceEvidence(
            provider_id="local",
            plan_id="unmetered",
            auth_surface=AuthSurface.PROVIDER_NATIVE_CLI if ollama else AuthSurface.NONE_SUPPORTED,
            quota_surface=QuotaSurface.NONE_SUPPORTED,
            live_result=LiveProviderResult.NOT_SUPPORTED,
            command_family="ollama list",
            observed_at=observed_at,
            confidence=EvidenceConfidence.EXACT,
            quota_semantics="Local capacity is runtime availability, not provider quota",
            reset_semantics="not applicable",
            source_method="existing-cli-metadata",
            sanitized_status="OLLAMA_AVAILABLE" if ollama else "OLLAMA_NOT_FOUND",
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Shadow campaign state")
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(".personal-ai-orchestrator/p36-shadow"),
    )
    parser.add_argument("--catalog-snapshot-id", default="catalog-p36-local")
    parser.add_argument("--policy-snapshot-id", default="policy-p36-local")
    parser.add_argument("--minimum-observations", type=int, default=20)
    parser.add_argument("--minimum-real-reset-cycles", type=int, default=2)
    args = parser.parse_args(argv)

    observed_at = datetime.now(UTC)
    journal = ShadowEvidenceJournal(args.state_root)
    campaign = journal.load_campaign_state()
    if campaign is None:
        campaign = ShadowCampaignState(
            campaign_id=f"shadow-{observed_at.strftime('%Y%m%dT%H%M%SZ')}",
            status=ShadowCampaignStatus.BOOTSTRAPPED,
            started_at=observed_at,
            head=_git_head(),
            catalog_snapshot_id=args.catalog_snapshot_id,
            policy_snapshot_id=args.policy_snapshot_id,
            providers_enabled=("minimax", "openai", "anthropic", "local"),
            providers_unknown=("zai", "deepseek"),
            reset_cycles_required=args.minimum_real_reset_cycles,
            acceptance_policy=ShadowAcceptancePolicy(
                minimum_observations=args.minimum_observations,
                minimum_real_reset_cycles=args.minimum_real_reset_cycles,
            ),
        )
        journal.save_campaign_state(campaign)

    provider_evidence = _provider_evidence(observed_at)
    provider_payload = [item.model_dump(mode="json") for item in provider_evidence]
    summary = journal.summarize_campaign(
        minimum_observations=20,
        minimum_reset_cycles=campaign.reset_cycles_required,
    )
    report = {
        "generated_at": observed_at.isoformat(),
        "campaign": campaign.model_dump(mode="json"),
        "provider_evidence": provider_payload,
        "summary": summary.model_dump(mode="json"),
        "production_active": "DISABLED_BY_DESIGN",
        "campaign_state_semantics": (
            "BOOTSTRAPPED means durable state/report exist; COLLECTING requires automatic "
            "ShadowObservation wiring to feed this evidence path"
        ),
    }
    assert_sanitized(report)
    args.state_root.mkdir(parents=True, exist_ok=True)
    target = args.state_root / "p36-shadow-campaign-report.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)
    print(campaign.campaign_id)
    print(summary.quality_observations)
    print(summary.real_reset_cycles_observed)
    print(summary.review_eligible)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
