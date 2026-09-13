"""P4.2.6.4 — connected-provider quota observability.

Two product truths are pinned here:

1. Connecting a provider and observing its quota are *separate* operations,
   but a connected provider is always visible on the Quota page — with
   ``UNKNOWN`` when no evidence exists, never by disappearing.
2. Quota refresh is read-only. It must never spend model quota to measure
   quota, and it must never fabricate a percentage it did not observe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import (
    ControlPlaneService,
    QuotaRefreshResultView,
)
from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    ModelRegistry,
    QuotaWindowKind,
)
from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
)
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.provider_registry_store import save
from personal_ai_orchestrator.quota_acceptance import (
    ScopedRefreshTargetMissing,
    ScopedRefreshWidened,
    refresh_all_provider_quota,
    scoped_quota_refresh,
)
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.quota_collectors.minimax import MiniMaxQuotaCollector
from personal_ai_orchestrator.quota_collectors.zai import ZAIQuotaCollector
from personal_ai_orchestrator.quota_refresh import (
    QUOTA_SOURCE_BY_PROVIDER,
    QuotaPageState,
    QuotaRefreshReason,
    QuotaRefreshService,
    assert_read_only_collector,
    has_readonly_quota_source,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal

OBSERVED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _discovery_result() -> DiscoveryResult:
    return DiscoveryResult(
        discovered_at=OBSERVED_AT,
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=OBSERVED_AT,
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_scope_verified=True,
                execution_verified=False,
            ),
            ProviderDiscovery(
                provider_id="minimax-cn-coding-plan",
                display_name="MiniMax CN Coding Plan",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("minimax-m2",),
                env_variables_present=("MINIMAX_API_KEY",),
                observed_at=OBSERVED_AT,
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_scope_verified=True,
                execution_verified=False,
            ),
        ),
        state=DiscoveryState.DISCOVERED,
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProviderRegistryManager:
    root = tmp_path / "runtime-state"
    root.mkdir()
    result = _discovery_result()
    save(result, runtime_state_root=root)

    def _no_op_discover(**_kwargs: object) -> DiscoveryCycleOutcome:
        return DiscoveryCycleOutcome(result=result, error_code=None, error_message=None)

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _no_op_discover,
    )
    return ProviderRegistryManager(runtime_state_root=root)


def _service(
    tmp_path: Path,
    manager: ProviderRegistryManager | None,
    *,
    collectors: dict[str, object] | None = None,
) -> ControlPlaneService:
    root = tmp_path / "runtime-state"
    root.mkdir(exist_ok=True)
    refresh = QuotaRefreshService(
        runtime_state_root=root,
        connected_provider_ids=(
            manager.connected_provider_ids if manager is not None else tuple
        ),
        collectors=collectors,
        environ={},
        auth_store_paths=(),
    )
    return ControlPlaneService(
        registry=ModelRegistry(),
        store=SafetyKernelStore(tmp_path / "state.db"),
        activation_gate=ActiveRoutingGate(),
        runtime_availability={},
        verification_journal=VerificationEvidenceJournal(root),
        quota_availability_journal=QuotaAvailabilityJournal(root),
        provider_registry_manager=manager,
        quota_refresh_service=refresh,
    )


class _StubCollector:
    """Read-only collector stub. Exposes ``collect`` and nothing billable."""

    def __init__(self, result: QuotaCollectionResult) -> None:
        self._result = result
        self.calls = 0

    def collect(self) -> QuotaCollectionResult:
        self.calls += 1
        return self._result


class _RecordingTransport:
    """Captures every provider call a collector makes."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float):
        self.calls.append(("GET", url))
        return self.payload


# ---------------------------------------------------------------------------
# §7/§8/§14 — connected providers are always visible
# ---------------------------------------------------------------------------


def test_no_connected_provider_state(tmp_path: Path, manager) -> None:
    view = _service(tmp_path, manager).quota()
    assert view.state == QuotaPageState.NO_CONNECTED_PROVIDER.value
    assert view.providers == ()
    assert view.summary.connected_provider_count == 0


def test_connected_provider_with_zero_quota_pools_stays_visible(
    tmp_path: Path, manager
) -> None:
    """Required regression case (§14).

    CONNECTED + zero observations must not equal "provider invisible", and
    must not be reported as "no provider registered".
    """

    manager.connect_provider("zai-coding-plan")
    view = _service(tmp_path, manager).quota()

    assert view.state == QuotaPageState.CONNECTED_BUT_QUOTA_UNKNOWN.value
    assert len(view.providers) == 1
    card = view.providers[0]
    assert card.provider_id == "zai-coding-plan"
    assert card.display_name == "GLM / Z.AI"
    assert card.connection_state == "CONNECTED"
    assert card.quota_state == "UNKNOWN"
    assert card.confidence == "UNKNOWN"
    assert card.quota_pools == ()
    # A read-only source exists for this surface, but no credential is present.
    assert card.readonly_source_available is True
    assert card.collector_available is False
    assert card.failure_reason == QuotaRefreshReason.CREDENTIAL_NOT_AVAILABLE.value


def test_connect_makes_provider_immediately_quota_visible(
    tmp_path: Path, manager
) -> None:
    """§11 — no app restart, no discovery cycle, no quota evidence required."""

    service = _service(tmp_path, manager)
    assert service.quota().summary.connected_provider_count == 0
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    view = service.quota()
    assert [card.provider_id for card in view.providers] == ["minimax-cn-coding-plan"]


def test_disconnect_removes_provider_from_quota_projection(
    tmp_path: Path, manager
) -> None:
    """§18 — a disconnected provider must not still read as currently usable."""

    service = _service(tmp_path, manager)
    service.connect_provider({"provider_id": "zai-coding-plan"})
    assert service.quota().summary.connected_provider_count == 1
    service.disconnect_provider("zai-coding-plan", {"confirm": True})
    view = service.quota()
    assert view.providers == ()
    assert view.state == QuotaPageState.NO_CONNECTED_PROVIDER.value


def test_summary_counts_unknown_as_connected_but_not_observable(
    tmp_path: Path, manager
) -> None:
    """§15 — UNKNOWN is never classified as healthy/observable."""

    service = _service(tmp_path, manager)
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    summary = service.quota().summary
    assert summary.connected_provider_count == 2
    assert summary.quota_observable_provider_count == 0
    assert summary.quota_unknown_provider_count == 2
    assert summary.quota_warning_count == 0
    assert summary.quota_exhausted_count == 0


# ---------------------------------------------------------------------------
# §9/§10 — refresh is distinct from discovery and never billable
# ---------------------------------------------------------------------------


def test_quota_refresh_is_distinct_from_provider_discovery(
    tmp_path: Path, manager
) -> None:
    service = _service(tmp_path, manager)
    service.connect_provider({"provider_id": "zai-coding-plan"})
    before = manager.discovery_cycle_count()
    service.refresh_quota()
    assert manager.discovery_cycle_count() == before, (
        "quota refresh must not run a provider-discovery cycle"
    )


def test_provider_refresh_does_not_collect_quota(tmp_path: Path, manager) -> None:
    collector = _StubCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.SUCCESS, snapshot=None)
    )
    service = _service(
        tmp_path, manager, collectors={"zai-coding-plan": collector}
    )
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.refresh_providers()
    assert collector.calls == 0


def test_quota_refresh_issues_only_read_only_gets(tmp_path: Path, manager) -> None:
    """§10 — no dummy model generation is ever used to discover quota."""

    transport = _RecordingTransport(
        {
            "data": {
                "limits": [
                    {"type": "TOKENS_LIMIT", "percentage": 25.0},
                ]
            }
        }
    )
    collector = ZAIQuotaCollector(
        authorization_token="token-not-persisted", transport=transport
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.refresh_quota()

    assert transport.calls, "collector must contact the provider"
    for method, url in transport.calls:
        assert method == "GET"
        assert "chat" not in url
        assert "completion" not in url
        assert "generat" not in url


def test_real_collectors_expose_no_billable_surface() -> None:
    for collector in (
        ZAIQuotaCollector(authorization_token=None),
        MiniMaxQuotaCollector(bearer_token=None),
    ):
        assert_read_only_collector(collector)


def test_assert_read_only_collector_rejects_billable_surface() -> None:
    class Billable:
        def collect(self):  # pragma: no cover - never reached
            raise AssertionError

        def generate(self):  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(ValueError):
        assert_read_only_collector(Billable())


# ---------------------------------------------------------------------------
# §13/§16/§17 — truth contract and history
# ---------------------------------------------------------------------------


def test_successful_refresh_renders_real_windows_and_feeds_history(
    tmp_path: Path, manager
) -> None:
    transport = _RecordingTransport(
        {
            "model_remains": [
                {
                    "current_interval_remaining_percent": 42.0,
                    "current_weekly_remaining_percent": 17.0,
                    "start_time": 1_772_000_000_000,
                    "end_time": 1_772_018_000_000,
                    "weekly_start_time": 1_771_900_000_000,
                    "weekly_end_time": 1_772_500_000_000,
                }
            ]
        }
    )
    collector = MiniMaxQuotaCollector(
        bearer_token="token-not-persisted", region="cn", transport=transport
    )
    service = _service(
        tmp_path, manager, collectors={"minimax-cn-coding-plan": collector}
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})

    result = service.refresh_quota()
    assert result.refreshed_provider_ids == ("minimax-cn-coding-plan",)

    view = result.overview
    assert view.state == QuotaPageState.CONNECTED_WITH_QUOTA_OBSERVATIONS.value
    card = view.providers[0]
    assert card.quota_state == "OBSERVED"
    assert card.confidence == "EXACT"
    assert card.measurement_source == "PROVIDER_API"
    assert card.observed_at is not None
    pool = card.quota_pools[0]
    windows = {window.window_id: window for window in pool.windows}
    assert windows["5h"].remaining_fraction == pytest.approx(0.42)
    assert windows["weekly"].remaining_fraction == pytest.approx(0.17)
    assert windows["5h"].confidence == "EXACT"
    assert view.summary.quota_observable_provider_count == 1

    # §17: real observations land in the existing bounded history store.
    history = service.quota_history()
    recorded = {row.window_id: row.remaining_fraction for row in history.observations}
    assert recorded["5h"] == pytest.approx(0.42)
    assert recorded["weekly"] == pytest.approx(0.17)


def test_unknown_confidence_never_fabricates_a_percentage(
    tmp_path: Path, manager
) -> None:
    """§13 — UNKNOWN renders no percentage and no progress input."""

    # A payload the collector cannot interpret yields UNKNOWN, not 0%.
    transport = _RecordingTransport({"data": {"limits": []}})
    collector = ZAIQuotaCollector(
        authorization_token="token-not-persisted", transport=transport
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    view = service.refresh_quota().overview

    card = view.providers[0]
    assert card.quota_state == "UNKNOWN"
    assert card.confidence == "UNKNOWN"
    for pool in card.quota_pools:
        for window in pool.windows:
            if window.confidence == "UNKNOWN":
                assert window.remaining_fraction is None
    # Nothing UNKNOWN may enter the history trend.
    assert service.quota_history().observations == ()


def test_estimated_confidence_is_preserved_not_upgraded(
    tmp_path: Path, manager
) -> None:
    """Z.AI derives remaining from a usage percentage: ESTIMATED, not EXACT."""

    transport = _RecordingTransport(
        {"data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 30.0}]}}
    )
    collector = ZAIQuotaCollector(
        authorization_token="token-not-persisted", transport=transport
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    card = service.refresh_quota().overview.providers[0]
    assert card.confidence == "ESTIMATED"
    assert card.quota_pools[0].windows[0].remaining_fraction == pytest.approx(0.70)


def test_failed_collector_keeps_provider_visible_with_sanitized_reason(
    tmp_path: Path, manager
) -> None:
    """§19 — a failed refresh must not leave a blank page."""

    collector = _StubCollector(
        QuotaCollectionResult(
            status=QuotaCollectionStatus.AUTH_REQUIRED,
            error_category="CREDENTIAL_NOT_AVAILABLE",
        )
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    view = service.refresh_quota().overview

    assert len(view.providers) == 1
    card = view.providers[0]
    assert card.quota_state == "UNKNOWN"
    assert card.last_refresh_status == "AUTH_REQUIRED"
    assert card.last_refresh_at is not None
    assert card.failure_reason == "CREDENTIAL_NOT_AVAILABLE"


def test_refresh_failure_after_success_keeps_last_known_good(
    tmp_path: Path, manager
) -> None:
    payload = {
        "data": {
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 10.0}
            ]
        }
    }
    transport = _RecordingTransport(payload)
    good = ZAIQuotaCollector(authorization_token="t", transport=transport)
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": good})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.refresh_quota()
    assert service.quota().providers[0].quota_state == "OBSERVED"

    failing = _StubCollector(
        QuotaCollectionResult(
            status=QuotaCollectionStatus.PROVIDER_ERROR, error_category="HTTP_500"
        )
    )
    service.quota_refresh_service._injected_collectors["zai-coding-plan"] = failing
    view = service.refresh_quota().overview
    card = view.providers[0]
    # Last-known-good remains visible; the failure is reported alongside it.
    assert card.quota_state == "OBSERVED"
    assert card.last_refresh_status == "STALE"


def test_provider_scoped_refresh_only_touches_that_provider(
    tmp_path: Path, manager
) -> None:
    zai = _StubCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN, error_category=None)
    )
    minimax = _StubCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN, error_category=None)
    )
    service = _service(
        tmp_path,
        manager,
        collectors={"zai-coding-plan": zai, "minimax-cn-coding-plan": minimax},
    )
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    result = service.refresh_quota("zai-coding-plan")
    assert result.refreshed_provider_ids == ("zai-coding-plan",)
    assert zai.calls == 1
    assert minimax.calls == 0
    # Both providers stay visible regardless of which one was refreshed.
    assert len(result.overview.providers) == 2


def test_refresh_of_unconnected_provider_is_a_no_op(tmp_path: Path, manager) -> None:
    collector = _StubCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN, error_category=None)
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    result = service.refresh_quota("zai-coding-plan")
    assert result.refreshed_provider_ids == ()
    assert collector.calls == 0


def test_video_scope_never_suppresses_the_coding_quota_figure(
    tmp_path: Path, manager
) -> None:
    """A video balance must not blank the provider's coding quota.

    ``general`` and ``video`` are workload scopes, not models and not two views
    of one balance. This orchestrator schedules coding and text work, so
    ``general`` is the scope its MiniMax targets draw on and ``video`` says
    nothing about them in either direction.

    Both earlier implementations read the disagreement as "no plan figure is
    derivable" and reported the provider UNKNOWN. Here the coding projection
    reads ``general`` — 95% over 5 hours, 60% weekly, exactly what the provider
    said — while the video observation is preserved beside it, out of scope.
    """

    transport = _RecordingTransport(
        {
            "model_remains": [
                {
                    "model_name": "general",
                    "current_interval_remaining_percent": 95,
                    "current_weekly_remaining_percent": 60,
                },
                {
                    "model_name": "video",
                    "current_interval_remaining_percent": 100,
                    "current_weekly_remaining_percent": 60,
                },
            ],
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
    )
    collector = MiniMaxQuotaCollector(
        bearer_token="token-not-persisted", region="cn", transport=transport
    )
    service = _service(
        tmp_path, manager, collectors={"minimax-cn-coding-plan": collector}
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    card = service.refresh_quota().overview.providers[0]

    # The coding scope is fully readable, so the provider is observable.
    assert card.quota_state == "OBSERVED"
    assert card.confidence == "EXACT"
    assert card.failure_reason is None
    assert card.last_refresh_status == "SUCCESS"
    assert card.last_refresh_at is not None

    projection = collector.collect().projection
    assert projection is not None
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None and weekly is not None
    # The provider's own general figures, neither averaged with video nor
    # limited by it.
    assert five_hour.remaining_fraction == pytest.approx(0.95)
    assert five_hour.confidence is EvidenceConfidence.EXACT
    assert weekly.remaining_fraction == pytest.approx(0.60)
    assert weekly.confidence is EvidenceConfidence.EXACT
    # Video is preserved as a real observation, out of the coding workload.
    assert {view.scope_id for view in projection.model_equivalents} == {
        "general",
        "video",
    }
    assert projection.workload_scope_notes == ("VIDEO_SCOPE_IGNORED_FOR_CODING",)
    # ...and are not promoted into the list of models the owner can route to:
    # "general" and "video" are MiniMax workload scopes, not models.
    assert projection.covered_model_ids() == ()


def test_agreeing_workload_scopes_still_yield_one_exact_plan_figure(
    tmp_path: Path, manager
) -> None:
    """Scope agreement is not what makes the figure readable, but must still work.

    Coding quota comes from ``general`` whether or not ``video`` happens to
    match it, so this payload reads exactly like the disagreeing one above.
    """

    transport = _RecordingTransport(
        {
            "model_remains": [
                {
                    "model_name": "general",
                    "current_interval_remaining_percent": 63,
                    "current_weekly_remaining_percent": 81,
                },
                {
                    "model_name": "video",
                    "current_interval_remaining_percent": 63,
                    "current_weekly_remaining_percent": 81,
                },
            ]
        }
    )
    collector = MiniMaxQuotaCollector(
        bearer_token="token-not-persisted", region="cn", transport=transport
    )
    projection = collector.collect().projection
    assert projection is not None

    assert projection.confidence is EvidenceConfidence.EXACT
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None and weekly is not None
    assert five_hour.remaining_fraction == pytest.approx(0.63)
    assert weekly.remaining_fraction == pytest.approx(0.81)
    # Models draw on one pool; none owns a balance of its own.
    assert projection.pool.shared_across_models is True
    assert projection.binding_window.window_id == "5h"


def test_empty_provider_payload_reports_no_entries(tmp_path: Path, manager) -> None:
    collector = MiniMaxQuotaCollector(
        bearer_token="t", region="cn", transport=_RecordingTransport({"model_remains": []})
    )
    service = _service(
        tmp_path, manager, collectors={"minimax-cn-coding-plan": collector}
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    card = service.refresh_quota().overview.providers[0]
    assert card.failure_reason == "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"


def test_zai_empty_limits_reports_no_entries(tmp_path: Path, manager) -> None:
    collector = ZAIQuotaCollector(
        authorization_token="t", transport=_RecordingTransport({"data": {"limits": []}})
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    card = service.refresh_quota().overview.providers[0]
    assert card.failure_reason == "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"


def test_unknown_status_without_category_still_reports_a_reason(
    tmp_path: Path, manager
) -> None:
    collector = _StubCollector(
        QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN, error_category=None)
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    card = service.refresh_quota().overview.providers[0]
    assert card.failure_reason == "PROVIDER_QUOTA_NOT_INTERPRETABLE"


def test_anonymous_provider_entries_still_produce_an_exact_figure(
    tmp_path: Path, manager
) -> None:
    """An unnamed bar has no workload label, and must still be read.

    Workload classification narrows what is read; it must not require a name
    the provider never gave.
    """

    entry = {
        "current_interval_remaining_percent": 80.0,
        "current_weekly_remaining_percent": 80.0,
    }
    collector = MiniMaxQuotaCollector(
        bearer_token="t",
        region="cn",
        transport=_RecordingTransport({"model_remains": [dict(entry), dict(entry)]}),
    )
    service = _service(
        tmp_path, manager, collectors={"minimax-cn-coding-plan": collector}
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    card = service.refresh_quota().overview.providers[0]
    assert card.quota_state == "OBSERVED"
    assert card.confidence == "EXACT"


# ---------------------------------------------------------------------------
# Source table and credential handling
# ---------------------------------------------------------------------------


def test_known_provider_families_have_readonly_quota_sources() -> None:
    assert has_readonly_quota_source("zai-coding-plan")
    assert has_readonly_quota_source("minimax-cn-coding-plan")
    assert has_readonly_quota_source("minimax")
    assert not has_readonly_quota_source("some-unmapped-provider")


def test_minimax_aliases_share_regional_pools() -> None:
    sources = QUOTA_SOURCE_BY_PROVIDER
    assert sources["minimax-cn"].quota_pool_id == sources["minimax-cn-coding-plan"].quota_pool_id
    assert sources["minimax"].quota_pool_id == sources["minimax-coding-plan"].quota_pool_id
    assert sources["minimax-cn"].quota_pool_id != sources["minimax"].quota_pool_id


def test_collector_is_built_from_environment_credential_only(
    tmp_path: Path, manager
) -> None:
    root = tmp_path / "runtime-state"
    root.mkdir(exist_ok=True)
    manager.connect_provider("zai-coding-plan")
    service = QuotaRefreshService(
        runtime_state_root=root,
        connected_provider_ids=manager.connected_provider_ids,
        environ={"ZAI_API_KEY": "value-never-persisted"},
        auth_store_paths=(),
    )
    observation = service.observations()[0]
    assert observation.collector_available is True

    without = QuotaRefreshService(
        runtime_state_root=root,
        connected_provider_ids=manager.connected_provider_ids,
        environ={},
        auth_store_paths=(),
    )
    assert without.observations()[0].collector_available is False


def test_quota_state_files_never_contain_credentials(tmp_path: Path, manager) -> None:
    transport = _RecordingTransport(
        {
            "data": {
                "limits": [
                    {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 55.0}
                ]
            }
        }
    )
    collector = ZAIQuotaCollector(
        authorization_token="super-secret-token", transport=transport
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": collector})
    service.connect_provider({"provider_id": "zai-coding-plan"})
    service.refresh_quota()

    quota_root = tmp_path / "runtime-state" / "quota"
    written = list(quota_root.rglob("*.json"))
    assert written, "a successful refresh must persist a snapshot"
    for path in written:
        raw = path.read_text(encoding="utf-8")
        assert "super-secret-token" not in raw
        payload = json.loads(raw)
        assert "authorization" not in json.dumps(payload).lower()


# ---------------------------------------------------------------------------
# P4.2.6.5.1 §17/§25 — observability count, and scoped acceptance
# ---------------------------------------------------------------------------


def test_minimax_general_quota_makes_two_providers_observable(
    tmp_path: Path, manager
) -> None:
    """§17 — video ambiguity must not hold MiniMax in UNKNOWN.

    Both providers report a real coding figure, so both are observable. Before
    the workload-scope projection the MiniMax card sat at UNKNOWN because its
    ``video`` scope disagreed with ``general``, and the owner saw
    "observable: 1" while holding two readable balances.
    """

    minimax = MiniMaxQuotaCollector(
        bearer_token="t",
        region="cn",
        transport=_RecordingTransport(
            {
                "model_remains": [
                    {
                        "model": "general",
                        "current_interval_remaining_percent": 95,
                        "current_weekly_remaining_percent": 59,
                    },
                    {
                        "model": "video",
                        "current_interval_remaining_percent": 60,
                        "current_weekly_remaining_percent": 60,
                    },
                ]
            }
        ),
    )
    glm = ZAIQuotaCollector(
        authorization_token="t",
        transport=_RecordingTransport(
            {
                "data": {
                    "limits": [
                        {
                            "type": "CREDIT_LIMIT",
                            "unit": 3,
                            "number": 5,
                            "percentage": 30.0,
                        }
                    ]
                }
            }
        ),
    )
    service = _service(
        tmp_path,
        manager,
        collectors={"minimax-cn-coding-plan": minimax, "zai-coding-plan": glm},
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    service.connect_provider({"provider_id": "zai-coding-plan"})

    view = service.refresh_quota().overview

    assert view.summary.connected_provider_count == 2
    assert view.summary.quota_observable_provider_count == 2
    cards = {card.provider_id: card for card in view.providers}
    minimax_plan = cards["minimax-cn-coding-plan"].plan
    assert minimax_plan is not None
    assert minimax_plan.active_workload_scope == "CODING_TEXT"
    assert minimax_plan.workload_scope_notes == ("VIDEO_SCOPE_IGNORED_FOR_CODING",)
    windows = {window.window_id: window for window in minimax_plan.windows}
    assert windows["5h"].remaining_fraction == pytest.approx(0.95)
    assert windows["weekly"].remaining_fraction == pytest.approx(0.59)
    # ...and the video observation reaches the client, classified as such.
    video = [
        item
        for item in minimax_plan.model_equivalents
        if item.workload_scope == "VIDEO_GENERATION"
    ]
    assert {item.scope_id for item in video} == {"video"}


def test_glm_semantics_are_untouched_by_the_minimax_projection(
    tmp_path: Path, manager
) -> None:
    """§10 — GLM keeps its own provider-authoritative windows and product name."""

    glm = ZAIQuotaCollector(
        authorization_token="t",
        transport=_RecordingTransport(
            {
                "data": {
                    "limits": [
                        {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 30.0}
                    ]
                }
            }
        ),
    )
    service = _service(tmp_path, manager, collectors={"zai-coding-plan": glm})
    service.connect_provider({"provider_id": "zai-coding-plan"})

    plan = service.refresh_quota().overview.providers[0].plan
    assert plan is not None
    assert plan.display_name == "GLM Coding Plan"
    # GLM reports no workload scopes; nothing about MiniMax's projection may
    # invent one for it, and no GLM window may be dropped by it.
    assert plan.active_workload_scope == "UNKNOWN"
    assert plan.workload_scope_notes == ()
    assert plan.model_equivalents == ()


def test_scoped_acceptance_refresh_fails_closed_without_a_provider(
    tmp_path: Path, manager
) -> None:
    """§25 — a scoped refresh with no target must stop, never widen.

    The previous acceptance run omitted ``provider_id`` and refreshed every
    connected provider, including one pending credential rotation. The scoped
    helper cannot do that: it refuses instead.
    """

    minimax_transport = _RecordingTransport(
        {"model_remains": [{"model": "general", "current_interval_remaining_percent": 95}]}
    )
    minimax = MiniMaxQuotaCollector(
        bearer_token="t", region="cn", transport=minimax_transport
    )
    glm_transport = _RecordingTransport({"data": {"limits": []}})
    glm = ZAIQuotaCollector(authorization_token="t", transport=glm_transport)
    service = _service(
        tmp_path,
        manager,
        collectors={"minimax-cn-coding-plan": minimax, "zai-coding-plan": glm},
    )
    service.connect_provider({"provider_id": "minimax-cn-coding-plan"})
    service.connect_provider({"provider_id": "zai-coding-plan"})

    for missing in (None, "", "   "):
        with pytest.raises(ScopedRefreshTargetMissing):
            scoped_quota_refresh(service, provider_id=missing)
    # No provider was contacted: refusing is not a silent all-provider refresh.
    assert glm_transport.calls == []
    assert minimax_transport.calls == []

    result = scoped_quota_refresh(service, provider_id="zai-coding-plan")
    assert result.refreshed_provider_ids == ("zai-coding-plan",)
    assert glm_transport.calls != []
    # The provider that was not named stays untouched — this is the exact
    # widening that reached a credential pending rotation.
    assert minimax_transport.calls == []


def test_scoped_refresh_rejects_a_result_that_touched_other_providers(
    tmp_path: Path, manager
) -> None:
    """The widening this guard exists for is caught, not assumed impossible."""

    class _WideningClient:
        def refresh_quota(self, provider_id: str | None = None):
            return QuotaRefreshResultView(
                refreshed_provider_ids=("zai-coding-plan", "minimax-cn-coding-plan"),
                overview=_service(tmp_path, manager).quota(),
            )

    with pytest.raises(ScopedRefreshWidened):
        scoped_quota_refresh(_WideningClient(), provider_id="zai-coding-plan")


def test_the_all_provider_refresh_must_be_named_to_be_invoked(
    tmp_path: Path, manager
) -> None:
    """The product-wide operation still exists — as a deliberate call."""

    glm_transport = _RecordingTransport({"data": {"limits": []}})
    service = _service(
        tmp_path,
        manager,
        collectors={
            "zai-coding-plan": ZAIQuotaCollector(
                authorization_token="t", transport=glm_transport
            )
        },
    )
    service.connect_provider({"provider_id": "zai-coding-plan"})

    result = refresh_all_provider_quota(service)
    assert result.refreshed_provider_ids == ("zai-coding-plan",)
