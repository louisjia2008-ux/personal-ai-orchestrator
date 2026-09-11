import Foundation

/// M1 WP5b: pure presentation model for the supervised-auto task surface.
///
/// Every displayed value is derived from authoritative ``TaskView``
/// truth plus a caller-supplied clock. The model performs no I/O and
/// mutates nothing: a locally expired countdown is a *presentation*
/// state that asks the store for an authoritative refresh — it never
/// claims the task transitioned to RUNNING (the daemon owns that).
public enum AutoSupervisionPhase: Equatable, Sendable {
    /// Not an AUTO lifecycle; the task surface renders nothing.
    case inactive
    /// ``AUTO_PLANNED``: planning is frozen, promotion to
    /// ``AUTO_GRACE`` is pending. Veto is allowed; no countdown exists.
    case planned
    /// ``AUTO_GRACE`` with no deadline yet: waiting for the owner's ACK.
    case waitingAck
    /// ``AUTO_GRACE`` with a future deadline: counting down.
    case countingDown
    /// The deadline passed locally before the daemon refreshed the
    /// task: dispatch confirmation is pending, never assumed.
    case expiredRefreshing
}

/// What the owner may do with one supervised-auto lifecycle right now.
///
/// All booleans are *presentation offers*. The daemon revalidates
/// every precondition (state, version, mode, project opt-in, frozen
/// decision, quota admission) when the action is actually submitted.
public struct AutoSupervisionPresentation: Equatable, Sendable {
    public let phase: AutoSupervisionPhase
    public let taskId: String
    /// Authoritative version at fetch time; actions re-fetch before
    /// submitting, so this value is never sent as-is.
    public let stateVersion: Int
    public let autoDecisionId: String?
    public let autoReason: String?
    public let ackedAt: Date?
    /// Daemon-published grace deadline, parsed with the shared
    /// timestamp parser. ``nil`` in ``planned`` / ``waitingAck``.
    public let deadline: Date?
    /// Whole seconds until the deadline, rounded up; ``nil`` when no
    /// deadline exists, ``0`` at and after the deadline — never negative.
    public let secondsRemaining: Int?
    /// Target frozen by the authoritative routing decision, supplied
    /// by the caller from the routing read model. ``nil`` means the
    /// daemon has no frozen target — displayed as unknown, never guessed.
    public let frozenTargetId: String?

    public var isExpiredLocally: Bool { phase == .expiredRefreshing }
    public var requiresAck: Bool { phase == .waitingAck }
    /// ACK begins the backend-owned deadline. Once a deadline exists
    /// the ACK control disappears entirely: it must never look like a
    /// way to reset or extend the countdown.
    public var canAck: Bool { phase == .waitingAck }
    public var canVeto: Bool {
        phase == .planned || phase == .waitingAck
            || phase == .countingDown || phase == .expiredRefreshing
    }
    /// Dispatch-now accelerates a *live* grace window. After local
    /// expiry the dispatch is already being confirmed by the daemon,
    /// so the accelerator is disabled rather than raced.
    public var canDispatchNow: Bool {
        phase == .waitingAck || phase == .countingDown
    }

    public static func derive(
        task: TaskView,
        now: Date,
        frozenTargetId: String? = nil
    ) -> AutoSupervisionPresentation {
        let deadline = task.autoGraceDeadlineAt.flatMap(TaskTiming.parseTimestamp)
        let ackedAt = task.autoAckedAt.flatMap(TaskTiming.parseTimestamp)
        let phase: AutoSupervisionPhase
        var secondsRemaining: Int? = nil
        switch task.state {
        case "AUTO_PLANNED":
            phase = .planned
        case "AUTO_GRACE":
            guard let deadline else {
                phase = .waitingAck
                break
            }
            let remaining = deadline.timeIntervalSince(now)
            if remaining > 0 {
                phase = .countingDown
                secondsRemaining = Int(remaining.rounded(.up))
            } else {
                phase = .expiredRefreshing
                secondsRemaining = 0
            }
        default:
            phase = .inactive
        }
        return AutoSupervisionPresentation(
            phase: phase,
            taskId: task.taskId,
            stateVersion: task.stateVersion,
            autoDecisionId: task.autoDecisionId,
            autoReason: task.autoReason,
            ackedAt: ackedAt,
            deadline: deadline,
            secondsRemaining: secondsRemaining,
            frozenTargetId: frozenTargetId
        )
    }

    /// ``m:ss`` under an hour, ``h:mm:ss`` above it. Monospaced digits
    /// keep the text from jittering as it ticks.
    public static func clockText(secondsRemaining: Int) -> String {
        let clamped = max(0, secondsRemaining)
        let hours = clamped / 3600
        let minutes = (clamped % 3600) / 60
        let seconds = clamped % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        }
        return String(format: "%d:%02d", minutes, seconds)
    }
}

/// Manual owner dispatch is offered only where its accepted backend
/// contract allows it: ``SUBMITTED`` / ``READY``. ``AUTO_PLANNED`` and
/// ``AUTO_GRACE`` use the dedicated supervised-auto surface instead —
/// a manual dispatch panel there would look like an ordinary action
/// path around the grace window.
public enum ManualDispatchPolicy {
    public static func isOffered(state: String) -> Bool {
        state == "SUBMITTED" || state == "READY"
    }
}

/// The menu-bar emergency stop is offered only while the global mode is
/// ``SUPERVISED_AUTO``. Its implementation is the existing daemon-owned
/// mode change to ``MANUAL`` — never a client-side kill switch.
public enum MenuBarAutoStop {
    public static func shouldOffer(currentMode: String?) -> Bool {
        currentMode == "SUPERVISED_AUTO"
    }
}

/// WP5b §17: owner-selectable automation modes. ``ACTIVE`` exists on
/// the wire and is displayed for truth, but it is deliberately absent
/// from the selectable set — production activation authority remains a
/// separate daemon-side gate, not an ordinary toggle in this app.
public enum AutomationModeCatalog {
    public static let selectable = ["MANUAL", "SUPERVISED_AUTO"]
    public static let displayOnly = ["ACTIVE"]
}
