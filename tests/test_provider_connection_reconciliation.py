"""Connection reconciliation: catalog discovery is not a connection, and never becomes one.

P4.2.6.1 §4/§5. Three sets stay distinct:

* CONNECTED — explicit ``provider-connections.json`` records.
* AVAILABLE_TO_ADD — catalog-discovered but never connected.
* IMPORT_CANDIDATES — historical surfaces with strong, *surface-scoped* evidence.

The upgrade problem these tests pin down: a strict registry turns an owner's working
setup into a blank Connected tab. The fix must restore that setup without ever letting
"the binary knows this provider's name" masquerade as "the owner had it working".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.provider_connections import (
    ImportEvidenceKind,
    ProviderConnectionRegistry,
    ProviderConnectionState,
    build_import_candidates,
    connect_from_discovery,
    disconnect_provider,
    import_connections,
    load_connections,
    project_connections,
    save_connections,
)
from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
)

_OBSERVED = datetime(2026, 1, 1, tzinfo=UTC)


def _provider(
    provider_id: str,
    *,
    display_name: str | None = None,
    region: str | None = None,
    credential_region_verified: bool = False,
    credential_plan_surface_verified: bool = False,
    credential_scope_verified: bool = False,
    catalog_discovered: bool = True,
) -> ProviderDiscovery:
    return ProviderDiscovery(
        provider_id=provider_id,
        display_name=display_name or provider_id,
        auth_status=(
            AuthStatus.AUTH_FROM_ENV_PRESENCE
            if credential_scope_verified
            else AuthStatus.AUTH_REQUIRED
        ),
        execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
        evidence_source="DISCOVERED_FROM_CATALOG",
        model_skus=(f"{provider_id}/model-a",),
        env_variables_present=(),
        observed_at=_OBSERVED,
        region=region,
        catalog_discovered=catalog_discovered,
        credential_evidence_present=credential_scope_verified,
        credential_region_verified=credential_region_verified,
        credential_plan_surface_verified=credential_plan_surface_verified,
        credential_scope_verified=credential_scope_verified,
        execution_verified=False,
    )


def _discovery(*providers: ProviderDiscovery) -> DiscoveryResult:
    return DiscoveryResult(
        discovered_at=_OBSERVED,
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=providers,
        state=DiscoveryState.DISCOVERED,
    )


# --------------------------------------------------------------------------
# Catalog discovery is never, by itself, sufficient
# --------------------------------------------------------------------------


def test_catalog_only_provider_is_not_connected() -> None:
    discovery = _discovery(_provider("zai-coding-plan"))
    projection = project_connections(ProviderConnectionRegistry(), discovery)

    assert projection.connected == ()
    assert [item["provider_id"] for item in projection.available_to_add] == [
        "zai-coding-plan"
    ]


def test_catalog_only_provider_is_not_an_import_candidate() -> None:
    """The core anti-regression: discovery alone must never propose an import."""

    discovery = _discovery(_provider("zai-coding-plan"))

    candidates = build_import_candidates(ProviderConnectionRegistry(), discovery)

    assert candidates == ()


def test_scoped_credential_evidence_creates_import_candidate() -> None:
    discovery = _discovery(
        _provider(
            "zai-coding-plan",
            display_name="GLM / Z.AI",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )

    candidates = build_import_candidates(ProviderConnectionRegistry(), discovery)

    assert [candidate.provider_id for candidate in candidates] == ["zai-coding-plan"]
    kinds = {item.kind for item in candidates[0].evidence}
    assert ImportEvidenceKind.CREDENTIAL_REGION_SCOPED in kinds
    assert ImportEvidenceKind.CREDENTIAL_PLAN_SURFACE_SCOPED in kinds
    # No execution has been proven, so the candidate must not claim it.
    assert candidates[0].execution_verified is False


def test_prior_verified_execution_creates_import_candidate() -> None:
    """The real P4.2.4-B signal: GLM 5.3 actually ran and verified."""

    discovery = _discovery(_provider("zai-coding-plan"))

    candidates = build_import_candidates(
        ProviderConnectionRegistry(),
        discovery,
        verified_execution_lookup=lambda provider_id: (
            _OBSERVED if provider_id == "zai-coding-plan" else None
        ),
    )

    assert [candidate.provider_id for candidate in candidates] == ["zai-coding-plan"]
    assert candidates[0].execution_verified is True
    assert ImportEvidenceKind.PRIOR_VERIFIED_EXECUTION in {
        item.kind for item in candidates[0].evidence
    }


# --------------------------------------------------------------------------
# Evidence scope never crosses a surface boundary
# --------------------------------------------------------------------------


def test_zai_execution_evidence_does_not_promote_minimax() -> None:
    discovery = _discovery(
        _provider("zai-coding-plan"),
        _provider("minimax-cn-coding-plan"),
    )

    candidates = build_import_candidates(
        ProviderConnectionRegistry(),
        discovery,
        verified_execution_lookup=lambda provider_id: (
            _OBSERVED if provider_id == "zai-coding-plan" else None
        ),
    )

    assert [candidate.provider_id for candidate in candidates] == ["zai-coding-plan"]


def test_minimax_region_and_plan_scope_is_preserved() -> None:
    """CN Coding Plan evidence must not admit International, nor the Token Plan."""

    discovery = _discovery(
        _provider(
            "minimax-cn-coding-plan",
            display_name="MiniMax CN Coding Plan",
            region="CN",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        ),
        _provider(
            "minimax-coding-plan",
            display_name="MiniMax International",
            region="International",
        ),
        _provider("minimax-cn", display_name="MiniMax CN", region="CN"),
    )

    candidates = build_import_candidates(ProviderConnectionRegistry(), discovery)

    assert [candidate.provider_id for candidate in candidates] == [
        "minimax-cn-coding-plan"
    ]
    assert candidates[0].region == "CN"
    assert candidates[0].plan_surface == "Coding Plan"


def test_region_evidence_alone_does_not_admit_a_plan_distinguished_surface() -> None:
    """Region match without plan-surface match is not scope-verified evidence."""

    discovery = _discovery(
        _provider(
            "minimax-cn-coding-plan",
            region="CN",
            credential_region_verified=True,
            credential_plan_surface_verified=False,
            credential_scope_verified=False,
        )
    )

    assert build_import_candidates(ProviderConnectionRegistry(), discovery) == ()


def test_uncatalogued_surface_with_scoped_evidence_is_still_importable() -> None:
    """Evidence, not the current catalog snapshot, decides candidacy.

    A surface the owner demonstrably used can drop out of ``opencode models``.
    Requiring catalog presence here would recreate the disappearing-connection
    defect, so candidacy is deliberately catalog-independent.
    """

    discovery = _discovery(
        _provider(
            "minimax-cn",
            region="CN",
            catalog_discovered=False,
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )

    candidates = build_import_candidates(ProviderConnectionRegistry(), discovery)
    projection = project_connections(ProviderConnectionRegistry(), discovery)

    assert [candidate.provider_id for candidate in candidates] == ["minimax-cn"]
    # ...but it is not something to "add" from the catalog, because it is not in one.
    assert projection.available_to_add == ()


def test_uncatalogued_surface_without_evidence_stays_invisible() -> None:
    discovery = _discovery(_provider("minimax", catalog_discovered=False))

    projection = project_connections(ProviderConnectionRegistry(), discovery)

    assert projection.available_to_add == ()
    assert projection.import_candidates == ()


def test_plan_surfaces_are_labelled_distinctly() -> None:
    discovery = _discovery(
        _provider("minimax-cn", region="CN"),
        _provider("minimax-cn-coding-plan", region="CN"),
    )
    projection = project_connections(ProviderConnectionRegistry(), discovery)
    surfaces = {
        item["provider_id"]: item["plan_surface"] for item in projection.available_to_add
    }

    assert surfaces == {
        "minimax-cn": "Token Plan",
        "minimax-cn-coding-plan": "Coding Plan",
    }


# --------------------------------------------------------------------------
# Import is an owner action, and copies no secrets
# --------------------------------------------------------------------------


def test_import_requires_owner_action_and_does_not_happen_implicitly() -> None:
    discovery = _discovery(
        _provider(
            "zai-coding-plan",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )
    registry = ProviderConnectionRegistry()

    # Merely projecting the state — what every dashboard refresh does — must not connect.
    projection = project_connections(registry, discovery)
    assert projection.connected == ()
    assert len(projection.import_candidates) == 1
    assert registry.connected_provider_ids() == frozenset()

    imported = import_connections(
        registry, discovery, provider_ids=("zai-coding-plan",)
    )
    assert imported.connected_provider_ids() == frozenset({"zai-coding-plan"})


def test_importing_a_non_candidate_is_refused() -> None:
    discovery = _discovery(_provider("zai-coding-plan"))

    try:
        import_connections(
            ProviderConnectionRegistry(), discovery, provider_ids=("zai-coding-plan",)
        )
    except LookupError as error:
        assert str(error) == "provider_not_import_candidate"
    else:  # pragma: no cover - failure path
        raise AssertionError("catalog-only provider must not be importable")


def test_import_persists_no_credential_material(tmp_path: Path) -> None:
    discovery = _discovery(
        _provider(
            "zai-coding-plan",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )
    registry = import_connections(
        ProviderConnectionRegistry(), discovery, provider_ids=("zai-coding-plan",)
    )
    save_connections(registry, runtime_state_root=tmp_path)

    raw = (tmp_path / "provider-connections.json").read_text(encoding="utf-8")

    # A reference *type* is fine; a value is never acceptable.
    assert "OPENCODE_AUTH" in raw or "UNKNOWN" in raw
    for forbidden in ("api_key", "apiKey", "token", "Bearer", "auth.json", "secret"):
        assert forbidden not in raw


def test_connections_survive_restart(tmp_path: Path) -> None:
    discovery = _discovery(
        _provider(
            "zai-coding-plan",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )
    registry = import_connections(
        ProviderConnectionRegistry(), discovery, provider_ids=("zai-coding-plan",)
    )
    save_connections(registry, runtime_state_root=tmp_path)

    reloaded = load_connections(tmp_path)

    assert reloaded.connected_provider_ids() == frozenset({"zai-coding-plan"})


# --------------------------------------------------------------------------
# Disconnect semantics
# --------------------------------------------------------------------------


def test_disconnect_removes_scheduler_eligibility() -> None:
    discovery = _discovery(_provider("zai-coding-plan"))
    connected = connect_from_discovery(
        ProviderConnectionRegistry(), discovery, provider_id="zai-coding-plan"
    )
    assert connected.connected_provider_ids() == frozenset({"zai-coding-plan"})

    after = disconnect_provider(connected, provider_id="zai-coding-plan")

    assert after.connected_provider_ids() == frozenset()
    record = after.connections["zai-coding-plan"]
    assert record.connection_state is ProviderConnectionState.DISCONNECTED
    assert record.last_reason_code == "OWNER_DISCONNECTED"


def test_disconnected_provider_is_not_re_proposed_for_import() -> None:
    """Disconnect is a decision, not a glitch — reconciliation must not undo it."""

    discovery = _discovery(
        _provider(
            "zai-coding-plan",
            credential_region_verified=True,
            credential_plan_surface_verified=True,
            credential_scope_verified=True,
        )
    )
    connected = connect_from_discovery(
        ProviderConnectionRegistry(), discovery, provider_id="zai-coding-plan"
    )
    after = disconnect_provider(connected, provider_id="zai-coding-plan")

    assert build_import_candidates(after, discovery) == ()
