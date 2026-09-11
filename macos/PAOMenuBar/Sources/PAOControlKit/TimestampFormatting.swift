import Foundation

/// One way to render an instant.
///
/// The client previously printed raw ISO-8601 strings in most places and
/// hand-rolled an English relative-time helper in one, so the same instant could
/// appear three different ways on one screen and none of them followed the
/// product language. Every timestamp the owner reads now goes through here; the
/// raw machine value stays available for the inspector, where verbatim is the
/// point.
public enum Timestamps {

    /// The locale every timestamp is rendered in.
    ///
    /// Not `Locale.current`: the app resolves its language from the macOS
    /// preferred-language list itself (`L10n.resolvedLanguageCode`), because
    /// CFBundle's automatic matching is unreliable for SwiftPM resource
    /// bundles. Left on `Locale.current` the process locale disagreed with the
    /// resolved language, and a zh-Hans dashboard printed "5 hours ago" and
    /// "Monday, August 31" beside its Chinese labels.
    public static var displayLocale: Locale {
        Locale(identifier: L10n.resolvedLanguageCode)
    }

    /// Relative for anything recent, absolute once relative stops being useful.
    ///
    /// "Updated 3 days ago" is worse than a date: past a day the owner wants to
    /// know *when*, not *how long ago*.
    public static func friendly(
        _ value: String,
        now: Date = Date(),
        locale: Locale = Timestamps.displayLocale
    ) -> String {
        guard let date = TaskTiming.parseTimestamp(value) else { return value }
        let interval = now.timeIntervalSince(date)
        if interval >= 0 && interval < 86_400 {
            return relative(date, now: now, locale: locale)
        }
        return absolute(date, locale: locale)
    }

    /// Localized relative time ("3 minutes ago" / "3 分钟前").
    ///
    /// `RelativeDateTimeFormatter` follows the user's locale, which is what keeps
    /// this consistent with the rest of the product language instead of pinning
    /// English unit names into the client.
    public static func relative(
        _ date: Date,
        now: Date = Date(),
        locale: Locale = Timestamps.displayLocale
    ) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = locale
        formatter.unitsStyle = .full
        // Under a minute, "0 seconds ago" reads as broken rather than fresh.
        if abs(now.timeIntervalSince(date)) < 60 {
            return L10n.timestampJustNow
        }
        return formatter.localizedString(for: date, relativeTo: now)
    }

    public static func absolute(_ date: Date, locale: Locale = Timestamps.displayLocale) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    public static func absolute(_ value: String, locale: Locale = Timestamps.displayLocale) -> String {
        guard let date = TaskTiming.parseTimestamp(value) else { return value }
        return absolute(date, locale: locale)
    }

    /// A duration in the compact form the dashboard uses for elapsed work.
    public static func duration(_ interval: TimeInterval) -> String {
        let seconds = max(0, Int(interval))
        if seconds < 60 { return "\(seconds)s" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes)m \(seconds % 60)s" }
        return "\(minutes / 60)h \(minutes % 60)m"
    }

    /// Time remaining until an instant, for reset countdowns.
    ///
    /// Returns nil once the instant has passed, so a caller renders "resetting"
    /// or re-reads rather than counting down into negative numbers. B4 uses this
    /// for quota reset horizons.
    public static func countdown(
        until value: String, now: Date = Date()
    ) -> String? {
        guard let date = TaskTiming.parseTimestamp(value) else { return nil }
        let remaining = date.timeIntervalSince(now)
        guard remaining > 0 else { return nil }
        return duration(remaining)
    }

    /// The clock time of an instant ("15:03"), for rows that already sit under a
    /// day heading and would only repeat the date.
    public static func timeOfDay(_ value: String, locale: Locale = Timestamps.displayLocale) -> String {
        guard let date = TaskTiming.parseTimestamp(value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    /// A day heading: "Today" / "Yesterday" / the date itself.
    ///
    /// `doesRelativeDateFormatting` is what supplies the localized "Today" and
    /// "Yesterday", so the product does not carry its own translations of two
    /// words the system already knows in every language it ships.
    public static func dayHeading(_ date: Date, locale: Locale = Timestamps.displayLocale) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateStyle = .full
        formatter.timeStyle = .none
        formatter.doesRelativeDateFormatting = true
        return formatter.string(from: date)
    }

    /// One day's worth of items, under the heading that names the day.
    public struct DayGroup<Item>: Identifiable {
        public let title: String
        public let items: [Item]
        public var id: String { title }
    }

    /// Splits a chronological list into day groups, preserving the order it was
    /// given. Items whose timestamp cannot be parsed keep their place in the
    /// list under the heading of the group being built, rather than being
    /// silently dropped.
    public static func groupedByDay<Item>(
        _ items: [Item],
        id timestamp: KeyPath<Item, String>,
        now: Date = Date(),
        locale: Locale = Timestamps.displayLocale,
        calendar: Calendar = .current
    ) -> [DayGroup<Item>] {
        var groups: [DayGroup<Item>] = []
        var currentDay: Date?
        var currentItems: [Item] = []

        func flush() {
            guard let currentDay, !currentItems.isEmpty else { return }
            groups.append(
                DayGroup(title: dayHeading(currentDay, locale: locale), items: currentItems)
            )
            currentItems = []
        }

        for item in items {
            let date = TaskTiming.parseTimestamp(item[keyPath: timestamp])
            let day = date.map { calendar.startOfDay(for: $0) } ?? currentDay
            if let day, day != currentDay {
                flush()
                currentDay = day
            }
            currentItems.append(item)
        }
        flush()
        return groups
    }
}
