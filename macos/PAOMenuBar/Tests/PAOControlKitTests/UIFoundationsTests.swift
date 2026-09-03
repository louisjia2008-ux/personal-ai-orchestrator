import Foundation

import XCTest

@testable import PAOControlKit

/// B1: the shared presentation foundations.
///
/// These pin the rules that were previously re-decided per call site, and the
/// two rules the redesign depends on most: unknown never reads as healthy, and a
/// forecast never reads as an observed value.
final class UIFoundationsTests: XCTestCase {

    // MARK: - Status vocabulary

    func testUnknownStateIsNeverPresentedAsHealthy() {
        // A state this build has not seen must not resolve to success. Five
        // independent colour maps previously made this a per-view decision.
        let unknown = StatusStyle.task(state: "SOME_FUTURE_STATE")
        XCTAssertEqual(unknown.tone, .unknown)
        XCTAssertNotEqual(unknown.tone, .positive)
    }

    func testUnknownIsDistinctFromNeutral() {
        // "We do not know" and "nothing to report" must not share a treatment.
        XCTAssertNotEqual(StatusTone.unknown, StatusTone.neutral)
        XCTAssertNotEqual(
            StatusStyle.quotaConfidence("UNKNOWN").tone,
            StatusStyle.task(state: "RUNNING").tone
        )
    }

    func testEveryToneCarriesANonEmptySymbol() {
        // Colour is never the only channel: the status has to survive greyscale.
        let presentations = [
            StatusStyle.task(state: "RUNNING"),
            StatusStyle.task(state: "BLOCKED"),
            StatusStyle.task(state: "COMPLETED"),
            StatusStyle.task(state: "???"),
            StatusStyle.severity("BLOCKED"),
            StatusStyle.severity("WARNING"),
            StatusStyle.quotaConfidence("EXACT"),
            StatusStyle.quotaConfidence("UNKNOWN"),
            StatusStyle.quotaState("EXHAUSTED_OBSERVED"),
            StatusStyle.connection("CONNECTED"),
            StatusStyle.verification("FAILED"),
        ]
        for presentation in presentations {
            XCTAssertFalse(presentation.symbol.isEmpty)
        }
    }

    func testTaskStatesResolveToTheExpectedTones() {
        XCTAssertEqual(StatusStyle.task(state: "BLOCKED").tone, .critical)
        XCTAssertEqual(StatusStyle.task(state: "FAILED").tone, .critical)
        XCTAssertEqual(StatusStyle.task(state: "COMPLETED").tone, .positive)
        XCTAssertEqual(StatusStyle.task(state: "VERIFIED").tone, .positive)
        XCTAssertEqual(StatusStyle.task(state: "RUNNING").tone, .neutral)
        XCTAssertEqual(StatusStyle.task(state: "SUBMITTED").tone, .caution)
    }

    func testQuotaConfidenceNeverMakesUnknownLookMeasured() {
        XCTAssertEqual(StatusStyle.quotaConfidence("EXACT").tone, .positive)
        XCTAssertEqual(StatusStyle.quotaConfidence("ESTIMATED").tone, .caution)
        XCTAssertEqual(StatusStyle.quotaConfidence("UNKNOWN").tone, .unknown)
        XCTAssertEqual(StatusStyle.quotaConfidence("ANYTHING_ELSE").tone, .unknown)
    }

    // MARK: - Routing role status vs outcome

    func testCompletedFailureReadsAsAVerdictNotABreakage() {
        // COMPLETED + FAIL is a review that rejected the work. Rendering it with
        // the same tone as a crashed reviewer would misreport what happened.
        let rejected = StatusStyle.routingRole(status: .completed, outcome: .fail)
        let crashed = StatusStyle.routingRole(status: .completed, outcome: .error)
        XCTAssertEqual(rejected.tone, .caution)
        XCTAssertEqual(crashed.tone, .critical)
        XCTAssertNotEqual(rejected.tone, crashed.tone)
    }

    func testRunningRoleIgnoresOutcomeUntilItCompletes() {
        let running = StatusStyle.routingRole(status: .running, outcome: .none)
        XCTAssertEqual(running.tone, StatusStyle.routingStatus(.running).tone)
    }

    func testUnknownRoleStatusIsNotSettled() {
        let style = StatusStyle.routingRole(
            status: RoutingRoleStatus(rawValue: "QUARANTINED"), outcome: .none
        )
        XCTAssertEqual(style.tone, .unknown)
    }

    // MARK: - Evidence levels

    func testForecastIsNeverVisuallyIdenticalToAnObservedValue() {
        // The rule the quota surface depends on: an extrapolation must not be
        // mistakable for a provider reading.
        let observed = MetricPresentation(text: "61%", level: .observed)
        let forecast = MetricPresentation(text: "34%", level: .forecast)

        XCTAssertNotEqual(observed.displayText, "≈\u{202F}61%")
        XCTAssertEqual(observed.displayText, "61%")
        XCTAssertTrue(forecast.displayText.hasPrefix("≈"))
        XCTAssertNotEqual(observed.level.fontWeight, forecast.level.fontWeight)
        XCTAssertTrue(forecast.level.isDashed)
        XCTAssertFalse(observed.level.isDashed)
    }

    func testOnlyObservedValuesMayFillAMeter() {
        // A projection inside the meter would claim to be the reading.
        XCTAssertTrue(EvidenceLevel.observed.mayFillMeter)
        XCTAssertFalse(EvidenceLevel.derived.mayFillMeter)
        XCTAssertFalse(EvidenceLevel.forecast.mayFillMeter)
    }

    func testDerivedIsDistinctFromBothObservedAndForecast() {
        let derived = MetricPresentation(text: "1.9%/h", level: .derived)
        XCTAssertEqual(derived.displayText, "1.9%/h", "derived states a computed fact plainly")
        XCTAssertFalse(derived.level.isDashed)
        XCTAssertFalse(derived.level.mayFillMeter)
    }

    func testUnavailableExplainsItselfRatherThanRenderingZero() {
        // Absent is not zero. A projection that cannot be made must say why.
        let missing = MetricPresentation.unavailable(reason: "NO_HISTORY", level: .forecast)
        XCTAssertTrue(missing.isUnavailable)
        XCTAssertEqual(missing.unavailableReason, "NO_HISTORY")
        XCTAssertFalse(missing.displayText.contains("0"))
        XCTAssertFalse(missing.displayText.hasPrefix("≈"), "an absence takes no estimate marker")
    }

    func testEvidenceLevelsCarryFreshness() {
        let value = MetricPresentation(
            text: "34%", level: .forecast, observedAt: "2026-09-03T12:03:00Z"
        )
        XCTAssertEqual(value.observedAt, "2026-09-03T12:03:00Z")
    }

    func testEveryEvidenceLevelIsLocalizedInBothLanguages() {
        for level in EvidenceLevel.allCases {
            for language in ["en", "zh-Hans"] {
                let key = "evidence.\(level.rawValue.lowercased())"
                XCTAssertNotNil(
                    L10n.catalogString(key: key, language: language),
                    "missing \(language) entry for \(key)"
                )
            }
        }
    }

    func testEvidenceLabelsDifferBetweenLanguages() {
        XCTAssertEqual(L10n.catalogString(key: "evidence.observed", language: "en"), "Observed")
        XCTAssertEqual(L10n.catalogString(key: "evidence.observed", language: "zh-Hans"), "实测")
        XCTAssertEqual(L10n.catalogString(key: "evidence.forecast", language: "en"), "Projected")
        XCTAssertEqual(L10n.catalogString(key: "evidence.forecast", language: "zh-Hans"), "预计")
    }

    // MARK: - Timestamps

    private let base = TaskTiming.parseTimestamp("2026-09-03T12:00:00Z")!

    func testSubMinuteAgesReadAsJustNowRatherThanZeroSeconds() {
        let recent = base.addingTimeInterval(-20)
        let text = Timestamps.relative(recent, now: base, locale: Locale(identifier: "en_US"))
        XCTAssertFalse(text.contains("0 seconds"))
    }

    func testRecentInstantsAreRelativeAndOlderOnesAbsolute() {
        let enUS = Locale(identifier: "en_US")
        let anHourAgo = Timestamps.friendly(
            "2026-09-03T11:00:00Z", now: base, locale: enUS
        )
        XCTAssertTrue(anHourAgo.contains("hour"), anHourAgo)

        // Past a day, "3 days ago" is less useful than the date itself.
        let lastWeek = Timestamps.friendly(
            "2026-08-27T11:00:00Z", now: base, locale: enUS
        )
        XCTAssertFalse(lastWeek.contains("ago"), lastWeek)
    }

    func testRelativeTimeFollowsTheProductLanguage() {
        // The previous helper hard-coded English unit names, so a zh-Hans user
        // read "Updated 3m ago" on an otherwise localized screen.
        let zh = Timestamps.relative(
            base.addingTimeInterval(-3600), now: base, locale: Locale(identifier: "zh_Hans_CN")
        )
        let en = Timestamps.relative(
            base.addingTimeInterval(-3600), now: base, locale: Locale(identifier: "en_US")
        )
        XCTAssertNotEqual(zh, en)
    }

    func testUnparseableTimestampsFallBackToTheRawValue() {
        // Better to show the machine value than to hide that something is wrong.
        XCTAssertEqual(Timestamps.friendly("not-a-date"), "not-a-date")
        XCTAssertEqual(Timestamps.absolute("not-a-date"), "not-a-date")
    }

    func testCountdownStopsAtZeroRatherThanGoingNegative() {
        XCTAssertNotNil(Timestamps.countdown(until: "2026-09-03T16:21:00Z", now: base))
        XCTAssertNil(
            Timestamps.countdown(until: "2026-09-03T11:00:00Z", now: base),
            "a passed instant has no remaining time to count"
        )
    }

    func testDurationFormatting() {
        XCTAssertEqual(Timestamps.duration(45), "45s")
        XCTAssertEqual(Timestamps.duration(90), "1m 30s")
        XCTAssertEqual(Timestamps.duration(3660), "1h 1m")
        XCTAssertEqual(Timestamps.duration(-5), "0s")
    }

    // MARK: - Layout tokens

    func testSpacingScaleIsOnAFourPointGrid() {
        for value in [Spacing.tight, Spacing.inner, Spacing.element, Spacing.section, Spacing.page] {
            XCTAssertEqual(value.truncatingRemainder(dividingBy: 4), 0, "\(value) is off-grid")
        }
    }

    func testRadiiAreDistinctAndOrdered() {
        XCTAssertLessThan(Radius.inline, Radius.panel)
    }
}
