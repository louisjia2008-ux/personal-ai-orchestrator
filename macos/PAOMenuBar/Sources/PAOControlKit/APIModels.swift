import Foundation

/// Typed decoding of the Personal AI Orchestrator `/v1` control-plane JSON surface.
///
/// Models mirror the Python pydantic view models exactly (snake_case keys). Unknown JSON
/// fields are ignored by design: the daemon is authoritative and the client must never
/// surface fields outside the documented contract (in particular credential-like fields).
public enum APIVersion {
    public static let v1 = "v1"
}

public struct HealthView: Decodable, Equatable, Sendable {
    public let status: String
    public let apiVersion: String

    enum CodingKeys: String, CodingKey {
        case status
        case apiVersion = "api_version"
    }

    public var isCompatible: Bool { apiVersion == APIVersion.v1 }
}

public struct TaskView: Decodable, Equatable, Identifiable, Sendable {
    public let taskId: String
    public let requestId: String
    public let intent: String
    public let state: String
    public let stateVersion: Int
    public let createdAt: String
    public let updatedAt: String

    public var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case requestId = "request_id"
        case intent
        case state
        case stateVersion = "state_version"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct TaskListView: Decodable, Equatable, Sendable {
    public let tasks: [TaskView]
    public let total: Int
}

public struct CancelView: Decodable, Equatable, Sendable {
    public let task: TaskView
    public let cancelledNow: Bool

    enum CodingKeys: String, CodingKey {
        case task
        case cancelledNow = "cancelled_now"
    }
}

public struct RunView: Decodable, Equatable, Identifiable, Sendable {
    public let runId: String
    public let taskId: String
    public let workerId: String
    public let pid: Int?
    public let status: String
    public let startedAt: String
    public let finishedAt: String?

    public var id: String { runId }

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case taskId = "task_id"
        case workerId = "worker_id"
        case pid
        case status
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

public struct RunListView: Decodable, Equatable, Sendable {
    public let runs: [RunView]
}

public struct ApprovalView: Decodable, Equatable, Identifiable, Sendable {
    public let approvalId: String
    public let taskId: String
    public let kind: String
    public let status: String
    public let createdAt: String
    public let resolvedAt: String?

    public var id: String { approvalId }

    enum CodingKeys: String, CodingKey {
        case approvalId = "approval_id"
        case taskId = "task_id"
        case kind
        case status
        case createdAt = "created_at"
        case resolvedAt = "resolved_at"
    }
}

public struct ApprovalListView: Decodable, Equatable, Sendable {
    public let approvals: [ApprovalView]
}

public struct ObservedAvailabilityView: Codable, Equatable, Sendable {
    public let state: String
    public let observedAt: String
    public let measurementSource: String
    public let confidence: String
    public let sanitizedReasonCode: String?

    enum CodingKeys: String, CodingKey {
        case state
        case observedAt = "observed_at"
        case measurementSource = "measurement_source"
        case confidence
        case sanitizedReasonCode = "sanitized_reason_code"
    }
}

public struct ExecutionTargetHealthView: Codable, Equatable, Identifiable, Sendable {
    public let executionTargetId: String
    public let modelSkuId: String
    public let runtimeId: String
    public let enabled: Bool
    public let executionVerified: Bool?
    public let runtimeAvailable: Bool?
    public let observedAvailability: ObservedAvailabilityView?

    public var id: String { executionTargetId }
    public var isExecutionVerified: Bool { executionVerified ?? false }

    enum CodingKeys: String, CodingKey {
        case executionTargetId = "execution_target_id"
        case modelSkuId = "model_sku_id"
        case runtimeId = "runtime_id"
        case enabled
        case executionVerified = "execution_verified"
        case runtimeAvailable = "runtime_available"
        case observedAvailability = "observed_availability"
    }
}

public struct QuotaWindowHealthView: Codable, Equatable, Sendable {
    public let windowId: String
    public let windowKind: String
    public let state: String
    public let confidence: String
    public let remainingFraction: Double?
    public let resetAt: String?

    enum CodingKeys: String, CodingKey {
        case windowId = "window_id"
        case windowKind = "window_kind"
        case state
        case confidence
        case remainingFraction = "remaining_fraction"
        case resetAt = "reset_at"
    }
}

public struct QuotaPoolHealthView: Codable, Equatable, Identifiable, Sendable {
    public let quotaPoolId: String
    public let name: String
    public let planId: String
    public let state: String
    public let confidence: String
    public let measurementSourceType: String
    public let observedAt: String?
    public let windows: [QuotaWindowHealthView]

    public var id: String { quotaPoolId }

    enum CodingKeys: String, CodingKey {
        case quotaPoolId = "quota_pool_id"
        case name
        case planId = "plan_id"
        case state
        case confidence
        case measurementSourceType = "measurement_source_type"
        case observedAt = "observed_at"
        case windows
    }
}

public struct ProviderHealthView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let accountCount: Int
    public let quotaPools: [QuotaPoolHealthView]
    public let executionTargets: [ExecutionTargetHealthView]
    public let evidenceSource: String?
    public let authStatus: String?
    public let executionStatus: String?
    public let lastChecked: String?

    public var id: String { providerId }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case accountCount = "account_count"
        case quotaPools = "quota_pools"
        case executionTargets = "execution_targets"
        case evidenceSource = "evidence_source"
        case authStatus = "auth_status"
        case executionStatus = "execution_status"
        case lastChecked = "last_checked"
    }

    public init(
        providerId: String,
        displayName: String,
        accountCount: Int,
        quotaPools: [QuotaPoolHealthView] = [],
        executionTargets: [ExecutionTargetHealthView] = [],
        evidenceSource: String? = nil,
        authStatus: String? = nil,
        executionStatus: String? = nil,
        lastChecked: String? = nil
    ) {
        self.providerId = providerId
        self.displayName = displayName
        self.accountCount = accountCount
        self.quotaPools = quotaPools
        self.executionTargets = executionTargets
        self.evidenceSource = evidenceSource
        self.authStatus = authStatus
        self.executionStatus = executionStatus
        self.lastChecked = lastChecked
    }
}

public struct ProviderHealthListView: Codable, Equatable, Sendable {
    public let providers: [ProviderHealthView]

    public init(providers: [ProviderHealthView]) {
        self.providers = providers
    }
}

/// Provider-discovery status projected by the product runtime.
///
/// `discoveryState` mirrors the daemon's discovery lifecycle:
///   PENDING      – product just launched, discovery has not yet completed
///   DISCOVERED   – at least one provider family is currently registered
///   EMPTY        – discovery ran and found nothing (e.g. no auth configured)
///   FAILED       – discovery errored; `lastErrorCode` describes the cause
///
/// `lastDiscoveredAt` is the ISO-8601 timestamp of the most recent
/// successful discovery cycle. It is informational only.
public struct ProviderDiscoveryStatusView: Codable, Equatable, Sendable {
    public let discoveryState: String
    public let lastDiscoveredAt: String?
    public let providerCount: Int
    public let executionTargetCount: Int
    public let lastErrorCode: String?
    public let catalogSnapshotId: String?
    public let sourceMethod: String?

    enum CodingKeys: String, CodingKey {
        case discoveryState = "discovery_state"
        case lastDiscoveredAt = "last_discovered_at"
        case providerCount = "provider_count"
        case executionTargetCount = "execution_target_count"
        case lastErrorCode = "last_error_code"
        case catalogSnapshotId = "catalog_snapshot_id"
        case sourceMethod = "source_method"
    }

    public init(
        discoveryState: String,
        lastDiscoveredAt: String? = nil,
        providerCount: Int = 0,
        executionTargetCount: Int = 0,
        lastErrorCode: String? = nil,
        catalogSnapshotId: String? = nil,
        sourceMethod: String? = nil
    ) {
        self.discoveryState = discoveryState
        self.lastDiscoveredAt = lastDiscoveredAt
        self.providerCount = providerCount
        self.executionTargetCount = executionTargetCount
        self.lastErrorCode = lastErrorCode
        self.catalogSnapshotId = catalogSnapshotId
        self.sourceMethod = sourceMethod
    }
}

public struct ActiveStatusView: Decodable, Equatable, Sendable {
    public let productionActive: String
    public let authorized: Bool
    public let blockingReasons: [String]
    public let gate: [String: Bool]

    enum CodingKeys: String, CodingKey {
        case productionActive = "production_active"
        case authorized
        case blockingReasons = "blocking_reasons"
        case gate
    }
}

public struct VerificationReportView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let taskState: String
    public let status: String
    public let evidenceId: String?
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case taskState = "task_state"
        case status
        case evidenceId = "evidence_id"
        case failureReason = "failure_reason"
    }
}

public struct ActivityEventView: Decodable, Equatable, Identifiable, Sendable {
    public let eventType: String
    public let taskId: String?
    public let createdAt: String
    public let summary: String

    public var id: String { "\(createdAt)-\(eventType)-\(taskId ?? "system")" }

    enum CodingKeys: String, CodingKey {
        case eventType = "event_type"
        case taskId = "task_id"
        case createdAt = "created_at"
        case summary
    }
}

public struct DashboardCountsView: Decodable, Equatable, Sendable {
    public let running: Int
    public let ready: Int
    public let blocked: Int
    public let verified: Int
    public let completed: Int
    public let total: Int
}

public struct DashboardSummaryView: Decodable, Equatable, Sendable {
    public let connection: HealthView
    public let counts: DashboardCountsView
    public let recentTasks: [TaskView]
    public let providers: ProviderHealthListView
    public let activeStatus: ActiveStatusView
    public let importantBlockers: [String]
    public let recentEvents: [ActivityEventView]

    enum CodingKeys: String, CodingKey {
        case connection
        case counts
        case recentTasks = "recent_tasks"
        case providers
        case activeStatus = "active_status"
        case importantBlockers = "important_blockers"
        case recentEvents = "recent_events"
    }
}

public struct RoutingDecisionView: Decodable, Equatable, Sendable {
    public let taskId: String?
    public let decisionId: String
    public let requestId: String
    public let createdAt: String
    public let decision: [String: StringValue]

    /// Minimal JSON-value wrapper: routing decisions carry heterogeneous payloads and the
    /// client only renders selected known string fields.
    public struct StringValue: Decodable, Equatable, Sendable {
        public let value: String?

        public init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let string = try? container.decode(String.self) {
                value = string
            } else {
                value = nil
            }
        }
    }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case decisionId = "decision_id"
        case requestId = "request_id"
        case createdAt = "created_at"
        case decision
    }

    public var mode: String? { decision["mode"]?.value }
    public var selectedExecutionTargetId: String? {
        decision["selected_execution_target_id"]?.value
    }
    public var fallbackReason: String? { decision["fallback_reason"]?.value }
}

public struct WorkspaceView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let repoPath: String
    public let worktreePath: String
    public let branch: String
    public let baseSha: String
    public let writerLocked: Bool

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case repoPath = "repo_path"
        case worktreePath = "worktree_path"
        case branch
        case baseSha = "base_sha"
        case writerLocked = "writer_locked"
    }
}

public struct TaskDetailView: Decodable, Equatable, Sendable {
    public let task: TaskView
    public let runs: [RunView]
    public let routing: RoutingDecisionView?
    public let verification: VerificationReportView
    public let approvals: ApprovalListView
    public let workspace: WorkspaceView?
    public let events: [ActivityEventView]
}

public struct SubmitRequest: Encodable, Equatable, Sendable {
    public let taskId: String
    public let requestId: String
    public let intent: String

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case requestId = "request_id"
        case intent
    }

    public init(taskId: String, requestId: String, intent: String) {
        self.taskId = taskId
        self.requestId = requestId
        self.intent = intent
    }
}

public struct DispatchRequest: Encodable, Equatable, Sendable {
    public let requestId: String
    public let taskStateVersion: Int
    public let executionTargetId: String

    enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case taskStateVersion = "task_state_version"
        case executionTargetId = "execution_target_id"
    }

    public init(requestId: String, taskStateVersion: Int, executionTargetId: String) {
        self.requestId = requestId
        self.taskStateVersion = taskStateVersion
        self.executionTargetId = executionTargetId
    }
}

public struct DispatchTaskView: Decodable, Equatable, Sendable {
    public let dispatchId: String
    public let task: TaskView
    public let requestId: String
    public let authority: String
    public let executionTargetId: String
    public let status: String
    public let accepted: Bool
    public let reason: String?
    public let failureCode: String?

    enum CodingKeys: String, CodingKey {
        case dispatchId = "dispatch_id"
        case task
        case requestId = "request_id"
        case authority
        case executionTargetId = "execution_target_id"
        case status
        case accepted
        case reason
        case failureCode = "failure_code"
    }
}

public struct OwnerExecutionSettingsView: Decodable, Equatable, Sendable {
    public let ownerInitiatedExecutionEnabled: Bool
    public let productionActive: String

    enum CodingKeys: String, CodingKey {
        case ownerInitiatedExecutionEnabled = "owner_initiated_execution_enabled"
        case productionActive = "production_active"
    }
}

public struct OwnerExecutionSettingsUpdateRequest: Encodable, Equatable, Sendable {
    public let ownerInitiatedExecutionEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case ownerInitiatedExecutionEnabled = "owner_initiated_execution_enabled"
    }

    public init(ownerInitiatedExecutionEnabled: Bool) {
        self.ownerInitiatedExecutionEnabled = ownerInitiatedExecutionEnabled
    }
}
