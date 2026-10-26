import Foundation

public enum TaskBaselineMode: String, Codable, CaseIterable, Sendable {
    case committedHead = "COMMITTED_HEAD"
    case workingTreeSnapshot = "WORKING_TREE_SNAPSHOT"

    public var title: String {
        L10n.workflow(self == .committedHead ? "baselineHead" : "baselineWorking")
    }
}

public struct ProjectBaselinePreview: Decodable, Equatable, Sendable {
    public let sourceHead: String
    public let sourceBranch: String
    public let fingerprint: String
    public let changedFileCount: Int
    public let untrackedFileCount: Int
    public let snapshotAvailable: Bool
    public let snapshotError: String?

    enum CodingKeys: String, CodingKey {
        case sourceHead = "source_head", sourceBranch = "source_branch", fingerprint
        case changedFileCount = "changed_file_count", untrackedFileCount = "untracked_file_count"
        case snapshotAvailable = "snapshot_available", snapshotError = "snapshot_error"
    }
}

public struct TaskBaselineReceipt: Decodable, Equatable, Sendable {
    public let mode: TaskBaselineMode
    public let sourceHead: String
    public let sourceBranch: String
    public let sourceFingerprint: String?
    public let baseSha: String
    public let snapshotRef: String?

    enum CodingKeys: String, CodingKey {
        case mode, sourceHead = "source_head", sourceBranch = "source_branch"
        case sourceFingerprint = "source_fingerprint", baseSha = "base_sha", snapshotRef = "snapshot_ref"
    }
}

public struct TaskBaselineSelection: Equatable, Sendable {
    public let projectId: String
    public let mode: TaskBaselineMode
    public let fingerprint: String

    public init(projectId: String, mode: TaskBaselineMode, fingerprint: String) {
        self.projectId = projectId
        self.mode = mode
        self.fingerprint = fingerprint
    }
}
