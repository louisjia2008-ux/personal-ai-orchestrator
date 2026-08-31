import Foundation

import XCTest

@testable import PAOControlKit

/// P4.2.3 interactive-dashboard regressions: navigation filter semantics,
/// section completeness, refresh coalescing, auto-start gating, and quota
/// explainability localization.
@MainActor
final class InteractiveDashboardTests: XCTestCase {
    func testDashboardSectionsAreCompleteAndStable() {
        XCTAssertEqual(DashboardSection.allCases.map(\.rawValue), [
            "overview", "tasks", "agents", "providers", "quota",
            "routing", "verification", "history", "settings",
        ])
        for section in DashboardSection.allCases {
            XCTAssertEqual(section.id, section.rawValue)
            XCTAssertFalse(section.title.isEmpty, "section title must localize: \(section.rawValue)")
        }
    }

    func testMetricFilterMatchesCompositeDashboardCounters() {
        // Empty filter shows everything.
        for state in ["RUNNING", "READY", "SUBMITTED", "BLOCKED", "VERIFIED", "COMPLETED"] {
            XCTAssertTrue(MetricsFilter.matches(state: state, filter: ""))
        }
        // Exact states match themselves.
        XCTAssertTrue(MetricsFilter.matches(state: "RUNNING", filter: "RUNNING"))
        XCTAssertTrue(MetricsFilter.matches(state: "BLOCKED", filter: "BLOCKED"))
        // READY counter includes SUBMITTED tasks, mirroring taskCounts().
        XCTAssertTrue(MetricsFilter.matches(state: "READY", filter: "READY"))
        XCTAssertTrue(MetricsFilter.matches(state: "SUBMITTED", filter: "READY"))
        XCTAssertFalse(MetricsFilter.matches(state: "RUNNING", filter: "READY"))
        // VERIFIED counter includes COMPLETED tasks.
        XCTAssertTrue(MetricsFilter.matches(state: "VERIFIED", filter: "VERIFIED"))
        XCTAssertTrue(MetricsFilter.matches(state: "COMPLETED", filter: "VERIFIED"))
        XCTAssertFalse(MetricsFilter.matches(state: "READY", filter: "VERIFIED"))
        // Unknown states never sneak through a foreign filter.
        XCTAssertFalse(MetricsFilter.matches(state: "WEIRD", filter: "RUNNING"))
    }

    func testAutoStartDaemonFalseSkipsDaemonLaunch() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("autostart-off")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        // autoStartDaemon=false must not request a bundled-helper launch even
        // though the store keeps refreshing through the socket.
        let store = OrchestratorStore(
            socketPath: path,
            autoStartDaemon: false,
            idFactory: { "fixed" }
        )
        await store.refreshNow()
        XCTAssertEqual(store.daemonLifecycle.status, .unknown)
        // No lifecycle start task was scheduled, so no helper launch attempt
        // occurred; the connection still works through the preexisting daemon.
        XCTAssertEqual(store.connection, .connected)
    }

    func testRefreshNowCoalescesAndResetsRefreshingFlag() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("refresh-coalesce")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()
        XCTAssertFalse(store.isRefreshing, "refresh flag must reset after completion")

        // Two concurrent user-triggered refreshes: the guard must serialize or
        // coalesce them without leaving the flag stuck or crashing.
        async let first: Void = store.refreshNow()
        async let second: Void = store.refreshNow()
        _ = await (first, second)
        XCTAssertFalse(store.isRefreshing)
        XCTAssertEqual(store.connection, .connected)
    }

    func testQuotaExplanationsLocalizeAndNeverFabricatePercentages() {
        for language in ["en", "zh-Hans"] {
            XCTAssertNotNil(L10n.catalogString(key: "quota.explain.exact", language: language))
            XCTAssertNotNil(L10n.catalogString(key: "quota.explain.estimated", language: language))
            XCTAssertNotNil(L10n.catalogString(key: "quota.explain.unknown", language: language))
            XCTAssertNotNil(L10n.catalogString(key: "quota.source.providerExact", language: language))
            XCTAssertNotNil(L10n.catalogString(key: "quota.source.locallyMeasured", language: language))
            XCTAssertNotNil(L10n.catalogString(key: "quota.source.locallyInferred", language: language))
        }
        // Unknown confidence/source/state fall back to UNKNOWN semantics, never
        // to a numeric fabrication.
        let explain = L10n.quotaConfidenceExplanation("SOMETHING_NEW")
        XCTAssertEqual(explain, L10n.catalogString(key: "quota.explain.unknown", language: L10n.resolvedLanguageCode))
        let source = L10n.quotaSourceExplanation("SOMETHING_NEW")
        XCTAssertEqual(source, L10n.catalogString(key: "quota.source.unknown", language: L10n.resolvedLanguageCode))
        // UNKNOWN quota windows render no percentage through quotaRemaining,
        // regardless of whether a numeric fraction happens to be present.
        let withFraction = L10n.quotaRemaining(fraction: 0.42, confidence: "UNKNOWN")
        let withoutFraction = L10n.quotaRemaining(fraction: nil, confidence: "UNKNOWN")
        XCTAssertFalse(withFraction.contains("%"))
        XCTAssertFalse(withoutFraction.contains("%"))
        XCTAssertFalse(withFraction.contains("42"))
    }

    func testInteractiveDashboardKeysExistInBothLanguages() {
        for key in L10n.requiredKeys {
            XCTAssertNotNil(L10n.catalogString(key: key, language: "en"), "missing en: \(key)")
            XCTAssertNotNil(L10n.catalogString(key: key, language: "zh-Hans"), "missing zh-Hans: \(key)")
        }
    }
}
