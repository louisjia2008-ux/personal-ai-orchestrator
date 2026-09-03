import Foundation

import XCTest

@testable import PAOControlKit

/// B4: precision is a claim about accuracy.
///
/// A provider that reports 61% has not told us it is 61.0000%, and a rate
/// averaged over four readings is not known to four decimals. These pin that no
/// quota figure reaches the interface carrying more precision than its evidence
/// supports — and that `String(describing:)` on a `Double` never does.
final class QuotaFormattingTests: XCTestCase {

    private let locale = Locale(identifier: "en_US")

    // MARK: - Percentages

    func testPercentagesAreWholeNumbers() {
        // The resolution providers report in, and the resolution a meter shows.
        XCTAssertEqual(QuotaFormat.percent(0.6123456789, locale: locale), "61%")
        XCTAssertEqual(QuotaFormat.percent(1.0, locale: locale), "100%")
        XCTAssertEqual(QuotaFormat.percent(0, locale: locale), "0%")
    }

    func testAPercentageNeverLeaksRawDoubleDigits() {
        for value in [0.3333333333333333, 0.1 + 0.2, 0.9999999] {
            let text = QuotaFormat.percent(value, locale: locale)
            XCTAssertFalse(text.contains("."), "\(text) leaked fractional digits")
            XCTAssertFalse(text.contains("e"), "\(text) leaked scientific notation")
        }
    }

    // MARK: - Rates

    func testARateKeepsOneDecimalBecauseTheDifferenceMatters() {
        // 8.2%/h and 8%/h can decide whether a window survives the hour.
        XCTAssertTrue(QuotaFormat.ratePerHour(0.0823, locale: locale).contains("8.2%"))
    }

    func testAnImperceptibleRateReadsAsBarelyMovingRatherThanAsZero() {
        // Rounding a tiny positive rate to "0.0% / hour" would report a window
        // that is being consumed as one that is not.
        let text = QuotaFormat.ratePerHour(0.0000001, locale: locale)
        XCTAssertFalse(text.contains("0.0%"), "an imperceptible rate rounded to zero")
        XCTAssertTrue(text.contains("0.1%"), "the threshold is stated: \(text)")
    }

    func testAnExactlyZeroRateIsNotReportedAsBelowAThreshold() {
        // Flat is a measurement. "under 0.1% / hour" would understate it.
        let text = QuotaFormat.ratePerHour(0, locale: locale)
        XCTAssertTrue(text.contains("0.0%"), text)
    }

    func testARateNeverUsesScientificNotation() {
        for value in [1e-9, 1e-4, 0.5] {
            XCTAssertFalse(QuotaFormat.ratePerHour(value, locale: locale).lowercased().contains("e-"))
        }
    }

    // MARK: - Units

    func testProviderUnitsAreCountedNotMeasured() {
        XCTAssertEqual(QuotaFormat.units(1234.567, locale: locale), "1,235")
    }

    func testARatioWithOneSideMissingIsNotARatio() {
        XCTAssertNil(QuotaFormat.unitRatio(remaining: 40, total: nil, unit: "credits"))
        XCTAssertNil(QuotaFormat.unitRatio(remaining: nil, total: 100, unit: "credits"))
    }

    func testARatioCarriesItsUnitWhenTheProviderNamedOne() {
        XCTAssertEqual(
            QuotaFormat.unitRatio(remaining: 40, total: 100, unit: "CREDITS", locale: locale),
            "40 / 100 credits"
        )
        // An unnamed or UNKNOWN unit is omitted rather than printed verbatim as
        // a word the owner would read as the unit's name.
        XCTAssertEqual(
            QuotaFormat.unitRatio(remaining: 40, total: 100, unit: "UNKNOWN", locale: locale),
            "40 / 100"
        )
        XCTAssertEqual(
            QuotaFormat.unitRatio(remaining: 40, total: 100, unit: nil, locale: locale),
            "40 / 100"
        )
    }

    // MARK: - Horizons

    func testAHorizonStopsAtTwoUnits() {
        // "2d 3h" is what the owner acts on; the trailing minutes and seconds
        // change on every redraw and inform nothing.
        //
        // Asserted by structure rather than by literal text: the unit words are
        // localized, and pinning the English ones here would make the rule fail
        // on a Chinese machine for a reason that has nothing to do with the rule.
        let long = QuotaFormat.horizon(2 * 86_400 + 3 * 3_600 + 14 * 60 + 9)
        XCTAssertEqual(numbers(in: long), [2, 3], "days and hours only: \(long)")

        let medium = QuotaFormat.horizon(3 * 3_600 + 14 * 60 + 9)
        XCTAssertEqual(numbers(in: medium), [3, 14], "hours and minutes only: \(medium)")

        let short = QuotaFormat.horizon(14 * 60 + 9)
        XCTAssertEqual(numbers(in: short), [14], "minutes only, seconds dropped: \(short)")
    }

    func testEveryHorizonFormIsDefinedInBothLanguages() {
        for key in [
            "quota.horizon.daysHours", "quota.horizon.hoursMinutes", "quota.horizon.minutes",
        ] {
            XCTAssertNotNil(L10n.catalogString(key: key, language: "en"), key)
            XCTAssertNotNil(L10n.catalogString(key: key, language: "zh-Hans"), key)
        }
    }

    func testANegativeHorizonIsNeverPrintedAsNegativeTime() {
        let text = QuotaFormat.horizon(-500)
        XCTAssertEqual(numbers(in: text), [0])
        XCTAssertFalse(text.contains("-"), text)
    }

    // MARK: - Countdowns and ages

    func testACountdownStopsRatherThanRunningNegative() {
        // A countdown into negative numbers presents stale data as live.
        let now = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertNil(QuotaFormat.timeUntil(now.addingTimeInterval(-60), now: now))
        XCTAssertNil(QuotaFormat.timeUntil(now, now: now))
        let hour = QuotaFormat.timeUntil(now.addingTimeInterval(3_600), now: now)
        XCTAssertEqual(numbers(in: hour ?? ""), [1, 0])
    }

    func testClockSkewNeverProducesANegativeObservationAge() {
        // A daemon clock running ahead of the client must not report a reading
        // taken in the future as being minus four minutes old.
        let now = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertNil(QuotaFormat.age(of: now.addingTimeInterval(240), now: now))
        XCTAssertEqual(numbers(in: QuotaFormat.age(of: now.addingTimeInterval(-240), now: now) ?? ""), [4])
    }

    // MARK: - Evidence presentation

    func testAForecastArrivesAlreadyMarkedAsOne() {
        let forecast = MetricPresentation(
            text: QuotaFormat.percent(0.27, locale: locale), level: .forecast
        )
        XCTAssertTrue(forecast.displayText.contains("≈"))
        XCTAssertTrue(forecast.displayText.contains("27%"))

        let observed = MetricPresentation(
            text: QuotaFormat.percent(0.61, locale: locale), level: .observed
        )
        XCTAssertFalse(observed.displayText.contains("≈"))
    }

    func testAnUnavailableMetricRendersItsReasonRatherThanAZero() {
        // Absent and zero mean opposite things, and a quota surface that
        // conflates them tells the owner they have run out when they have not.
        let metric = MetricPresentation.unavailable(
            reason: QuotaProjectionUnavailable.insufficientObservations.explanation,
            level: .forecast
        )
        XCTAssertTrue(metric.isUnavailable)
        XCTAssertNotEqual(metric.text, "0")
        XCTAssertNotEqual(metric.text, "0%")
        XCTAssertFalse(metric.unavailableReason?.isEmpty ?? true)
    }

    // MARK: - Helpers

    /// The integers a rendered horizon contains, in order. Lets a test assert
    /// which units survived without depending on the language they are named in.
    private func numbers(in text: String) -> [Int] {
        text.split(whereSeparator: { !$0.isNumber })
            .compactMap { Int($0) }
    }
}
