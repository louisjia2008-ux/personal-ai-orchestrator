import Foundation

/// Optimistic concurrency token captured with the settings the owner reviewed.
/// It is never replaced just before a write, including after a 409 conflict.
public struct PolicyRevisionBinding: Equatable, Sendable {
    public let revision: String
    public let storeId: String
    public let processEpoch: String
    public var isValid: Bool {
        [revision, storeId, processEpoch].allSatisfy { value in
            value.count == 32 && value.unicodeScalars.allSatisfy { (48...57).contains($0.value) || (97...102).contains($0.value) }
        }
    }
    public init(revision: String, storeId: String, processEpoch: String) {
        self.revision = revision
        self.storeId = storeId
        self.processEpoch = processEpoch
    }
}

public struct ProjectSchedulingView: Decodable, Equatable, Sendable {
    public let projectId: String
    public let revision: String
    public let schedulingPolicy: String?
    public let manualExecutionTargetId: String?
    public let effectiveSchedulingPolicy: String
    public let effectiveManualExecutionTargetId: String?
    public let policySource: String
    public let globalRevision: String
    public let storeId: String
    public let processEpoch: String
    public var revisionBinding: PolicyRevisionBinding {
        PolicyRevisionBinding(revision: revision, storeId: storeId, processEpoch: processEpoch)
    }
    enum CodingKeys: String, CodingKey {
        case projectId = "project_id", revision, schedulingPolicy = "scheduling_policy"
        case manualExecutionTargetId = "manual_execution_target_id"
        case effectiveSchedulingPolicy = "effective_scheduling_policy"
        case effectiveManualExecutionTargetId = "effective_manual_execution_target_id"
        case policySource = "policy_source", globalRevision = "global_revision"
        case storeId = "store_id", processEpoch = "process_epoch"
    }
}

public extension PAOControlClient {
    func projectScheduling(_ projectId: String) async throws -> ProjectSchedulingView {
        let result: ProjectSchedulingView = try await perform(
            method: "GET", path: "/v1/projects/\(projectId)/scheduling", encodedBody: nil
        )
        guard result.projectId == projectId, result.revisionBinding.isValid else {
            throw PAOClientError.malformedResponse
        }
        return result
    }
}
