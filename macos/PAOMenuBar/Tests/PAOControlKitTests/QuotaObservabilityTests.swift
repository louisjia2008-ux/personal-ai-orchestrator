import Foundation

import XCTest

@testable import PAOControlKit

/// P4.2.6.4 — the Quota page is connection-based.
///
/// Regression under test: the page used to build its provider list from
/// providers that already carried quota pools, so connecting a provider
/// produced an empty page. A connected provider must always render, and
/// "refresh quota" must stay a different operation from "refresh providers".
@MainActor
final class QuotaObservabilityTests: XCTestCase {

    // MARK: - §14 connected provider with zero quota pools

    func testConnectedProviderWithZeroQuotaPoolsStaysVisible() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("quota-unknown")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        let quota = try XCTUnwrap(store.quota)
        XCTAssertEqual(quota.pageState, .connectedButQuotaUnknown)
        XCTAssertEqual(quota.providers.count, 1)

        let card = try XCTUnwrap(quota.providers.first)
        XCTAssertEqual(card.providerId, "zai-coding-plan")
        XCTAssertEqual(card.connectionState, "CONNECTED")
        XCTAssertEqual(card.quotaState, "UNKNOWN")
        XCTAssertEqual(card.confidence, "UNKNOWN")
        XCTAssertTrue(card.quotaPools.isEmpty)
        XCTAssertFalse(card.isObserved)
        // The page must explain why, not just go blank.
        XCTAssertEqual(card.failureReason, "CREDENTIAL_NOT_AVAILABLE")
        XCTAssertFalse(L10n.quotaFailureReason(card.failureReason).isEmpty)
    }

    /// §15 — UNKNOWN counts as connected but not observable, never healthy.
    func testSummaryDoesNotCountUnknownAsObservable() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("quota-summary")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        let summary = try XCTUnwrap(store.quota?.summary)
        XCTAssertEqual(summary.connectedProviderCount, 1)
        XCTAssertEqual(summary.quotaObservableProviderCount, 0)
        XCTAssertEqual(summary.quotaUnknownProviderCount, 1)
    }

    // MARK: - §9 refresh quota is distinct from refresh providers

    func testRefreshQuotaCallsQuotaEndpointNotProviderDiscovery() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("quota-refresh")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshQuota()

        let posts = daemon.receivedRequests.filter { $0.method == "POST" }
        XCTAssertEqual(posts.map(\.path), ["/v1/quota/refresh"])
        XCTAssertFalse(
            posts.contains { $0.path == "/v1/providers/refresh" },
            "quota refresh must not run provider discovery"
        )
    }

    func testRefreshProvidersDoesNotPostToQuotaRefresh() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("provider-refresh")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshProviders()

        let posts = daemon.receivedRequests.filter { $0.method == "POST" }
        XCTAssertEqual(posts.map(\.path), ["/v1/providers/refresh"])
        XCTAssertFalse(posts.contains { $0.path.hasSuffix("/quota/refresh") })
    }

    func testProviderScopedQuotaRefreshTargetsThatProvider() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route(
            "POST",
            "/v1/providers/zai-coding-plan/quota/refresh",
            body: quotaRefreshBody
        )
        let path = temporarySocketPath("quota-scoped")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshQuota(providerId: "zai-coding-plan")

        XCTAssertEqual(
            daemon.receivedRequests.filter { $0.method == "POST" }.map(\.path),
            ["/v1/providers/zai-coding-plan/quota/refresh"]
        )
    }

    // MARK: - §11 connect makes the provider immediately quota-visible

    func testConnectingAProviderRefreshesTheQuotaProjection() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/provider-connections", status: 201, body: """
        {"provider_id":"zai-coding-plan","display_name":"GLM / Z.AI","connection_state":"CONNECTED","auth_state":"AUTH_UNKNOWN","execution_verified":false,"runtime_state":"UNKNOWN","credential_reference_type":"ENV_PRESENCE","region":null,"plan_surface":"Coding Plan","model_skus":[],"connected_at":"2026-08-31T00:00:00Z","last_validated_at":null,"last_reason_code":null}
        """)
        daemon.route("GET", "/v1/provider-connections", body: """
        {"connected":[],"available_to_add":[],"import_candidates":[]}
        """)
        let path = temporarySocketPath("quota-connect")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.connectProvider(providerId: "zai-coding-plan")

        // No app restart: the quota projection is re-read as part of connecting.
        XCTAssertTrue(daemon.receivedRequests.contains { $0.path == "/v1/quota" })
        XCTAssertNotNil(store.quota)
    }

    // MARK: - §13 UNKNOWN never fabricates a percentage

    func testUnknownWindowsCarryNoRemainingFraction() throws {
        let payload = Data("""
        {"state":"CONNECTED_BUT_QUOTA_UNKNOWN",
         "summary":{"connected_provider_count":1,"quota_observable_provider_count":0,"quota_unknown_provider_count":1,"quota_warning_count":0,"quota_exhausted_count":0},
         "providers":[{"provider_id":"p","display_name":"P","connection_state":"CONNECTED","auth_state":null,"plan_surface":null,"region":null,"quota_state":"UNKNOWN","confidence":"UNKNOWN","measurement_source":null,"observed_at":null,"readonly_source_available":false,"collector_available":false,"last_refresh_status":null,"last_refresh_at":null,"failure_reason":"NO_READONLY_QUOTA_SOURCE","quota_pools":[{"quota_pool_id":"pool","name":"pool","plan_id":"plan","state":"UNKNOWN","confidence":"UNKNOWN","measurement_source_type":"INFERRED","observed_at":null,"windows":[{"window_id":"5h","window_kind":"FIVE_HOUR","state":"UNKNOWN","confidence":"UNKNOWN","remaining_fraction":null,"reset_at":null}]}]}],
         "history":{"observations":[],"retention_limit":500}}
        """.utf8)
        let view = try JSONDecoder().decode(QuotaOverviewView.self, from: payload)
        let window = try XCTUnwrap(view.providers.first?.quotaPools.first?.windows.first)
        XCTAssertEqual(window.confidence, "UNKNOWN")
        XCTAssertNil(window.remainingFraction)
        // The rendering helper must refuse to print a percentage for UNKNOWN.
        let rendered = L10n.quotaRemaining(
            fraction: window.remainingFraction,
            confidence: window.confidence
        )
        XCTAssertFalse(rendered.contains("%"))
    }

    func testExactAndEstimatedRenderPercentagesAndEstimatedIsLabelled() throws {
        let exact = L10n.quotaRemaining(fraction: 0.42, confidence: "EXACT")
        XCTAssertTrue(exact.contains("%"))
        let estimated = L10n.quotaRemaining(fraction: 0.70, confidence: "ESTIMATED")
        XCTAssertTrue(estimated.contains("%"))
        // ESTIMATED must be visibly distinguishable from EXACT in the UI.
        XCTAssertFalse(L10n.quotaEstimatedBadge.isEmpty)
        XCTAssertNotEqual(L10n.quotaEstimatedBadge, "EXACT")
    }

    // MARK: - §19 a failed refresh must not blank the page

    func testFailedQuotaRefreshKeepsTheProjectionVisible() async throws {
        let daemon = TestDaemon()
        // Registered first: TestDaemon resolves the earliest matching route, so
        // this overrides the standard success response.
        daemon.route("POST", "/v1/quota/refresh", status: 503, body: """
        {"error":"control_plane_unavailable"}
        """)
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("quota-fail")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        // This test owns both refreshes. A background refresh can otherwise
        // clear lastError while the failed quota refresh awaits its fallback.
        let store = OrchestratorStore(
            socketPath: path, autoStartDaemon: false, autoRefresh: false,
            idFactory: { "fixed" }
        )
        await store.refreshNow()
        XCTAssertNotNil(store.quota)

        await store.refreshQuota()
        // The last projection survives, and the provider is still listed.
        let quota = try XCTUnwrap(store.quota)
        XCTAssertEqual(quota.providers.count, 1)
        XCTAssertNotNil(store.lastError)
    }

    // MARK: - Observed state renders real evidence

    func testObservedProviderExposesRealWindows() throws {
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self,
            from: Data(quotaObservedBody.utf8)
        )
        XCTAssertEqual(view.pageState, .connectedWithQuotaObservations)
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertTrue(card.isObserved)
        XCTAssertEqual(card.confidence, "EXACT")
        XCTAssertEqual(card.measurementSource, "PROVIDER_API")
        let window = try XCTUnwrap(card.quotaPools.first?.windows.first)
        XCTAssertEqual(window.remainingFraction, 0.42)
        XCTAssertEqual(window.resetAt, "2026-08-31T05:00:00Z")
    }

    /// A daemon reporting a state this build does not know must not collapse
    /// the page into "nothing connected".
    func testUnknownPageStateFallsBackWithoutHidingProviders() throws {
        let payload = Data("""
        {"state":"SOME_FUTURE_STATE",
         "summary":{"connected_provider_count":1,"quota_observable_provider_count":0,"quota_unknown_provider_count":1,"quota_warning_count":0,"quota_exhausted_count":0},
         "providers":[{"provider_id":"p","display_name":"P","connection_state":"CONNECTED","auth_state":null,"plan_surface":null,"region":null,"quota_state":"UNKNOWN","confidence":"UNKNOWN","measurement_source":null,"observed_at":null,"readonly_source_available":false,"collector_available":false,"last_refresh_status":null,"last_refresh_at":null,"failure_reason":null,"quota_pools":[]}],
         "history":{"observations":[],"retention_limit":500}}
        """.utf8)
        let view = try JSONDecoder().decode(QuotaOverviewView.self, from: payload)
        XCTAssertEqual(view.pageState, .connectedButQuotaUnknown)
        XCTAssertEqual(view.providers.count, 1)
    }

    // MARK: - §M1 WP1 burn sub-object + sourcePressure

    func testQuotaPlanWindowDecodesBurnSubObject() throws {
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(quotaObservedWithBurnBody.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        let plan = try XCTUnwrap(card.plan)
        let window = try XCTUnwrap(plan.windows.first)
        let burn = try XCTUnwrap(window.burn)
        XCTAssertEqual(burn.pressure, "ON_TRACK")
        XCTAssertFalse(burn.windowStartInferred)
        XCTAssertEqual(burn.expectedUsedFraction ?? 0, 0.5, accuracy: 1e-9)
        XCTAssertEqual(burn.actualUsedFraction ?? 0, 0.58, accuracy: 1e-9)
        XCTAssertEqual(burn.deviation ?? 0, 0.08, accuracy: 1e-9)
        XCTAssertEqual(burn.remainingFraction ?? 0, 0.42, accuracy: 1e-9)
        XCTAssertEqual(burn.secondsToReset ?? 0, 3600.0, accuracy: 1e-9)
        XCTAssertEqual(burn.pressureScore, 0.13, accuracy: 1e-9)
    }

    func testQuotaPlanWindowDecodesWithoutBurnSubObject() throws {
        // Pre-WP1 daemons do not emit ``burn`` — decode must succeed with
        // ``burn == nil`` so an upgrade does not blank the Quota page.
        // We hand-build a minimal plan-window payload here because the
        // shared canned bodies only exercise ``quota_pools`` (which is a
        // different view-model that does not carry burn at all).
        let payload = """
        {
          "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
          "summary": {"connected_provider_count": 1, "quota_observable_provider_count": 1, "quota_unknown_provider_count": 0, "quota_warning_count": 0, "quota_exhausted_count": 0},
          "providers": [{
            "provider_id": "p", "display_name": "P", "connection_state": "CONNECTED",
            "auth_state": null, "plan_surface": null, "region": null,
            "quota_state": "OBSERVED", "confidence": "EXACT",
            "measurement_source": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z",
            "readonly_source_available": true, "collector_available": true,
            "last_refresh_status": null, "last_refresh_at": null, "failure_reason": null,
            "credential_source": "ENV_VAR",
            "quota_pools": [],
            "plan": {
              "provider_id": "p", "plan_id": "plan", "display_name": "P",
              "quota_semantics": "SHARED_POOL",
              "pool_id": "pool", "resource_kind": "TOKEN_PLAN_INCLUDED_QUOTA",
              "shared_across_models": true, "unit_kind": "TOKENS",
              "covered_model_ids": [],
              "state": "AVAILABLE", "confidence": "EXACT",
              "observed_at": null, "unknown_reason": null,
              "active_workload_scope": "UNKNOWN", "workload_scope_notes": [],
              "binding_window": {"window_id": null, "window_kind": null, "remaining_fraction": null, "reset_at": null, "seconds_until_reset": null, "reason": "NO_KNOWN_REMAINING", "confidence": "UNKNOWN"},
              "model_consumption": [], "model_equivalents": [], "equivalent_capacity": [],
              "windows": [{"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE", "confidence": "EXACT", "remaining_fraction": 0.5, "reset_at": "2026-08-31T05:00:00Z"}]
            }
          }],
          "history": {"observations": [], "retention_limit": 500}
        }
        """
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(payload.utf8)
        )
        let plan = try XCTUnwrap(view.providers.first?.plan)
        let window = try XCTUnwrap(plan.windows.first)
        XCTAssertNil(window.burn)
    }

    func testQuotaProviderCardDecodesSourcePressure() throws {
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(quotaObservedWithBurnBody.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(card.sourcePressure, "ON_TRACK")
    }

    func testQuotaProviderCardDecodesWithoutSourcePressure() throws {
        // Pre-WP1 daemons omit ``source_pressure`` — decode must succeed
        // with ``sourcePressure == nil``.
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(quotaUnknownBody.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertNil(card.sourcePressure)
    }

    // M1 WP3 fix (F5): the per-provider ``collection_failure_streak``
    // rides on the card view so the owner can spot a host that has
    // been unable to probe the provider without drilling into a
    // target row. Pre-F5 daemons omit the field — decode must
    // succeed with ``collectionFailureStreak == 0``.
    func testQuotaProviderCardDecodesCollectionFailureStreak() throws {
        let body = """
        {
          "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
          "summary": {
            "connected_provider_count": 1,
            "quota_observable_provider_count": 1,
            "quota_unknown_provider_count": 0,
            "quota_warning_count": 0,
            "quota_exhausted_count": 0
          },
          "providers": [
            {
              "provider_id": "minimax-cn-coding-plan",
              "display_name": "MiniMax CN Coding Plan",
              "connection_state": "CONNECTED",
              "auth_state": "AUTH_FROM_ENV_PRESENCE",
              "quota_state": "OBSERVED",
              "confidence": "ESTIMATED",
              "readonly_source_available": true,
              "collector_available": true,
              "credential_source": "ENV",
              "quota_pools": [],
              "collection_failure_streak": 2
            }
          ],
          "history": {"observations": [], "retention_limit": 0}
        }
        """
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(body.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(card.collectionFailureStreak, 2)
    }

    func testQuotaProviderCardDecodesWithoutCollectionFailureStreak() throws {
        // Pre-F5 daemons do not surface the streak; ``decodeIfPresent``
        // must leave the field at its default 0.
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(quotaUnknownBody.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(card.collectionFailureStreak, 0)
    }

    // M1 WP4: pool_kind and unmetered block round-trip on the
    // provider card. Lenient decode keeps pre-WP4 daemons (which
    // omit the field) on the legacy ``windowed`` default.
    func testQuotaProviderCardDecodesPoolKindUnmetered() throws {
        let body = """
        {
          "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
          "summary": {
            "connected_provider_count": 1,
            "quota_observable_provider_count": 1,
            "quota_unknown_provider_count": 0,
            "quota_warning_count": 0,
            "quota_exhausted_count": 0
          },
          "providers": [
            {
              "provider_id": "opencode",
              "display_name": "OpenCode Free",
              "connection_state": "CONNECTED",
              "auth_state": "AUTH_FROM_ENV_PRESENCE",
              "quota_state": "OBSERVED",
              "confidence": "ESTIMATED",
              "readonly_source_available": true,
              "collector_available": true,
              "credential_source": "NONE",
              "quota_pools": [],
              "collection_failure_streak": 0,
              "pool_kind": "unmetered",
              "unmetered": {
                "rpm_observed": 12,
                "error_rate_1h": 0.05,
                "cooldown_until": null
              }
            }
          ],
          "history": {"observations": [], "retention_limit": 0}
        }
        """
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(body.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(card.poolKind, "unmetered")
        let unmetered = try XCTUnwrap(card.unmetered)
        XCTAssertEqual(unmetered.rpmObserved, 12)
        XCTAssertEqual(unmetered.errorRate1h, 0.05)
        XCTAssertNil(unmetered.cooldownUntil)
    }

    func testQuotaProviderCardDecodesWithoutPoolKindOrUnmetered() throws {
        // Pre-WP4 daemons omit pool_kind and unmetered. The lenient
        // defaults preserve the legacy chrome (windowed pool, no
        // unmetered block).
        let view = try JSONDecoder().decode(
            QuotaOverviewView.self, from: Data(quotaUnknownBody.utf8)
        )
        let card = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(card.poolKind, "windowed")
        XCTAssertNil(card.unmetered)
    }

    func testUnmeteredObservationViewErrorRateOneHourDecodesNull() throws {
        // V3: ``error_rate_1h`` is ``Double?`` on Swift — the
        // daemon emits ``null`` when no evidence rows exist in the
        // window. The Swift decoder must accept the null sentinel
        // and surface ``nil`` so the Resources page renders the
        // "no data" hint instead of a fabricated 0% error rate.
        let body = """
        {
          "rpm_observed": 0,
          "error_rate_1h": null,
          "cooldown_until": null
        }
        """
        let view = try JSONDecoder().decode(
            UnmeteredObservationView.self, from: Data(body.utf8)
        )
        XCTAssertNil(view.errorRate1h)
        XCTAssertEqual(view.rpmObserved, 0)
        XCTAssertNil(view.cooldownUntil)
    }

    func testStoreFetchesQuotaOverviewWithUnmeteredProvider() async throws {
        // V5: ``TestDaemon`` registers an unmetered canned body
        // for ``/v1/quota`` via ``registerStandardRoutes(quotaBody:)``
        // so the Swift store round-trip exercises the
        // ``pool_kind == "unmetered"`` branch with the same
        // fidelity as a real daemon.
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, quotaBody: quotaUnmeteredBody)
        let path = temporarySocketPath("quota-unmetered")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        let quota = try XCTUnwrap(store.quota)
        XCTAssertEqual(quota.pageState, .connectedWithQuotaObservations)
        let opencode = try XCTUnwrap(
            quota.providers.first { $0.providerId == "opencode" }
        )
        XCTAssertEqual(opencode.poolKind, "unmetered")
        let unmetered = try XCTUnwrap(opencode.unmetered)
        XCTAssertEqual(unmetered.rpmObserved, 12)
        XCTAssertEqual(unmetered.errorRate1h, 0.05)
        XCTAssertNil(unmetered.cooldownUntil)

        // The windowed provider in the same response still carries
        // the legacy chrome.
        let coding = try XCTUnwrap(
            quota.providers.first { $0.providerId == "minimax-cn-coding-plan" }
        )
        XCTAssertEqual(coding.poolKind, "windowed")
        XCTAssertNil(coding.unmetered)
    }
}
