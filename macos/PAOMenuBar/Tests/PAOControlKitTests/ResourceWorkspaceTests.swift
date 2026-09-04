import Foundation

import XCTest

@testable import PAOControlKit

/// B4: the resource collection and the states it must keep distinct.
///
/// The join is the risk here. Three endpoints describe the same provider and
/// disagree about it in legitimate ways, so most of these check that a provider
/// survives a disagreement rather than falling between two answers.
final class ResourceWorkspaceTests: XCTestCase {

    // MARK: - Fixtures

    private func health(
        _ providerId: String,
        name: String = "Discovered",
        targets: [ExecutionTargetHealthView] = [],
        pools: [QuotaPoolHealthView] = []
    ) -> ProviderHealthView {
        ProviderHealthView(
            providerId: providerId,
            displayName: name,
            accountCount: 1,
            quotaPools: pools,
            executionTargets: targets
        )
    }

    private func connection(_ providerId: String, name: String = "Connected")
        -> ProviderConnectionView
    {
        decode(
            """
            {
              "provider_id": "\(providerId)", "display_name": "\(name)",
              "connection_state": "CONNECTED", "auth_state": "AUTHENTICATED",
              "execution_verified": true, "runtime_state": "AVAILABLE",
              "credential_reference_type": "KEYCHAIN", "region": null,
              "plan_surface": null, "model_skus": ["m-1"],
              "connected_at": null, "last_validated_at": null, "last_reason_code": null
            }
            """
        )
    }

    private func available(_ providerId: String, name: String = "Available")
        -> AvailableProviderView
    {
        decode(
            """
            {
              "provider_id": "\(providerId)", "display_name": "\(name)",
              "connection_state": "NOT_CONNECTED", "auth_state": "AUTH_REQUIRED",
              "execution_verified": false, "runtime_state": "UNKNOWN",
              "region": null, "plan_surface": null, "model_skus": [],
              "last_checked": null
            }
            """
        )
    }

    private func card(
        _ providerId: String,
        name: String = "Quota",
        quotaState: String = "OBSERVED",
        confidence: String = "EXACT",
        observedAt: String? = "2026-09-03T10:00:00Z",
        plan: QuotaPlanView? = nil,
        pools: [QuotaPoolHealthView] = []
    ) -> QuotaProviderCardView {
        QuotaProviderCardView(
            providerId: providerId,
            displayName: name,
            connectionState: "CONNECTED",
            quotaState: quotaState,
            confidence: confidence,
            observedAt: observedAt,
            quotaPools: pools,
            plan: plan
        )
    }

    private func window(
        _ windowId: String,
        kind: String = "5h",
        state: String = "AVAILABLE",
        confidence: String = "EXACT",
        remaining: Double? = 0.6,
        resetAt: String? = "2026-09-03T15:00:00Z"
    ) -> QuotaPlanWindowView {
        QuotaPlanWindowView(
            windowId: windowId,
            windowKind: kind,
            state: state,
            confidence: confidence,
            remainingFraction: remaining,
            resetAt: resetAt
        )
    }

    private func plan(
        _ providerId: String,
        poolId: String = "pool-a",
        windows: [QuotaPlanWindowView],
        limiting: String? = nil
    ) -> QuotaPlanView {
        QuotaPlanView(
            providerId: providerId,
            planId: "plan-1",
            displayName: "GLM Coding Plan",
            poolId: poolId,
            windows: windows,
            bindingWindow: limiting.map { BindingWindowView(windowId: $0) }
        )
    }

    private func target(
        _ id: String, verified: Bool = true, enabled: Bool = true
    ) -> ExecutionTargetHealthView {
        decode(
            """
            {
              "execution_target_id": "\(id)", "model_sku_id": "sku-\(id)",
              "runtime_id": "runtime-\(id)", "enabled": \(enabled),
              "execution_verified": \(verified), "runtime_available": true,
              "observed_availability": null
            }
            """
        )
    }

    private func decode<T: Decodable>(_ json: String) -> T {
        // swiftlint:disable:next force_try
        try! JSONDecoder().decode(T.self, from: Data(json.utf8))
    }

    private func overview(_ cards: [QuotaProviderCardView]) -> QuotaOverviewView {
        QuotaOverviewView(
            state: "CONNECTED_WITH_QUOTA_OBSERVATIONS",
            summary: QuotaSummaryView(
                connectedProviderCount: cards.count,
                quotaObservableProviderCount: cards.filter(\.isObserved).count,
                quotaUnknownProviderCount: 0,
                quotaWarningCount: 0,
                quotaExhaustedCount: 0
            ),
            providers: cards,
            history: QuotaHistoryView(observations: [], retentionLimit: 500)
        )
    }

    private func connections(
        connected: [ProviderConnectionView] = [], available: [AvailableProviderView] = []
    ) -> ProviderConnectionListView {
        let encoder = JSONEncoder()
        // swiftlint:disable force_try
        let connectedJSON = String(data: try! encoder.encode(connected), encoding: .utf8)!
        let availableJSON = String(data: try! encoder.encode(available), encoding: .utf8)!
        // swiftlint:enable force_try
        return decode(
            """
            {
              "connected": \(connectedJSON),
              "available_to_add": \(availableJSON),
              "import_candidates": []
            }
            """
        )
    }

    // MARK: - Joining the three surfaces

    func testAProviderKnownToAllThreeEndpointsAppearsOnce() {
        let snapshots = ResourceCollection.snapshots(
            providers: ProviderHealthListView(providers: [health("glm")]),
            connections: connections(connected: [connection("glm", name: "GLM")]),
            quota: overview([card("glm")])
        )
        XCTAssertEqual(snapshots.count, 1)
        XCTAssertEqual(snapshots[0].providerId, "glm")
        XCTAssertNotNil(snapshots[0].health)
        XCTAssertNotNil(snapshots[0].connection)
        XCTAssertNotNil(snapshots[0].quota)
    }

    func testAProviderOnlyDiscoveryKnowsAboutStaysVisible() {
        // The union, not the intersection: what the owner has must not depend
        // on which endpoint happened to answer.
        let snapshots = ResourceCollection.snapshots(
            providers: ProviderHealthListView(providers: [health("orphan")]),
            connections: connections(),
            quota: nil
        )
        XCTAssertEqual(snapshots.count, 1)
        XCTAssertEqual(snapshots[0].kind, .discovered)
    }

    func testAProviderOnlyTheQuotaCardKnowsAboutStaysVisible() {
        let snapshots = ResourceCollection.snapshots(
            providers: nil, connections: connections(), quota: overview([card("ghost")])
        )
        XCTAssertEqual(snapshots.count, 1)
        XCTAssertEqual(snapshots[0].providerId, "ghost")
    }

    func testTheRegistryNamesTheResourceAndDiscoveryIsTheFallback() {
        let snapshots = ResourceCollection.snapshots(
            providers: ProviderHealthListView(providers: [health("glm", name: "catalog name")]),
            connections: connections(connected: [connection("glm", name: "GLM Coding Plan")]),
            quota: nil
        )
        XCTAssertEqual(snapshots[0].displayName, "GLM Coding Plan")
    }

    func testAResourceIsNeverDisplayedNameless() {
        // Falls all the way back to the id rather than rendering a blank row.
        let snapshots = ResourceCollection.snapshots(
            providers: nil, connections: connections(), quota: overview([card("bare", name: "")])
        )
        XCTAssertFalse(snapshots[0].displayName.isEmpty)
    }

    func testKindReflectsWhatTheOwnerDidNotWhatDiscoveryFound() {
        let snapshots = ResourceCollection.snapshots(
            providers: ProviderHealthListView(providers: [health("a"), health("b"), health("c")]),
            connections: connections(
                connected: [connection("a")], available: [available("b")]
            ),
            quota: nil
        )
        let kinds = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.providerId, $0.kind) })
        XCTAssertEqual(kinds["a"], .connected)
        XCTAssertEqual(kinds["b"], .available)
        XCTAssertEqual(kinds["c"], .discovered)
    }

    // MARK: - Quota bindings

    func testAProviderWithQuotaExposesEachWindowAsItsOwnBinding() {
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [window("5h"), window("monthly", kind: "monthly", remaining: 0.9)]))
            ])
        )[0]
        XCTAssertEqual(resource.quotaBindings.count, 2)
        XCTAssertEqual(Set(resource.quotaBindings.map(\.series.windowId)), ["5h", "monthly"])
    }

    func testMultipleReadableBindingsAreNeverAveragedIntoOneFigure() {
        // The row falls back to a count. A 5-hour window at 12% averaged with a
        // monthly at 90% describes no window that exists.
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [
                    window("5h", remaining: 0.12),
                    window("monthly", kind: "monthly", remaining: 0.9),
                ]))
            ])
        )[0]
        XCTAssertNil(resource.summaryBinding, "no window may stand for the resource")
        XCTAssertEqual(resource.readableBindingCount, 2)
    }

    func testTheDaemonsLimitingWindowIsTheOneThatMayStandForTheResource() {
        // The client never elects a limiting window itself; it uses the one the
        // daemon declared.
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [
                    window("5h", remaining: 0.12),
                    window("monthly", kind: "monthly", remaining: 0.9),
                ], limiting: "5h"))
            ])
        )[0]
        XCTAssertEqual(resource.summaryBinding?.series.windowId, "5h")
        XCTAssertEqual(resource.summaryBinding?.isLimiting, true)
    }

    func testALoneReadableBindingMayStandForTheResource() {
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([card("glm", plan: plan("glm", windows: [window("5h")]))])
        )[0]
        XCTAssertEqual(resource.summaryBinding?.remainingFraction, 0.6)
    }

    func testAnUnknownWindowCarriesNoPercentageAndCannotFillAMeter() {
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [
                    window("5h", confidence: "UNKNOWN", remaining: nil)
                ]))
            ])
        )[0]
        let binding = resource.quotaBindings[0]
        XCTAssertFalse(binding.isReadable)
        XCTAssertNil(binding.remainingFraction)
        XCTAssertNil(resource.summaryBinding)
    }

    func testAConfidentlyUnknownWindowIsNotReadableEvenWithAFraction() {
        // UNKNOWN confidence disqualifies the figure regardless of what came
        // with it: the daemon is saying it does not stand behind the number.
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [
                    window("5h", confidence: "UNKNOWN", remaining: 0.5)
                ]))
            ])
        )[0]
        XCTAssertFalse(resource.quotaBindings[0].isReadable)
    }

    func testExhaustedIsCriticalAndUnknownIsNeitherHealthyNorNeutral() {
        XCTAssertEqual(StatusStyle.quotaState("EXHAUSTED_OBSERVED").tone, .critical)
        XCTAssertEqual(StatusStyle.quotaState("LIMITED").tone, .caution)
        XCTAssertEqual(StatusStyle.quotaState("AVAILABLE").tone, .positive)
        let unknown = StatusStyle.quotaState("SOMETHING_NEW")
        XCTAssertEqual(unknown.tone, .unknown)
        XCTAssertNotEqual(unknown.tone, .positive)
        XCTAssertNotEqual(unknown.tone, .neutral)
    }

    func testPlanAndDiscoveryPoolsAreNeverMergedAndNeverCountedTwice() {
        // The two sources can carry the same pool id, or the discovery pool can
        // be identified by the provider id because the collector reported none.
        // The client cannot tell those apart, so it merges neither.
        let pool = QuotaPoolHealthView(
            quotaPoolId: "pool-a",
            name: "Discovery pool",
            planId: "plan-1",
            state: "AVAILABLE",
            confidence: "EXACT",
            measurementSourceType: "PROVIDER_API",
            observedAt: nil,
            windows: [
                QuotaWindowHealthView(
                    windowId: "5h", windowKind: "5h", state: "AVAILABLE",
                    confidence: "EXACT", remainingFraction: 0.4, resetAt: nil
                )
            ]
        )
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [window("5h")]), pools: [pool])
            ])
        )[0]
        // One binding: the plan's, which arrived first. The identical pool and
        // window from discovery is not appended as a second copy.
        XCTAssertEqual(resource.quotaBindings.count, 1)
        XCTAssertEqual(resource.quotaBindings[0].remainingFraction, 0.6)
    }

    func testPlanWindowsAndPoolTwinsRenderOnceEvenWhenThePlanNamesAFamily() {
        // The live daemon keys a card on the surface id ("minimax-cn") while
        // the plan payload names the provider family ("minimax"). Identity
        // follows the card — the daemon's own history key — so the same pool
        // and window reported through both payloads is one binding, not two
        // identical FIVE_HOUR and WEEKLY cards.
        let pool = QuotaPoolHealthView(
            quotaPoolId: "minimax-token-plan-cn",
            name: "minimax-token-plan-cn",
            planId: "token-plan",
            state: "AVAILABLE",
            confidence: "EXACT",
            measurementSourceType: "PROVIDER_API",
            observedAt: nil,
            windows: [
                QuotaWindowHealthView(
                    windowId: "5h", windowKind: "FIVE_HOUR", state: "AVAILABLE",
                    confidence: "EXACT", remainingFraction: 0.97, resetAt: nil
                ),
                QuotaWindowHealthView(
                    windowId: "weekly", windowKind: "WEEKLY", state: "AVAILABLE",
                    confidence: "EXACT", remainingFraction: 0.49, resetAt: nil
                )
            ]
        )
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("minimax-cn")]),
            quota: overview([
                card(
                    "minimax-cn",
                    plan: plan(
                        "minimax",
                        poolId: "minimax-token-plan-cn",
                        windows: [
                            window("5h", kind: "FIVE_HOUR", remaining: 0.97, resetAt: nil),
                            window("weekly", kind: "WEEKLY", remaining: 0.49, resetAt: nil)
                        ],
                        limiting: "weekly"
                    ),
                    pools: [pool]
                )
            ])
        )[0]
        XCTAssertEqual(resource.quotaBindings.count, 2)
        XCTAssertEqual(Set(resource.quotaBindings.map(\.windowKind)), ["FIVE_HOUR", "WEEKLY"])
        // The plan's copy wins and carries the daemon's limiting verdict.
        let weekly = resource.quotaBindings.first { $0.windowKind == "WEEKLY" }
        XCTAssertEqual(weekly?.isLimiting, true)
        XCTAssertEqual(weekly?.remainingFraction, 0.49)
    }

    // MARK: - Providers without quota

    func testAProviderWithoutQuotaTelemetryIsStillAResource() {
        // Being unobservable is a fact about the reading, not about whether the
        // thing exists. Disappearing would be the worse answer.
        let resource = ResourceCollection.snapshots(
            providers: ProviderHealthListView(providers: [health("local", targets: [target("t1")])]),
            connections: connections(connected: [connection("local")]),
            quota: nil
        )[0]
        XCTAssertTrue(resource.quotaBindings.isEmpty)
        XCTAssertEqual(resource.quotaState, "UNKNOWN")
        XCTAssertFalse(resource.isQuotaObserved)
        XCTAssertEqual(resource.executionTargets.count, 1)
    }

    func testNoQuotaCardIsUnknownRatherThanHealthy() {
        let resource = ResourceCollection.snapshots(
            providers: nil, connections: connections(connected: [connection("x")]), quota: nil
        )[0]
        XCTAssertEqual(resource.quotaState, "UNKNOWN")
        XCTAssertNil(resource.quotaObservedAt, "freshness is not invented")
    }

    func testAnUnobservedQuotaCardReportsUnknownNotAnEmptyReading() {
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("x")]),
            quota: overview([
                card("x", quotaState: "UNKNOWN", confidence: "UNKNOWN", observedAt: nil)
            ])
        )[0]
        XCTAssertFalse(resource.isQuotaObserved)
        XCTAssertTrue(resource.quotaBindings.isEmpty)
    }

    // MARK: - Execution targets

    func testOnlyEnabledAndVerifiedTargetsCountAsRunnable() {
        // Either alone is not a runnable target; reporting one as the other
        // would offer the owner a target the scheduler would refuse.
        let resource = ResourceCollection.snapshots(
            providers: ProviderHealthListView(
                providers: [
                    health("p", targets: [
                        target("a", verified: true, enabled: true),
                        target("b", verified: false, enabled: true),
                        target("c", verified: true, enabled: false),
                    ])
                ]
            ),
            connections: connections(), quota: nil
        )[0]
        XCTAssertEqual(resource.executionTargets.count, 3)
        XCTAssertEqual(resource.verifiedExecutionTargetCount, 1)
    }

    func testCatalogModelsAreNeverPromotedIntoExecutionTargets() {
        // The registry lists model SKUs; a listed model is a catalog fact, not a
        // verified runnable target.
        let resource = ResourceCollection.snapshots(
            providers: nil, connections: connections(connected: [connection("p")]), quota: nil
        )[0]
        XCTAssertEqual(resource.modelSkus, ["m-1"])
        XCTAssertTrue(resource.executionTargets.isEmpty)
    }

    // MARK: - Collection states

    func testDisconnectedIsUnknownRatherThanEmpty() {
        let state = ResourceCollection.resolve(
            providers: nil, connections: nil, quota: nil,
            connection: .disconnected(reason: .daemonNotRunning)
        )
        XCTAssertEqual(state, .disconnected(reason: .daemonNotRunning))
        XCTAssertNotEqual(state, .empty)
    }

    func testLoadingIsDistinctFromEmpty() {
        // Nothing has answered yet. An empty state would claim the daemon holds
        // no providers before it was asked.
        let loading = ResourceCollection.resolve(
            providers: nil, connections: nil, quota: nil, connection: .connected
        )
        XCTAssertEqual(loading, .loading)

        let empty = ResourceCollection.resolve(
            providers: ProviderHealthListView(providers: []),
            connections: nil, quota: nil, connection: .connected
        )
        XCTAssertEqual(empty, .empty)
    }

    func testSearchMatchesIdentityAndTheMachineValuesAnOwnerMightPaste() {
        let inputs = (
            providers: ProviderHealthListView(providers: []),
            connections: connections(connected: [connection("glm-coding", name: "GLM Coding Plan")])
        )
        for needle in ["GLM", "glm-coding", "CONNECTED", "m-1"] {
            let state = ResourceCollection.resolve(
                providers: inputs.providers, connections: inputs.connections,
                quota: nil, connection: .connected, query: needle
            )
            XCTAssertTrue(state.isPopulated, "\(needle) matched nothing")
        }
        let miss = ResourceCollection.resolve(
            providers: inputs.providers, connections: inputs.connections,
            quota: nil, connection: .connected, query: "nothing-like-this"
        )
        XCTAssertEqual(miss, .noSearchMatch)
    }

    func testSearchIsCaseInsensitive() {
        let state = ResourceCollection.resolve(
            providers: nil,
            connections: connections(connected: [connection("glm", name: "GLM Coding Plan")]),
            quota: nil, connection: .connected, query: "coding"
        )
        XCTAssertTrue(state.isPopulated)
    }

    func testWhitespaceOnlySearchDoesNotNarrowTheCollection() {
        let state = ResourceCollection.resolve(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: nil, connection: .connected, query: "   "
        )
        XCTAssertEqual(state.resources.count, 1)
    }

    // MARK: - Unknown future values

    func testAnUnrecognizedConnectionStateIsUnknownRatherThanConnected() {
        let resource = ResourceCollection.snapshots(
            providers: nil, connections: connections(),
            quota: overview([
                QuotaProviderCardView(
                    providerId: "future", displayName: "Future",
                    connectionState: "SOME_NEW_STATE",
                    quotaState: "OBSERVED", confidence: "EXACT"
                )
            ])
        )[0]
        XCTAssertEqual(resource.connectionState, "SOME_NEW_STATE")
        XCTAssertEqual(resource.connectionStatus.tone, .unknown)
        XCTAssertNotEqual(resource.connectionStatus.tone, .positive)
    }

    func testAnUnrecognizedQuotaStateNeverRendersAsAvailable() {
        let resource = ResourceCollection.snapshots(
            providers: nil,
            connections: connections(connected: [connection("glm")]),
            quota: overview([
                card("glm", plan: plan("glm", windows: [window("5h", state: "SOME_NEW_STATE")]))
            ])
        )[0]
        XCTAssertEqual(resource.quotaBindings[0].state, "SOME_NEW_STATE")
        XCTAssertEqual(resource.quotaBindings[0].status.tone, .unknown)
    }

    // MARK: - Localization

    func testEveryResourceKindIsNamedInBothLanguages() {
        for kind in ResourceKind.allCases {
            let key = "resources.kind.\(kind.rawValue)"
            let en = L10n.catalogString(key: key, language: "en")
            let zh = L10n.catalogString(key: key, language: "zh-Hans")
            XCTAssertNotNil(en, "missing en for \(kind.rawValue)")
            XCTAssertNotNil(zh, "missing zh-Hans for \(kind.rawValue)")
            XCTAssertNotEqual(en, zh, "\(kind.rawValue) was not translated")
        }
    }

    func testForecastLanguageStaysUncertainInBothCatalogs() {
        // "Projected" must not become a statement of fact in translation. Both
        // catalogs are checked for their own hedging vocabulary.
        let english = L10n.catalogString(key: "quota.projection.footer", language: "en") ?? ""
        XCTAssertTrue(
            english.localizedCaseInsensitiveContains("projection")
                || english.localizedCaseInsensitiveContains("not a reading"),
            "English forecast copy lost its hedge"
        )
        let chinese = L10n.catalogString(key: "quota.projection.footer", language: "zh-Hans") ?? ""
        XCTAssertTrue(chinese.contains("预测"), "Chinese forecast copy lost its hedge")
        XCTAssertTrue(chinese.contains("不是读数"), "Chinese copy must deny being a reading")
    }

    func testTheScarcityGapIsStatedRatherThanLeftSilent() {
        // The daemon does not publish scarcity_class over the control API, so
        // none is shown — and the surface says so instead of leaving a hole
        // where a verdict would be.
        for language in ["en", "zh-Hans"] {
            let copy = L10n.catalogString(key: "quota.scarcity.unavailable", language: language)
            XCTAssertNotNil(copy, "missing \(language) scarcity explanation")
            XCTAssertFalse(copy?.isEmpty ?? true)
        }
    }

    func testMachineValuesStayUntranslated() {
        // Protocol enums the Resources surface renders verbatim.
        for value in ["OBSERVED", "AVAILABLE", "EXHAUSTED_OBSERVED", "CODING_TEXT", "KEYCHAIN"] {
            for language in ["en", "zh-Hans"] {
                XCTAssertNil(L10n.catalogString(key: value, language: language))
            }
        }
    }
}
