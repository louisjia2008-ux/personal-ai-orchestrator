import Foundation

/// Timestamp parsing and elapsed-time semantics for tasks.
///
/// Lives here rather than in the view layer because "how long has this task been
/// running" is a question about task state, not about presentation, and it has to
/// be testable without the app executable.
public enum TaskTiming {

    /// States in which a task is no longer progressing.
    ///
    /// For these, `updated_at` is the moment work stopped, so elapsed time is the
    /// span between submission and that moment. For every other state the task is
    /// still accruing time and the span runs to now.
    public static let terminalStates: Set<String> = [
        "VERIFIED", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED",
    ]

    public static func isTerminal(state: String) -> Bool {
        terminalStates.contains(state)
    }

    /// Parses an API timestamp, with or without fractional seconds.
    public static func parseTimestamp(_ value: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: value) {
            return date
        }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: value)
    }

    /// How long a task has been running.
    ///
    /// A live task's elapsed time is measured to `now`, not to `updated_at`.
    /// Measuring to `updated_at` reports the time since the last *state change*,
    /// which for a running task stops advancing the moment it enters RUNNING — the
    /// reading then sits still while the work continues, which is the opposite of
    /// what an elapsed timer is for.
    ///
    /// Returns nil when a timestamp cannot be parsed, so callers show "unknown"
    /// rather than a fabricated zero.
    public static func elapsed(
        createdAt: String,
        updatedAt: String,
        state: String,
        now: Date = Date()
    ) -> TimeInterval? {
        guard let start = parseTimestamp(createdAt) else { return nil }
        let end: Date
        if isTerminal(state: state) {
            guard let finished = parseTimestamp(updatedAt) else { return nil }
            end = finished
        } else {
            end = now
        }
        // Clock skew between daemon and client must not produce a negative age.
        return max(0, end.timeIntervalSince(start))
    }

    public static func elapsed(task: TaskView, now: Date = Date()) -> TimeInterval? {
        elapsed(
            createdAt: task.createdAt,
            updatedAt: task.updatedAt,
            state: task.state,
            now: now
        )
    }
}
