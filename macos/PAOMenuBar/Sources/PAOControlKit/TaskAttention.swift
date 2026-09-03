import Foundation

/// Why a task needs the owner.
///
/// Failure information used to sit below routing, progress and live telemetry,
/// so the one thing a stopped task has to say arrived last. This resolves the
/// reasons once, worst first, so Task Detail can lead with them.
///
/// Nothing here is inferred from a state that merely looks bad: every case is
/// backed by an authoritative field, and the accompanying text is the daemon's
/// own, verbatim.
public struct TaskAttention: Equatable, Identifiable, Sendable {

    public enum Kind: String, Equatable, Sendable {
        /// The verifier ran and rejected the work.
        case verificationFailed
        /// The task ended in FAILED.
        case failed
        /// The task is held in BLOCKED.
        case blocked
        /// The daemon reports a state this build does not recognize. Surfaced
        /// because an unrecognized state is not a safe state to assume about.
        case unknownState
        /// Verified, but the evidence for it cannot be produced.
        case verificationEvidenceUnavailable
        /// The verifier saw writes outside the profile's allowed paths.
        case unexpectedChanges
        /// A durable approval record is still PENDING.
        case awaitingApproval
    }

    public let kind: Kind
    /// Verbatim daemon text explaining the state, when the payload carries one.
    public let detail: String?

    public var id: String { kind.rawValue }

    public init(kind: Kind, detail: String? = nil) {
        self.kind = kind
        self.detail = detail
    }

    public var title: String { L10n.taskAttentionTitle(kind.rawValue) }

    /// Every reason the selected task needs attention, most severe first.
    ///
    /// Returns an array rather than one reason because a task can be blocked
    /// *and* awaiting an approval, and reporting only the first would hide the
    /// action the owner can actually take.
    public static func reasons(for detail: TaskDetailView) -> [TaskAttention] {
        var reasons: [TaskAttention] = []
        let verification = TaskVerificationSummary(report: detail.verification)
        let state = detail.task.state

        if verification.status.isWorkRejection {
            reasons.append(
                TaskAttention(
                    kind: .verificationFailed,
                    detail: verification.failureReason ?? lastTransition(detail)
                )
            )
        }
        if state == "FAILED" {
            reasons.append(
                TaskAttention(kind: .failed, detail: lastTransition(detail))
            )
        }
        if state == "BLOCKED" {
            reasons.append(
                TaskAttention(
                    kind: .blocked,
                    detail: verification.failureReason ?? lastTransition(detail)
                )
            )
        }
        if !TaskStates.isKnown(state) {
            reasons.append(TaskAttention(kind: .unknownState, detail: state))
        }
        if verification.status.isEvidenceProblem {
            reasons.append(
                TaskAttention(
                    kind: .verificationEvidenceUnavailable, detail: verification.rawStatus
                )
            )
        }
        let unexpected = TaskChangeSet.derive(from: detail).unexpectedFiles
        if !unexpected.isEmpty {
            reasons.append(
                TaskAttention(
                    kind: .unexpectedChanges,
                    detail: unexpected.map(\.path).joined(separator: "\n")
                )
            )
        }
        let pending = detail.approvals.approvals.filter { $0.status == "PENDING" }
        if !pending.isEmpty {
            reasons.append(
                TaskAttention(
                    kind: .awaitingApproval,
                    detail: pending.map(\.kind).joined(separator: ", ")
                )
            )
        }
        return reasons
    }

    /// The daemon's own sentence for the most recent state change. Already
    /// formatted by the control API ("VERIFYING -> BLOCKED: <reason>"), so it is
    /// shown verbatim rather than reassembled here.
    private static func lastTransition(_ detail: TaskDetailView) -> String? {
        detail.events
            .filter { $0.eventType == "TASK_STATE_CHANGED" }
            .max { $0.createdAt < $1.createdAt }?
            .summary
    }
}
