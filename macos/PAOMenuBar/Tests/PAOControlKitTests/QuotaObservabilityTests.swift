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

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
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
}
