import Foundation

import XCTest

@testable import PAOControlKit

/// B4: what makes two quota observations part of one measurable sequence.
///
/// These pin the defect Phase A found — one chart row built from every stored
/// observation regardless of provider, pool, window or reset period — and the
/// semantic that defect destroyed: a missing sample is *no observation*, not
/// *no usage*.
final class QuotaSeriesTests: XCTestCase {

    // MARK: - Fixtures

    private static let base = Date(timeIntervalSince1970: 1_760_000_000)

    private func iso(_ offsetSeconds: TimeInterval) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: Self.base.addingTimeInterval(offsetSeconds))
    }

    private func observation(
        provider: String = "glm",
        pool: String = "pool-a",
        window: String = "5h",
        at offsetSeconds: TimeInterval,
        remaining: Double? = 0.5,
        confidence: String = "EXACT",
        resetAt: TimeInterval? = 18_000,
        state: String = "AVAILABLE"
    ) -> QuotaObservationView {
        decode(
            """
            {
              "provider_id": "\(provider)",
              "quota_pool_id": "\(pool)",
              "window_id": "\(window)",
              "observed_at": "\(iso(offsetSeconds))",
              "remaining_fraction": \(remaining.map { "\($0)" } ?? "null"),
              "confidence": "\(confidence)",
              "measurement_source": "PROVIDER_API",
              "reset_at": \(resetAt.map { "\"\(iso($0))\"" } ?? "null"),
              "state": "\(state)"
            }
            """
        )
    }

    private func decode(_ json: String) -> QuotaObservationView {
        // swiftlint:disable:next force_try
        try! JSONDecoder().decode(QuotaObservationView.self, from: Data(json.utf8))
    }

    // MARK: - Series identity

    func testObservationsFromOneProviderAndWindowFormOneSeries() {
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.9),
            observation(at: 3_600, remaining: 0.7),
            observation(at: 7_200, remaining: 0.5),
        ])
        XCTAssertEqual(series.count, 1)
        XCTAssertEqual(series[0].segments.count, 1)
        XCTAssertEqual(series[0].segments[0].count, 3)
    }

    func testDifferentProvidersNeverJoinIntoOneSeries() {
        // The defect: GLM and MiniMax both report a window called "5h", and the
        // old chart drew them as one falling line.
        let series = QuotaHistory.series(from: [
            observation(provider: "glm", at: 0, remaining: 0.9),
            observation(provider: "minimax", at: 1_800, remaining: 0.2),
            observation(provider: "glm", at: 3_600, remaining: 0.8),
        ])
        XCTAssertEqual(series.count, 2)
        XCTAssertEqual(Set(series.map(\.identity.providerId)), ["glm", "minimax"])
        for one in series {
            XCTAssertTrue(one.allReadings.allSatisfy { _ in true })
        }
        let glm = series.first { $0.identity.providerId == "glm" }
        XCTAssertEqual(glm?.allReadings.count, 2)
    }

    func testDifferentQuotaBindingsNeverJoinIntoOneSeries() {
        let series = QuotaHistory.series(from: [
            observation(pool: "pool-a", at: 0, remaining: 0.9),
            observation(pool: "pool-b", at: 1_800, remaining: 0.1),
        ])
        XCTAssertEqual(series.count, 2)
        XCTAssertEqual(Set(series.map(\.identity.quotaPoolId)), ["pool-a", "pool-b"])
    }

    func testDifferentWindowsOfOnePoolNeverJoinIntoOneSeries() {
        // A 5-hour window at 12% and a monthly window at 90% are two facts.
        let series = QuotaHistory.series(from: [
            observation(window: "5h", at: 0, remaining: 0.12),
            observation(window: "monthly", at: 60, remaining: 0.9),
        ])
        XCTAssertEqual(series.count, 2)
        XCTAssertEqual(Set(series.map(\.identity.windowId)), ["5h", "monthly"])
    }

    func testSeriesOrderIsStableAcrossInputOrder() {
        // Series index drives colour and symbol; a shuffled daemon response must
        // not repaint the chart.
        let forward = QuotaHistory.series(from: [
            observation(provider: "aaa", at: 0),
            observation(provider: "zzz", at: 0),
        ])
        let reversed = QuotaHistory.series(from: [
            observation(provider: "zzz", at: 0),
            observation(provider: "aaa", at: 0),
        ])
        XCTAssertEqual(forward.map(\.identity), reversed.map(\.identity))
        XCTAssertEqual(forward.map(\.identity.providerId), ["aaa", "zzz"])
    }

    // MARK: - Reset windows

    func testDifferentResetWindowsNeverJoinIntoOneSegment() {
        // The point of segmentation: 20% then 95% is a replenishment, not a
        // recovery, and no trend may be drawn through the turn.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.6, resetAt: 18_000),
            observation(at: 3_600, remaining: 0.2, resetAt: 18_000),
            observation(at: 21_600, remaining: 0.95, resetAt: 39_600),
        ])
        XCTAssertEqual(series.count, 1)
        XCTAssertEqual(series[0].segments.count, 2)
        XCTAssertEqual(series[0].segments[0].count, 2)
        XCTAssertEqual(series[0].segments[1].count, 1)
    }

    func testAnElapsedResetStartsANewSegmentEvenWhenTheDaemonRepeatsTheResetTime() {
        // The reset instant passed between two readings. The daemon has not yet
        // observed the new window, but the old one is over regardless.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.4, resetAt: 3_600),
            observation(at: 7_200, remaining: 0.9, resetAt: 3_600),
        ])
        XCTAssertEqual(series[0].segments.count, 2)
    }

    func testKnownToUnknownResetStartsANewSegment() {
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.8, resetAt: 18_000),
            observation(at: 600, remaining: 0.7, resetAt: nil),
        ])
        XCTAssertEqual(series[0].segments.count, 2)
    }

    func testSegmentationNeverUsesTheQuotaValueItself() {
        // A drop and a rise inside one declared window stay in one segment: the
        // boundary rule reads reset identity and time, never the reading. If it
        // read the value, the burn rate computed over segments would be circular
        // with the segmentation that produced them.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.5, resetAt: 18_000),
            observation(at: 600, remaining: 0.9, resetAt: 18_000),
        ])
        XCTAssertEqual(series[0].segments.count, 1)
    }

    func testCurrentSegmentIsTheLatestResetWindow() {
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.3, resetAt: 3_600),
            observation(at: 7_200, remaining: 1.0, resetAt: 25_200),
            observation(at: 10_800, remaining: 0.8, resetAt: 25_200),
        ])
        let current = try? XCTUnwrap(series[0].currentSegment)
        XCTAssertEqual(current?.count, 2)
        XCTAssertEqual(current?.first.remainingFraction, 1.0)
    }

    // MARK: - Gaps

    func testAMissingSampleIsNotSynthesizedAsZeroUsage() {
        // The semantic distinction B4 exists to protect. Six hours with no
        // reading is six hours of no observation; nothing may be invented for
        // them, least of all a zero.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.9),
            observation(at: 21_600, remaining: 0.4),
        ])
        let readings = series[0].allReadings
        XCTAssertEqual(readings.count, 2, "no bucket may be synthesized for the gap")
        XCTAssertFalse(readings.contains { $0.remainingFraction == 0 })
    }

    func testRealTimestampsArePreservedRatherThanReindexed() {
        // The old chart drew evenly spaced bars, so a six-hour gap and a
        // one-minute gap looked identical.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.9),
            observation(at: 60, remaining: 0.85),
            observation(at: 21_600, remaining: 0.4),
        ])
        let times = series[0].allReadings.map(\.observedAt)
        XCTAssertEqual(times[1].timeIntervalSince(times[0]), 60)
        XCTAssertEqual(times[2].timeIntervalSince(times[1]), 21_540)
    }

    func testNoObservationIsNeverPromotedIntoAReading() {
        // A row the daemon stored with UNKNOWN confidence or no fraction is not
        // a zero-usage sample; it is not a sample.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: nil, confidence: "UNKNOWN"),
            observation(at: 600, remaining: nil, confidence: "EXACT"),
            observation(at: 1_200, remaining: 0.5),
        ])
        XCTAssertEqual(series.count, 1)
        XCTAssertEqual(series[0].allReadings.count, 1)
        XCTAssertEqual(series[0].allReadings[0].remainingFraction, 0.5)
    }

    func testOutOfRangeValuesAreDroppedRatherThanClamped() {
        // Clamping 1.4 to 1.0 would invent a reading the provider never gave.
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 1.4),
            observation(at: 600, remaining: -0.2),
            observation(at: 1_200, remaining: 0.5),
        ])
        XCTAssertEqual(series[0].allReadings.count, 1)
    }

    func testAnUnparseableTimestampDropsTheObservation() {
        let malformed = decode(
            """
            {
              "provider_id": "glm", "quota_pool_id": "p", "window_id": "5h",
              "observed_at": "not-a-timestamp", "remaining_fraction": 0.5,
              "confidence": "EXACT", "measurement_source": "PROVIDER_API",
              "reset_at": null, "state": "AVAILABLE"
            }
            """
        )
        XCTAssertTrue(QuotaHistory.series(from: [malformed]).isEmpty)
    }

    // MARK: - Normalization

    func testOutOfOrderInputNormalizesToAscendingTime() {
        let series = QuotaHistory.series(from: [
            observation(at: 7_200, remaining: 0.4),
            observation(at: 0, remaining: 0.9),
            observation(at: 3_600, remaining: 0.6),
        ])
        let readings = series[0].allReadings
        XCTAssertEqual(readings.map(\.remainingFraction), [0.9, 0.6, 0.4])
        XCTAssertTrue(zip(readings, readings.dropFirst()).allSatisfy { $0.observedAt < $1.observedAt })
    }

    func testDuplicateTimestampsCollapseDeterministicallyToTheLowerReading() {
        // The daemon stores a row per distinct value tuple, so one instant can
        // carry two disagreeing percentages. The lower one wins, in either input
        // order: a quota surface must not round in the owner's favour.
        let forward = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.8),
            observation(at: 0, remaining: 0.6),
        ])
        let reversed = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.6),
            observation(at: 0, remaining: 0.8),
        ])
        XCTAssertEqual(forward[0].allReadings.count, 1)
        XCTAssertEqual(forward[0].allReadings[0].remainingFraction, 0.6)
        XCTAssertEqual(forward[0].allReadings, reversed[0].allReadings)
    }

    func testDuplicateTimestampsAndValuesPreferTheKnownResetHorizon() {
        let series = QuotaHistory.series(from: [
            observation(at: 0, remaining: 0.5, resetAt: nil),
            observation(at: 0, remaining: 0.5, resetAt: 18_000),
        ])
        XCTAssertEqual(series[0].allReadings.count, 1)
        XCTAssertNotNil(series[0].allReadings[0].resetAt)
    }

    // MARK: - Provider scoping

    func testScopingToOneProviderKeepsItsSeriesAndDropsTheRest() {
        let history = QuotaHistoryView(
            observations: [
                observation(provider: "glm", at: 0),
                observation(provider: "minimax", at: 0),
            ],
            retentionLimit: 500
        )
        let scoped = QuotaHistory.series(from: history, providerId: "glm")
        XCTAssertEqual(scoped.count, 1)
        XCTAssertEqual(scoped[0].identity.providerId, "glm")
    }

    func testAnEmptyHistoryProducesNoSeriesRatherThanAnEmptySeries() {
        // An empty series would render as a legend entry with no data, which
        // reads as a provider that reported nothing rather than one never asked.
        XCTAssertTrue(QuotaHistory.series(from: nil).isEmpty)
        XCTAssertTrue(
            QuotaHistory.series(from: QuotaHistoryView(observations: [], retentionLimit: 500))
                .isEmpty
        )
    }

    // MARK: - Series styling

    func testSeriesStyleNeverUsesAStatusColour() {
        // Red, orange and green mean critical, caution and healthy everywhere
        // else. A series painted in one would claim a verdict it does not carry.
        let statusColours = [
            StatusTone.critical.color, StatusTone.caution.color, StatusTone.positive.color,
        ]
        for colour in QuotaSeriesStyle.palette {
            XCTAssertFalse(statusColours.contains(colour))
        }
    }

    func testSeriesStyleIsStableForTheSameIdentity() {
        let identities = [
            QuotaSeriesIdentity(providerId: "zzz", quotaPoolId: "p", windowId: "5h"),
            QuotaSeriesIdentity(providerId: "aaa", quotaPoolId: "p", windowId: "5h"),
        ]
        let index = QuotaSeriesStyle.index(of: identities[0], in: identities)
        let reordered = QuotaSeriesStyle.index(of: identities[0], in: identities.reversed())
        XCTAssertEqual(index, reordered, "sorting makes the assignment order-independent")
        XCTAssertEqual(QuotaSeriesStyle.index(of: identities[1], in: identities), 0)
    }

    func testAnUnknownIdentityGetsNoStyleRatherThanTheFirstOne() {
        let known = [QuotaSeriesIdentity(providerId: "a", quotaPoolId: "p", windowId: "5h")]
        let stranger = QuotaSeriesIdentity(providerId: "b", quotaPoolId: "p", windowId: "5h")
        XCTAssertNil(QuotaSeriesStyle.index(of: stranger, in: known))
    }

    func testColourAndSymbolAdvanceOutOfStepSoCombinationsStayDistinct() {
        // Six colours against four symbols: twelve series before a pair repeats.
        var seen: Set<String> = []
        for index in 0..<12 {
            let pair = "\(index % QuotaSeriesStyle.palette.count)-\(QuotaSeriesStyle.symbolIndex(at: index))"
            XCTAssertTrue(seen.insert(pair).inserted, "combination repeated at index \(index)")
        }
    }

    func testSeriesLabelNamesTheProviderAndTheWindow() {
        // Two windows of one provider are two series; a legend that named them
        // identically would be worse than none.
        let identity = QuotaSeriesIdentity(providerId: "glm", quotaPoolId: "p", windowId: "5h")
        let label = QuotaSeriesStyle.label(for: identity, displayNames: ["glm": "GLM Coding Plan"])
        XCTAssertTrue(label.contains("GLM Coding Plan"))
        XCTAssertTrue(label.contains("5h"))
    }
}
