import Foundation

/// How quota figures are written down.
///
/// Precision is a claim about accuracy. A provider that reports "61%" has not
/// told us it is 61.0000%, and a burn rate averaged over four readings is not
/// known to four decimal places — printing either at full `Double` width asserts
/// a certainty the measurement does not have. Every quota number in the
/// interface goes through here, and each kind of number gets the precision its
/// evidence supports:
///
/// - percentages: whole numbers, because that is the resolution providers report
///   in and the resolution a meter can show;
/// - rates: one decimal, because the difference between 8.2%/h and 8%/h changes
///   whether a window survives the hour;
/// - units: whole numbers, because credits and tokens are counted, not measured;
/// - durations: the largest two units that are non-zero, because "2d 3h" is what
///   the owner acts on and "2d 3h 14m 9s" is not.
///
/// `String(describing:)` on a `Double` never reaches the interface.
public enum QuotaFormat {

    /// A remaining/consumed percentage. Whole numbers.
    public static func percent(_ fraction: Double, locale: Locale = .current) -> String {
        fraction.formatted(
            .percent.precision(.fractionLength(0)).locale(locale)
        )
    }

    /// A percentage of a window consumed per hour, e.g. `8.2% / h`.
    ///
    /// One decimal, and never scientific notation: a rate slow enough to need
    /// exponents is a rate the owner should read as "barely moving", which is
    /// what `< 0.1% / h` says.
    public static func ratePerHour(_ fractionPerHour: Double, locale: Locale = .current) -> String {
        let magnitude = abs(fractionPerHour)
        if magnitude > 0 && magnitude < 0.001 {
            return L10n.quotaRateBelowThreshold(
                (0.001).formatted(.percent.precision(.fractionLength(1)).locale(locale))
            )
        }
        return L10n.quotaRatePerHour(
            fractionPerHour.formatted(
                .percent.precision(.fractionLength(1)).locale(locale)
            )
        )
    }

    /// A provider-reported quantity in its own unit. Counted, so no decimals.
    public static func units(_ value: Double, locale: Locale = .current) -> String {
        value.formatted(.number.precision(.fractionLength(0)).locale(locale))
    }

    /// `remaining / total` in the provider's unit, or nil when either half is
    /// missing. A ratio with one side unknown is not a ratio.
    public static func unitRatio(
        remaining: Double?, total: Double?, unit: String?, locale: Locale = .current
    ) -> String? {
        guard let remaining, let total else { return nil }
        let base = "\(units(remaining, locale: locale)) / \(units(total, locale: locale))"
        guard let unit, !unit.isEmpty, unit != "UNKNOWN" else { return base }
        return "\(base) \(unit.lowercased())"
    }

    /// A span, to the two largest non-zero units.
    ///
    /// Distinct from `Timestamps.duration`, which measures task runtime in
    /// seconds and minutes. Quota horizons are hours and days, and seconds in a
    /// six-day countdown are noise that changes every time the view redraws.
    public static func horizon(_ interval: TimeInterval, locale: Locale = .current) -> String {
        let total = Int(max(0, interval.rounded()))
        let days = total / 86_400
        let hours = (total % 86_400) / 3_600
        let minutes = (total % 3_600) / 60
        if days > 0 { return L10n.quotaHorizonDaysHours(days, hours) }
        if hours > 0 { return L10n.quotaHorizonHoursMinutes(hours, minutes) }
        return L10n.quotaHorizonMinutes(minutes)
    }

    /// Time until a reset instant, or nil once it has passed. A countdown that
    /// runs negative is reporting stale data as if it were live.
    public static func timeUntil(_ date: Date, now: Date = Date()) -> String? {
        let remaining = date.timeIntervalSince(now)
        guard remaining > 0 else { return nil }
        return horizon(remaining)
    }

    /// Age of an observation, or nil when it is dated in the future — clock skew
    /// between the daemon and the client must not print a negative age.
    public static func age(of date: Date, now: Date = Date()) -> String? {
        let elapsed = now.timeIntervalSince(date)
        guard elapsed >= 0 else { return nil }
        return horizon(elapsed)
    }
}
