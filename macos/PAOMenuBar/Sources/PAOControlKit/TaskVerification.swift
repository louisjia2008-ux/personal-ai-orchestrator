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
    case verificationStale
    case workspaceUnavailable
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
        case "VERIFICATION_STALE": self = .verificationStale
        case "WORKSPACE_UNAVAILABLE": self = .workspaceUnavailable
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
        case .verificationStale: return "VERIFICATION_STALE"
        case .workspaceUnavailable: return "WORKSPACE_UNAVAILABLE"
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
        self == .verifiedEvidenceMissing || self == .verifiedEvidenceUnavailable || self == .workspaceUnavailable
    }
}

/// One verifier stage plus the exit code that decided it.
///
/// Preserve the host's bounded output so owners can inspect actual test results.
/// An exit code alone does not classify the cause of failure.
public struct TaskVerificationStage: Equatable, Identifiable, Sendable {
    public let name: String
    public let exitCode: Int
    public let stdout: String
    public let stderr: String
    public let argv: [String]

    public var id: String { name }
    public var passed: Bool { exitCode == 0 }

    public init(name: String, exitCode: Int, stdout: String = "", stderr: String = "",
                argv: [String] = []) {
        self.name = name
        self.exitCode = exitCode
        self.stdout = stdout
        self.stderr = stderr
        self.argv = argv
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
                TaskVerificationStage(name: $0.name, exitCode: $0.exitCode,
                                      stdout: $0.stdout ?? "", stderr: $0.stderr ?? "",
                                      argv: $0.argv ?? [])
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
