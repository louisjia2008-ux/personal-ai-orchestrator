import Foundation

/// Where a task actually is in its execution, derived only from what the daemon
/// reports.
///
/// The surface this replaces derived nine "phases" from event presence and gave
/// two of them (`Editing`, `Testing`) the same boolean, so a task that had
/// started a worker was reported as having finished editing and testing. That is
/// a fabricated timeline. This model reports a stage only when a direct,
/// non-inferred fact backs it, and reports `notReached` otherwise.
///
/// There is deliberately no percentage anywhere in this file. Intra-stage
/// progress is BACKEND_WORK_REQUIRED (Phase A): the daemon publishes stage
/// transitions, not completion fractions, so any bar drawn here would be an
/// invention rather than a reading.

/// Lifecycle position of one stage.
public enum TaskLifecycleStatus: String, Equatable, Sendable {
    /// The stage happened. Backed by a record, not by a state guess.
    case reached
    /// The stage is where the work is right now.
    case inProgress
    /// No evidence the stage has happened. Not the same as "it will not".
    case notReached
    /// The stage ran and did not succeed.
    case failed
    /// The stage was ended deliberately before finishing.
    case stopped
    /// The daemon reported a state this build does not recognize. Never
    /// presented as progress in either direction.
    case unknown
}

/// One stage of the execution lifecycle.
public enum TaskLifecycleStage: String, CaseIterable, Identifiable, Sendable {
    case submitted
    case routing
    case workspace
    case execution
    case verification
    case completion

    public var id: String { rawValue }

    public var title: String { L10n.taskLifecycleStage(rawValue) }

    public var symbol: String {
        switch self {
        case .submitted: return "tray.and.arrow.down"
        case .routing: return "point.topleft.down.curvedto.point.bottomright.up"
        case .workspace: return "folder.badge.gearshape"
        case .execution: return "play.rectangle"
        case .verification: return "checkmark.seal"
        case .completion: return "flag.checkered"
        }
    }
}

/// A stage plus the evidence that placed it there.
public struct TaskLifecycleStep: Equatable, Identifiable, Sendable {
    public let stage: TaskLifecycleStage
    public let status: TaskLifecycleStatus
    /// Authoritative instant this stage is anchored to, when one exists.
    /// Absent means "no timestamp is recorded", never "now".
    public let at: String?
    /// Verbatim daemon detail for the stage, when one exists.
    public let detail: String?

    public var id: String { stage.rawValue }

    public init(
        stage: TaskLifecycleStage,
        status: TaskLifecycleStatus,
        at: String? = nil,
        detail: String? = nil
    ) {
        self.stage = stage
        self.status = status
        self.at = at
        self.detail = detail
    }
}

public enum TaskLifecycle {

    /// The lifecycle of one task, in order, from authoritative fields only.
    public static func steps(for detail: TaskDetailView) -> [TaskLifecycleStep] {
        [
            submitted(detail),
            routing(detail),
            workspace(detail),
            execution(detail),
            verification(detail),
            completion(detail),
        ]
    }

    /// The stage the work is at, if any stage reports itself as current.
    public static func currentStage(for detail: TaskDetailView) -> TaskLifecycleStage? {
        steps(for: detail).first { $0.status == .inProgress }?.stage
    }

    // MARK: - Stages

    private static func submitted(_ detail: TaskDetailView) -> TaskLifecycleStep {
        // The task record exists, so submission is a fact, and `created_at` is
        // the instant it happened.
        TaskLifecycleStep(stage: .submitted, status: .reached, at: detail.task.createdAt)
    }

    private static func routing(_ detail: TaskDetailView) -> TaskLifecycleStep {
        guard let summary = detail.routingSummary else {
            return TaskLifecycleStep(stage: .routing, status: .notReached)
        }
        // The newest decision across every declared role dates the stage; a plan
        // with no decisions yet is declared but not decided.
        let decidedAt = summary.roles
            .compactMap { $0.activeDecision?.createdAt }
            .max()
        let anyRunning = summary.roles.contains { $0.status == .running }
        let allPending = summary.roles.allSatisfy(\.isPending)
        if allPending {
            return TaskLifecycleStep(stage: .routing, status: .inProgress)
        }
        return TaskLifecycleStep(
            stage: .routing,
            status: anyRunning ? .inProgress : .reached,
            at: decidedAt
        )
    }

    private static func workspace(_ detail: TaskDetailView) -> TaskLifecycleStep {
        guard detail.workspace != nil else {
            // A registration event without a workspace record is still proof the
            // stage happened; the record may simply not be attached any more.
            if let event = event(detail, "WORKSPACE_REGISTERED") {
                return TaskLifecycleStep(
                    stage: .workspace, status: .reached, at: event.createdAt
                )
            }
            return TaskLifecycleStep(stage: .workspace, status: .notReached)
        }
        return TaskLifecycleStep(
            stage: .workspace,
            status: .reached,
            at: event(detail, "WORKSPACE_REGISTERED")?.createdAt
        )
    }

    private static func execution(_ detail: TaskDetailView) -> TaskLifecycleStep {
        guard let first = detail.runs.first else {
            return TaskLifecycleStep(stage: .execution, status: .notReached)
        }
        if detail.runs.contains(where: { $0.status == "RUNNING" }) {
            return TaskLifecycleStep(
                stage: .execution, status: .inProgress, at: first.startedAt
            )
        }
        let last = detail.runs.last
        // A non-zero exit is the run's own verdict, and it is reported as such
        // rather than folded into the task's state.
        let failed = detail.runs.contains { ($0.exitCode ?? 0) != 0 }
        return TaskLifecycleStep(
            stage: .execution,
            status: failed ? .failed : .reached,
            at: last?.finishedAt ?? first.startedAt,
            detail: last?.status
        )
    }

    private static func verification(_ detail: TaskDetailView) -> TaskLifecycleStep {
        let summary = TaskVerificationSummary(report: detail.verification)
        switch summary.status {
        case .notVerified:
            return TaskLifecycleStep(stage: .verification, status: .notReached)
        case .inProgress:
            return TaskLifecycleStep(stage: .verification, status: .inProgress)
        case .verified:
            return TaskLifecycleStep(stage: .verification, status: .reached)
        case .verifiedEvidenceMissing, .verifiedEvidenceUnavailable:
            // The work passed but the proof cannot be produced. That is not a
            // clean pass, and it is not a verification failure either.
            return TaskLifecycleStep(
                stage: .verification, status: .unknown, detail: summary.rawStatus
            )
        case .failedVerification:
            return TaskLifecycleStep(
                stage: .verification, status: .failed, detail: detail.verification.failureReason
            )
        case .unrecognized:
            return TaskLifecycleStep(
                stage: .verification, status: .unknown, detail: summary.rawStatus
            )
        }
    }

    private static func completion(_ detail: TaskDetailView) -> TaskLifecycleStep {
        let state = detail.task.state
        let at = detail.task.updatedAt
        switch state {
        case "COMPLETED", "VERIFIED":
            return TaskLifecycleStep(stage: .completion, status: .reached, at: at)
        case "FAILED", "BLOCKED":
            return TaskLifecycleStep(stage: .completion, status: .failed, at: at, detail: state)
        case "CANCELLED":
            return TaskLifecycleStep(stage: .completion, status: .stopped, at: at)
        default:
            guard TaskStates.isKnown(state) else {
                return TaskLifecycleStep(stage: .completion, status: .unknown, detail: state)
            }
            return TaskLifecycleStep(stage: .completion, status: .notReached)
        }
    }

    // MARK: - Helpers

    /// Newest event of a type. Absence proves nothing on its own — the task
    /// event list is bounded — so every caller pairs it with a direct record.
    private static func event(
        _ detail: TaskDetailView, _ type: String
    ) -> ActivityEventView? {
        detail.events.filter { $0.eventType == type }.max { $0.createdAt < $1.createdAt }
    }
}
