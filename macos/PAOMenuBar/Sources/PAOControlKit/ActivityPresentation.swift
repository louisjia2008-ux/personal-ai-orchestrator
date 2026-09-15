import Foundation

/// Presentation-only Activity grouping.
///
/// The daemon remains authoritative for event type and summary. This helper does
/// not reinterpret an event's outcome or severity; it only provides owner-facing
/// filters over the raw event type so Activity is easier to scan. Unknown event
/// types remain visible under `all` and are never discarded.
public enum ActivityCategory: String, CaseIterable, Identifiable, Sendable {
    case all
    case task
    case routing
    case quota
    case safety

    public var id: String { rawValue }
}

public enum ActivityPresentation {
    /// Best-effort product grouping from the daemon's raw event type.
    ///
    /// Order is intentional. Safety and quota terms outrank task identity, and
    /// routing terms outrank the generic task bucket, because a dispatch event
    /// may also carry a task id but is more useful under Routing.
    public static func category(eventType: String, taskId: String?) -> ActivityCategory? {
        let value = eventType.lowercased()

        if containsAny(
            value,
            [
                "safety", "permission", "approval", "veto", "cancel", "guard",
                "gate", "blocked", "violation", "denied", "emergency", "lock",
            ]
        ) {
            return .safety
        }

        if containsAny(
            value,
            [
                "quota", "usage", "limit", "budget", "reserve", "scarcity",
                "burn", "exhaust", "cooldown", "capacity",
            ]
        ) {
            return .quota
        }

        if containsAny(
            value,
            [
                "routing", "route", "dispatch", "decision", "provider", "target",
                "worker", "delegate", "handoff", "candidate",
            ]
        ) {
            return .routing
        }

        if taskId != nil || containsAny(
            value,
            [
                "task", "run", "verify", "workspace", "submit", "complete",
                "change", "result", "execution",
            ]
        ) {
            return .task
        }

        return nil
    }

    public static func matches(
        eventType: String,
        taskId: String?,
        category: ActivityCategory
    ) -> Bool {
        category == .all || self.category(eventType: eventType, taskId: taskId) == category
    }

    /// Case-insensitive search over owner-readable summary, raw event type and
    /// task id. Search is presentation only and never changes event ordering.
    public static func matchesQuery(
        summary: String,
        eventType: String,
        taskId: String?,
        query: String
    ) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return true }

        return summary.localizedCaseInsensitiveContains(needle)
            || eventType.localizedCaseInsensitiveContains(needle)
            || (taskId?.localizedCaseInsensitiveContains(needle) ?? false)
    }

    private static func containsAny(_ value: String, _ needles: [String]) -> Bool {
        needles.contains { value.contains($0) }
    }
}
