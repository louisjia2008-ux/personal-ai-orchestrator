import Foundation

/// Normalized verification state for Task Detail.
///
/// The daemon reports six distinct verification statuses. The client previously
/// matched on `"VERIFIED"` and `"FAILED"` only, so `FAILED_VERIFICATION` —
/// the one status that means the work was rejected — fell through to the
/// unknown branch and rendered with a question mark, while
/// `VERIFIED_EVIDENCE_MISSING` did the same. This type names each one so the
/// distinctions the model makes survive into the UI.
///
/// The crucial pair is `failedVerification` versus the evidence statuses. A
/// verifier that ran and failed the work is a verdict about the work; a verifier
/// whose evidence cannot be produced is a fact about the infrastructure. They
/// must not read alike.
public enum TaskVerificationStatus: Equatable, Sendable {
    /// Verified, with retrievable evidence.
    case verified
    /// The task reached VERIFIED but no evidence id was ever recorded.
    case verifiedEvidenceMissing
    /// An evidence id exists but the journal could not return the record.
    case verifiedEvidenceUnavailable
    /// The verifier ran and rejected the work.
    case failedVerification
    /// Verification is running now.
    case inProgress
    /// Verification has not run.
    case notVerified
    /// A status this build does not know. Never presented as a pass.
    case unrecognized(String)

    public init(rawValue: String) {
        switch rawValue {
        case "VERIFIED": self = .verified
        case "VERIFIED_EVIDENCE_MISSING": self = .verifiedEvidenceMissing
        case "VERIFIED_EVIDENCE_UNAVAILABLE": self = .verifiedEvidenceUnavailable
        case "FAILED_VERIFICATION": self = .failedVerification
        case "IN_PROGRESS": self = .inProgress
        case "NOT_VERIFIED": self = .notVerified
        default: self = .unrecognized(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .verified: return "VERIFIED"
        case .verifiedEvidenceMissing: return "VERIFIED_EVIDENCE_MISSING"
        case .verifiedEvidenceUnavailable: return "VERIFIED_EVIDENCE_UNAVAILABLE"
        case .failedVerification: return "FAILED_VERIFICATION"
        case .inProgress: return "IN_PROGRESS"
        case .notVerified: return "NOT_VERIFIED"
        case .unrecognized(let raw): return raw
        }
    }

    public var isKnown: Bool {
        if case .unrecognized = self { return false }
        return true
    }

    /// True only for a rejection of the work by a verifier that actually ran.
    public var isWorkRejection: Bool { self == .failedVerification }

    /// True when the verifier's verdict cannot be evidenced. Distinct from a
    /// rejection: nothing is known to be wrong with the work itself.
    public var isEvidenceProblem: Bool {
        self == .verifiedEvidenceMissing || self == .verifiedEvidenceUnavailable
    }
}

/// One verifier stage plus the exit code that decided it.
///
/// A stage carries a name and a return code and nothing else. Whether a non-zero
/// exit means "the tests found a defect" or "the tool could not start" is not
/// distinguishable from the payload, so the client does not claim to know:
/// per-stage failure classification is BACKEND_WORK_REQUIRED.
public struct TaskVerificationStage: Equatable, Identifiable, Sendable {
    public let name: String
    public let exitCode: Int

    public var id: String { name }
    public var passed: Bool { exitCode == 0 }

    public init(name: String, exitCode: Int) {
        self.name = name
        self.exitCode = exitCode
    }
}

/// Everything Task Detail needs to render verification, resolved once.
public struct TaskVerificationSummary: Equatable, Sendable {
    public let status: TaskVerificationStatus
    /// The machine value exactly as the daemon sent it.
    public let rawStatus: String
    public let profile: String?
    /// The verifier's own overall verdict, when a result was retrieved. Kept
    /// separate from `status`: the report status describes the task's
    /// verification lifecycle, `resultPassed` describes the run.
    public let resultPassed: Bool?
    public let stages: [TaskVerificationStage]
    public let evidenceId: String?
    public let failureReason: String?

    public init(report: VerificationReportView) {
        self.status = TaskVerificationStatus(rawValue: report.status)
        self.rawStatus = report.status
        self.profile = report.result?.profile
        self.resultPassed = report.result?.passed
        self.stages =
            report.result?.stages.map {
                TaskVerificationStage(name: $0.name, exitCode: $0.exitCode)
            } ?? []
        self.evidenceId = report.evidenceId ?? report.result?.evidenceId
        self.failureReason = report.failureReason ?? report.result?.failureReason
    }

    /// True when no verifier result is attached, whatever the reason.
    public var hasResult: Bool { resultPassed != nil }

    public var failedStages: [TaskVerificationStage] {
        stages.filter { !$0.passed }
    }
}
