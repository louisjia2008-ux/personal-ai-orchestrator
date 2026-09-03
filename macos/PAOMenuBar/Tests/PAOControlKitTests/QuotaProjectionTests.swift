import Foundation

import XCTest

@testable import PAOControlKit

/// B4: the deterministic burn rate and forecast.
///
/// Most of these test a refusal. A forecast is the one figure on this surface
/// that is not a fact, so the interesting property is not that it computes the
/// right number from good history — it is that it declines, by name, on every
/// kind of history that cannot support one.
final class QuotaProjectionTests: XCTestCase {

    // MARK: - Fixtures

    private static let base = Date(timeIntervalSince1970: 1_760_000_000)

    private func at(_ offset: TimeInterval) -> Date {
        Self.base.addingTimeInterval(offset)
    }

    private let series = QuotaSeriesIdentity(
        providerId: "glm", quotaPoolId: "pool-a", windowId: "5h"
    )

    /// Builds one reset window directly, so a test states the readings it means
    /// rather than the observations that would produce them.
    private func segment(
        _ readings: [(offset: TimeInterval, remaining: Double)],
        resetAt: TimeInterval? = 18_000
    ) -> QuotaWindowSegment {
        QuotaHistory.segments(
            of: series,
            readings: readings.map {
                QuotaReading(
                    observedAt: at($0.offset),
                    remainingFraction: $0.remaining,
                    resetAt: resetAt.map(at)
                )
            }
        )[0]
    }

    // MARK: - Burn rate

    func testDecreasingQuotaProducesAPositiveBurnRate() {
        // 100% to 50% across two hours is 25% per hour.
        let rate = try? QuotaForecast.burnRate(
            in: segment([(0, 1.0), (7_200, 0.5)])
        ).get()
        XCTAssertEqual(rate?.fractionPerHour ?? 0, 0.25, accuracy: 1e-9)
        XCTAssertEqual(rate?.sampleCount, 2)
        XCTAssertEqual(rate?.spanSeconds, 7_200)
        XCTAssertEqual(rate?.remainingFraction, 0.5, "the rate carries the latest reading")
    }

    func testFlatConsumptionIsARateOfZeroNotAnAbsentRate() {
        // "Nothing was consumed" is a measurement. Refusing it would leave the
        // owner unable to tell a quiet window from an unobserved one.
        let rate = try? QuotaForecast.burnRate(
            in: segment([(0, 0.6), (3_600, 0.6)])
        ).get()
        XCTAssertEqual(rate?.fractionPerSecond, 0)
        XCTAssertEqual(rate?.isFlat, true)
        XCTAssertNil(rate?.secondsToExhaustion, "a flat rate never reaches zero")
    }

    func testASingleObservationIsNotARate() {
        assertBurnRateFails(
            segment([(0, 0.9)]), with: .insufficientObservations
        )
    }

    func testIdenticalTimestampsCannotProduceARate() {
        // The normalizer collapses one instant to one reading, so this arrives
        // as a single-sample window rather than as a division by zero.
        let single = segment([(0, 0.9), (0, 0.8)])
        XCTAssertEqual(single.count, 1)
        assertBurnRateFails(single, with: .insufficientObservations)
    }

    func testAZeroSpanIsRefusedRatherThanDividedBy() {
        // Guards the arithmetic directly, in case a future segment reaches the
        // rate with two readings sharing an instant.
        let readings = [
            QuotaReading(observedAt: at(0), remainingFraction: 0.9, resetAt: at(18_000)),
            QuotaReading(observedAt: at(0), remainingFraction: 0.5, resetAt: at(18_000)),
        ]
        let handmade = QuotaWindowSegment(
            series: series, readings: readings, resetAt: at(18_000)
        )
        assertBurnRateFails(handmade, with: .noElapsedTime)
        XCTAssertEqual(handmade.observedSpan, 0)
    }

    func testAnIncreasingBalanceIsRefusedRatherThanExtrapolatedUpward() {
        // Extrapolating a rising balance forecasts a surplus no provider
        // promised, and would let a meter fill from a projection.
        assertBurnRateFails(
            segment([(0, 0.4), (3_600, 0.8)]), with: .remainingIncreased
        )
    }

    func testNoHistoryIsItsOwnReasonRatherThanInsufficientSamples() {
        // "Never observed" is fixed by refreshing; "one reading so far" is fixed
        // by waiting. They must not read the same.
        assertBurnRateFails(nil, with: .noHistory)
    }

    func testTheRateIsMeasuredOverOneResetWindowOnly() {
        // Readings either side of a reset never form one slope. Here the pre-
        // reset window is dropped entirely and the rate describes the current
        // one: 90% to 70% in one hour, not 30% to 90% across the boundary.
        let segments = QuotaHistory.segments(
            of: series,
            readings: [
                QuotaReading(observedAt: at(0), remainingFraction: 0.3, resetAt: at(3_600)),
                QuotaReading(observedAt: at(7_200), remainingFraction: 0.9, resetAt: at(25_200)),
                QuotaReading(observedAt: at(10_800), remainingFraction: 0.7, resetAt: at(25_200)),
            ]
        )
        XCTAssertEqual(segments.count, 2)
        let rate = try? QuotaForecast.burnRate(in: segments.last).get()
        XCTAssertEqual(rate?.fractionPerHour ?? 0, 0.2, accuracy: 1e-9)
        XCTAssertEqual(rate?.sampleCount, 2)
    }

    // MARK: - Projection

    func testProjectedRemainingAtResetExtendsTheMeasuredRate() {
        // 20% per hour from 60% remaining, two hours to reset: 20% left.
        let outlook = outlookFor(
            segment([(0, 1.0), (7_200, 0.6)]), now: at(7_200), resetAt: 14_400
        )
        let projection = outlook.projection
        XCTAssertNotNil(projection)
        XCTAssertEqual(projection?.projectedRemainingAtReset ?? -1, 0.2, accuracy: 1e-9)
        XCTAssertNil(projection?.exhaustionAt)
        XCTAssertEqual(projection?.expectsExhaustion, false)
    }

    func testProjectedExhaustionBeforeResetIsDatedRatherThanImplied() {
        // 20% per hour from 20% remaining, six hours to reset: gone in one hour.
        let outlook = outlookFor(
            segment([(0, 0.6), (7_200, 0.2)]), now: at(7_200), resetAt: 28_800
        )
        let projection = outlook.projection
        XCTAssertEqual(projection?.expectsExhaustion, true)
        XCTAssertEqual(projection?.exhaustionAt, at(10_800))
        XCTAssertEqual(projection?.projectedRemainingAtReset, 0)
        XCTAssertNil(
            projection?.likelyUnusedFraction,
            "quota expected to run out cannot also be expected to go unused"
        )
    }

    func testExhaustionIsDatedFromTheLastReadingNotFromNow() {
        // The rate describes the span it was measured over. Dating exhaustion
        // from a later "now" would silently grant the window free time.
        let outlook = outlookFor(
            segment([(0, 0.6), (7_200, 0.2)]), now: at(9_000), resetAt: 28_800
        )
        XCTAssertEqual(outlook.projection?.exhaustionAt, at(10_800))
    }

    func testQuotaOutlastingItsWindowIsNotReportedAsExhaustion() {
        // 5% per hour from 90%, one hour to reset. Eighteen hours of headroom
        // against one hour of window: it resets first.
        let outlook = outlookFor(
            segment([(0, 1.0), (7_200, 0.9)]), now: at(7_200), resetAt: 10_800
        )
        XCTAssertEqual(outlook.projection?.expectsExhaustion, false)
        XCTAssertEqual(outlook.projection?.projectedRemainingAtReset ?? 0, 0.85, accuracy: 1e-9)
    }

    func testLikelyUnusedQuotaIsReportedWhenTheWindowResetsWithHeadroomLeft() {
        // The harvest question: is paid quota about to expire unused?
        let outlook = outlookFor(
            segment([(0, 1.0), (7_200, 0.95)]), now: at(7_200), resetAt: 10_800
        )
        XCTAssertEqual(outlook.projection?.likelyUnusedFraction ?? 0, 0.925, accuracy: 1e-9)
    }

    func testFlatConsumptionProjectsTheCurrentBalanceForwardUnchanged() {
        let outlook = outlookFor(
            segment([(0, 0.7), (3_600, 0.7)]), now: at(3_600), resetAt: 18_000
        )
        XCTAssertEqual(outlook.projection?.projectedRemainingAtReset, 0.7)
        XCTAssertEqual(outlook.projection?.expectsExhaustion, false)
        XCTAssertEqual(outlook.projection?.likelyUnusedFraction, 0.7)
    }

    func testProjectionIsClampedIntoTheRangeAPercentageCanOccupy() throws {
        // A very fast rate over a long horizon extrapolates below zero. The
        // clamp is where that becomes "runs out", never a negative balance.
        let outlook = outlookFor(
            segment([(0, 1.0), (600, 0.1)]), now: at(600), resetAt: 86_400
        )
        let projected = try XCTUnwrap(outlook.projection?.projectedRemainingAtReset)
        XCTAssertGreaterThanOrEqual(projected, 0)
        XCTAssertLessThanOrEqual(projected, 1)
        XCTAssertEqual(projected, 0)
    }

    // MARK: - Projection refusals

    func testAMissingResetLeavesTheRateButRefusesTheProjection() {
        // A window with no reported reset still has a measurable burn rate.
        // Reporting "8% per hour, reset unknown" beats reporting nothing.
        let outlook = QuotaForecast.outlook(
            for: QuotaSeries(
                identity: series,
                segments: [segment([(0, 0.9), (3_600, 0.7)], resetAt: nil)]
            ),
            now: at(3_600)
        )
        XCTAssertTrue(outlook.hasBurnRate)
        XCTAssertFalse(outlook.hasProjection)
        XCTAssertEqual(outlook.projectionUnavailable, .resetUnknown)
        XCTAssertNil(outlook.burnRateUnavailable)
    }

    func testAResetAlreadyPassedRefusesTheProjectionAsStaleRatherThanForecasting() {
        // The readings describe a window that has ended. Projecting into it
        // would present expired evidence as a live forecast.
        let outlook = outlookFor(
            segment([(0, 0.9), (3_600, 0.7)]), now: at(25_000), resetAt: 18_000
        )
        XCTAssertTrue(outlook.hasBurnRate)
        XCTAssertEqual(outlook.projectionUnavailable, .resetAlreadyPassed)
    }

    func testAResetExactlyNowIsTreatedAsPassedRatherThanAsZeroHorizon() {
        let outlook = outlookFor(
            segment([(0, 0.9), (3_600, 0.7)]), now: at(18_000), resetAt: 18_000
        )
        XCTAssertEqual(outlook.projectionUnavailable, .resetAlreadyPassed)
    }

    func testAnAbsentRateMakesTheProjectionUnavailableForTheSameReason() {
        // One fault, reported once. A second independent reason would suggest
        // two things went wrong.
        let outlook = QuotaForecast.outlook(
            for: QuotaSeries(identity: series, segments: [segment([(0, 0.9)])]),
            now: at(0)
        )
        XCTAssertEqual(outlook.burnRateUnavailable, .insufficientObservations)
        XCTAssertEqual(outlook.projectionUnavailable, .insufficientObservations)
    }

    func testAnIncreasingBalanceProducesNoProjectionAtAll() {
        let outlook = outlookFor(
            segment([(0, 0.4), (3_600, 0.8)]), now: at(3_600), resetAt: 18_000
        )
        XCTAssertFalse(outlook.hasBurnRate)
        XCTAssertFalse(outlook.hasProjection)
        XCTAssertEqual(outlook.projectionUnavailable, .remainingIncreased)
    }

    func testASeriesWithNoSegmentsReportsNoHistory() {
        let outlook = QuotaForecast.outlook(
            for: QuotaSeries(identity: series, segments: []), now: at(0)
        )
        XCTAssertEqual(outlook.burnRateUnavailable, .noHistory)
        XCTAssertNil(outlook.segment)
    }

    func testEveryRefusalReasonHasItsOwnExplanationInBothLanguages() {
        // Six reasons that lead to six different owner actions. A shared
        // sentence would make them indistinguishable.
        var english: Set<String> = []
        for reason in QuotaProjectionUnavailable.allCases {
            let key = "quota.projection.unavailable.\(reason.rawValue)"
            let en = L10n.catalogString(key: key, language: "en")
            let zh = L10n.catalogString(key: key, language: "zh-Hans")
            XCTAssertNotNil(en, "missing en for \(reason.rawValue)")
            XCTAssertNotNil(zh, "missing zh-Hans for \(reason.rawValue)")
            XCTAssertNotEqual(en, zh, "\(reason.rawValue) was not translated")
            XCTAssertTrue(english.insert(en ?? "").inserted, "\(reason.rawValue) reuses a sentence")
        }
    }

    // MARK: - Evidence semantics

    func testAProjectionIsNeverClassifiedObserved() {
        let outlook = outlookFor(
            segment([(0, 1.0), (7_200, 0.6)]), now: at(7_200), resetAt: 14_400
        )
        let projection = try? XCTUnwrap(outlook.projection)
        XCTAssertEqual(projection?.evidenceLevel, .forecast)
        XCTAssertNotEqual(projection?.evidenceLevel, .observed)
        XCTAssertNotEqual(projection?.evidenceLevel, .derived)
    }

    func testAForecastMayNeverFillAMeter() {
        // The rule the whole evidence model exists for: a projection drawn
        // inside a quota bar would claim to *be* the provider's reading.
        XCTAssertFalse(EvidenceLevel.forecast.mayFillMeter)
        XCTAssertTrue(EvidenceLevel.observed.mayFillMeter)
        XCTAssertFalse(EvidenceLevel.derived.mayFillMeter)
    }

    func testAForecastCarriesItsApproximationMarker() {
        XCTAssertTrue(EvidenceLevel.forecast.valuePrefix.contains("≈"))
        XCTAssertTrue(EvidenceLevel.observed.valuePrefix.isEmpty)
    }

    // MARK: - Cross-series safety

    func testOutlooksAreComputedPerSeriesAndNeverAcrossProviders() {
        let history = QuotaHistoryView(
            observations: [
                observed(provider: "glm", offset: 0, remaining: 0.9),
                observed(provider: "glm", offset: 3_600, remaining: 0.7),
                observed(provider: "minimax", offset: 1_800, remaining: 0.2),
                observed(provider: "minimax", offset: 5_400, remaining: 0.1),
            ],
            retentionLimit: 500
        )
        let outlooks = QuotaForecast.outlooks(from: history, now: at(5_400))
        XCTAssertEqual(outlooks.count, 2)
        // Each rate is measured inside its own series. A joined series would
        // produce one rate describing neither provider.
        let glm = outlooks.first { $0.series.providerId == "glm" }
        let minimax = outlooks.first { $0.series.providerId == "minimax" }
        XCTAssertEqual(glm?.burnRate?.fractionPerHour ?? 0, 0.2, accuracy: 1e-9)
        XCTAssertEqual(minimax?.burnRate?.fractionPerHour ?? 0, 0.1, accuracy: 1e-9)
    }

    func testOutlooksAreComputedPerBindingAndNeverAcrossPools() {
        let history = QuotaHistoryView(
            observations: [
                observed(pool: "pool-a", offset: 0, remaining: 1.0),
                observed(pool: "pool-a", offset: 3_600, remaining: 0.5),
                observed(pool: "pool-b", offset: 0, remaining: 1.0),
                observed(pool: "pool-b", offset: 3_600, remaining: 0.95),
            ],
            retentionLimit: 500
        )
        let outlooks = QuotaForecast.outlooks(from: history, now: at(3_600))
        XCTAssertEqual(outlooks.count, 2)
        let rates = outlooks.compactMap { $0.burnRate?.fractionPerHour }
        XCTAssertEqual(rates.count, 2)
        XCTAssertNotEqual(rates[0], rates[1], "one averaged rate would describe neither pool")
    }

    func testScopingOutlooksToOneProviderExcludesTheOthers() {
        let history = QuotaHistoryView(
            observations: [
                observed(provider: "glm", offset: 0, remaining: 0.9),
                observed(provider: "minimax", offset: 0, remaining: 0.2),
            ],
            retentionLimit: 500
        )
        let scoped = QuotaForecast.outlooks(from: history, providerId: "glm", now: at(0))
        XCTAssertEqual(scoped.count, 1)
        XCTAssertEqual(scoped[0].series.providerId, "glm")
    }

    // MARK: - Helpers

    private func outlookFor(
        _ segment: QuotaWindowSegment, now: Date, resetAt: TimeInterval
    ) -> QuotaOutlook {
        QuotaForecast.outlook(
            for: QuotaSeries(
                identity: series,
                segments: [
                    QuotaWindowSegment(
                        series: series, readings: segment.readings, resetAt: at(resetAt)
                    )
                ]
            ),
            now: now
        )
    }

    private func assertBurnRateFails(
        _ segment: QuotaWindowSegment?,
        with expected: QuotaProjectionUnavailable,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        switch QuotaForecast.burnRate(in: segment) {
        case .success(let rate):
            XCTFail("expected \(expected.rawValue), got a rate of \(rate.fractionPerHour)",
                    file: file, line: line)
        case .failure(let reason):
            XCTAssertEqual(reason, expected, file: file, line: line)
        }
    }

    private func observed(
        provider: String = "glm",
        pool: String = "pool-a",
        offset: TimeInterval,
        remaining: Double
    ) -> QuotaObservationView {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let json = """
            {
              "provider_id": "\(provider)", "quota_pool_id": "\(pool)", "window_id": "5h",
              "observed_at": "\(formatter.string(from: at(offset)))",
              "remaining_fraction": \(remaining), "confidence": "EXACT",
              "measurement_source": "PROVIDER_API",
              "reset_at": "\(formatter.string(from: at(86_400)))", "state": "AVAILABLE"
            }
            """
        // swiftlint:disable:next force_try
        return try! JSONDecoder().decode(QuotaObservationView.self, from: Data(json.utf8))
    }
}
