import Foundation

/// Pure presentation model for the supervised-auto task surface.
///
/// Every displayed value is derived from authoritative `TaskView` truth plus a
/// caller-supplied clock. The model performs no I/O and mutates nothing: a
/// locally expired countdown asks the store for an authoritative refresh — it
/// never claims the task transitioned to RUNNING.
public enum AutoSupervisionPhase: Equatable, Sendable {
    case inactive
    case planned
    case waitingAck
    case countingDown
    case expiredRefreshing
}

/// What the owner may do with one supervised-auto lifecycle right now.
///
/// These booleans are presentation offers only. The daemon revalidates state,
/// version, mode, project opt-in, frozen decision and quota admission when an
/// action is submitted.
public struct AutoSupervisionPresentation: Equatable, Sendable {
    public let phase: AutoSupervisionPhase
    public let taskId: String
    public let stateVersion: Int
    public let autoDecisionId: String?
    public let autoReason: String?
    public let ackedAt: Date?
    public let deadline: Date?
    public let secondsRemaining: Int?
    public let frozenTargetId: String?

    public var isExpiredLocally: Bool { phase == .expiredRefreshing }
    public var requiresAck: Bool { phase == .waitingAck }
    public var canAck: Bool { phase == .waitingAck }
    public var canVeto: Bool {
        phase == .planned || phase == .waitingAck
            || phase == .countingDown || phase == .expiredRefreshing
    }
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

    /// `m:ss` under an hour, `h:mm:ss` above it. Monospaced digits keep the
    /// countdown stable as it ticks.
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

/// Manual owner dispatch is offered only where its backend contract allows it.
/// AUTO_PLANNED/AUTO_GRACE use the dedicated supervised-auto controls instead.
public enum ManualDispatchPolicy {
    public static func isOffered(state: String) -> Bool {
        state == "SUBMITTED" || state == "READY"
    }
}

/// The menu-bar emergency stop is only meaningful while supervised auto is
/// active. The action itself remains daemon-owned: mode is changed to MANUAL.
public enum MenuBarAutoStop {
    public static func shouldOffer(currentMode: String?) -> Bool {
        currentMode == "SUPERVISED_AUTO"
    }
}

/// Owner-selectable automation modes. ACTIVE is visible as truth but is never a
/// normal toggle because production activation is controlled by separate gates.
public enum AutomationModeCatalog {
    public static let selectable = ["MANUAL", "SUPERVISED_AUTO"]
    public static let displayOnly = ["ACTIVE"]
}

/// Honest mode truth for Settings. Missing daemon settings remain unknown rather
/// than being fabricated as MANUAL.
public enum AutomationModePresentation {
    public static func currentMode(from settings: SchedulingSettingsView?) -> String? {
        settings?.mode
    }

    public static func canSelectModes(currentMode: String?) -> Bool {
        currentMode != nil
    }
}
