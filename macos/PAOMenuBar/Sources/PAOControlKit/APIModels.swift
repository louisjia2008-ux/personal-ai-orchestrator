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
    public let projectId: String?
    public let baseSha: String?
    public let workingSubpath: String?
    public let state: String
    public let stateVersion: Int
    public let createdAt: String
    public let updatedAt: String

    public var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case requestId = "request_id"
        case intent
        case projectId = "project_id"
        case baseSha = "base_sha"
        case workingSubpath = "working_subpath"
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

public struct ProjectView: Decodable, Equatable, Identifiable, Sendable {
    public let projectId: String
    public let displayName: String
    public let canonicalRepoRoot: String
    public let gitRoot: String
    public let defaultBranch: String
    public let lastKnownHead: String
    public let createdAt: String
    public let updatedAt: String
    public let workingSubpath: String?
    public let remoteUrl: String?
    public let lastOpenedAt: String?
    public let storageAvailability: String
    public let recentTaskCount: Int
    public let currentBranch: String?

    public var id: String { projectId }
    public var isOnline: Bool { storageAvailability == "ONLINE" }

    enum CodingKeys: String, CodingKey {
        case projectId = "project_id"
        case displayName = "display_name"
        case canonicalRepoRoot = "canonical_repo_root"
        case gitRoot = "git_root"
        case defaultBranch = "default_branch"
        case lastKnownHead = "last_known_head"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case workingSubpath = "working_subpath"
        case remoteUrl = "remote_url"
        case lastOpenedAt = "last_opened_at"
        case storageAvailability = "storage_availability"
        case recentTaskCount = "recent_task_count"
        case currentBranch = "current_branch"
    }
}

public struct ProjectListView: Decodable, Equatable, Sendable {
    public let projects: [ProjectView]
}

public struct ProjectRemoveView: Decodable, Equatable, Sendable {
    public let project: ProjectView
    public let removedFromOrchestrator: Bool

    enum CodingKeys: String, CodingKey {
        case project
        case removedFromOrchestrator = "removed_from_orchestrator"
    }
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
    public let result: [String: RunResultValue]?

    public var id: String { runId }

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case taskId = "task_id"
        case workerId = "worker_id"
        case pid
        case status
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case result
    }

    public var exitCode: Int? { result?["exit_code"]?.intValue }
    public var stdoutBytes: Int? { result?["stdout_bytes"]?.intValue }
    public var stderrBytes: Int? { result?["stderr_bytes"]?.intValue }
    public var stdoutSHA256: String? { result?["stdout_sha256"]?.stringValue }
    public var stderrSHA256: String? { result?["stderr_sha256"]?.stringValue }
    public var outputTruncated: Bool? { result?["output_truncated"]?.boolValue }
    public var timedOut: Bool? { result?["timed_out"]?.boolValue }
}

public struct RunListView: Decodable, Equatable, Sendable {
    public let runs: [RunView]
}

public struct RunResultValue: Decodable, Equatable, Sendable {
    public let stringValue: String?
    public let intValue: Int?
    public let boolValue: Bool?

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) {
            stringValue = string
            intValue = nil
            boolValue = nil
        } else if let int = try? container.decode(Int.self) {
            stringValue = nil
            intValue = int
            boolValue = nil
        } else if let bool = try? container.decode(Bool.self) {
            stringValue = nil
            intValue = nil
            boolValue = bool
        } else {
            stringValue = nil
            intValue = nil
            boolValue = nil
        }
    }
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
    public let connectionState: String?
    public let authState: String?
    public let runtimeState: String?
    public let planSurface: String?
    public let region: String?
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
        case connectionState = "connection_state"
        case authState = "auth_state"
        case runtimeState = "runtime_state"
        case planSurface = "plan_surface"
        case region
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
        connectionState: String? = nil,
        authState: String? = nil,
        runtimeState: String? = nil,
        planSurface: String? = nil,
        region: String? = nil,
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
        self.connectionState = connectionState
        self.authState = authState
        self.runtimeState = runtimeState
        self.planSurface = planSurface
        self.region = region
        self.lastChecked = lastChecked
    }
}

public struct ProviderHealthListView: Codable, Equatable, Sendable {
    public let providers: [ProviderHealthView]

    public init(providers: [ProviderHealthView]) {
        self.providers = providers
    }
}

public struct ProviderConnectionView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let connectionState: String
    public let authState: String
    public let executionVerified: Bool
    public let runtimeState: String
    public let credentialReferenceType: String
    public let region: String?
    public let planSurface: String?
    public let modelSkus: [String]
    public let connectedAt: String?
    public let lastValidatedAt: String?
    public let lastReasonCode: String?

    public var id: String { providerId }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case connectionState = "connection_state"
        case authState = "auth_state"
        case executionVerified = "execution_verified"
        case runtimeState = "runtime_state"
        case credentialReferenceType = "credential_reference_type"
        case region
        case planSurface = "plan_surface"
        case modelSkus = "model_skus"
        case connectedAt = "connected_at"
        case lastValidatedAt = "last_validated_at"
        case lastReasonCode = "last_reason_code"
    }
}

public struct AvailableProviderView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let connectionState: String
    public let authState: String
    public let executionVerified: Bool
    public let runtimeState: String
    public let region: String?
    public let planSurface: String?
    public let modelSkus: [String]
    public let lastChecked: String?

    public var id: String { providerId }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case connectionState = "connection_state"
        case authState = "auth_state"
        case executionVerified = "execution_verified"
        case runtimeState = "runtime_state"
        case region
        case planSurface = "plan_surface"
        case modelSkus = "model_skus"
        case lastChecked = "last_checked"
    }
}

/// One sanitized reason a historical provider surface may be offered for import.
/// Carries a human-readable detail only — never a credential value or path.
public struct ImportEvidenceItemView: Codable, Equatable, Identifiable, Sendable {
    public let kind: String
    public let detail: String
    public let observedAt: String?

    public var id: String { "\(kind)-\(detail)" }

    enum CodingKeys: String, CodingKey {
        case kind
        case detail
        case observedAt = "observed_at"
    }
}

/// A surface the owner plausibly already uses, offered for one-click import.
/// Being listed here grants no scheduling eligibility until the owner imports it.
public struct ProviderImportCandidateView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let region: String?
    public let planSurface: String?
    public let modelSkus: [String]
    public let executionVerified: Bool
    public let authState: String
    public let credentialReferenceType: String
    public let evidence: [ImportEvidenceItemView]

    public var id: String { providerId }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case region
        case planSurface = "plan_surface"
        case modelSkus = "model_skus"
        case executionVerified = "execution_verified"
        case authState = "auth_state"
        case credentialReferenceType = "credential_reference_type"
        case evidence
    }
}

public struct ProviderConnectionListView: Codable, Equatable, Sendable {
    public let connected: [ProviderConnectionView]
    public let availableToAdd: [AvailableProviderView]
    public let importCandidates: [ProviderImportCandidateView]

    enum CodingKeys: String, CodingKey {
        case connected
        case availableToAdd = "available_to_add"
        case importCandidates = "import_candidates"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        connected = try container.decode([ProviderConnectionView].self, forKey: .connected)
        availableToAdd = try container.decode([AvailableProviderView].self, forKey: .availableToAdd)
        // Tolerated as absent so a newer App still reads an older daemon's payload.
        importCandidates = try container.decodeIfPresent(
            [ProviderImportCandidateView].self,
            forKey: .importCandidates
        ) ?? []
    }
}

public struct ImportConnectionsView: Codable, Equatable, Sendable {
    public let imported: [ProviderConnectionView]
}

public struct SchedulingSettingsView: Codable, Equatable, Sendable {
    public let defaultSchedulingPolicy: String
    public let selectablePolicies: [String]

    enum CodingKeys: String, CodingKey {
        case defaultSchedulingPolicy = "default_scheduling_policy"
        case selectablePolicies = "selectable_policies"
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
    public let result: VerificationResultView?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case taskState = "task_state"
        case status
        case evidenceId = "evidence_id"
        case failureReason = "failure_reason"
        case result
    }
}

public struct VerificationResultView: Decodable, Equatable, Sendable {
    public let profile: String
    public let passed: Bool
    public let changedPaths: [String]
    public let unexpectedPaths: [String]
    public let stages: [VerificationStageView]
    public let evidenceId: String?
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case profile
        case passed
        case changedPaths = "changed_paths"
        case unexpectedPaths = "unexpected_paths"
        case stages
        case evidenceId = "evidence_id"
        case failureReason = "failure_reason"
    }
}

public struct VerificationStageView: Decodable, Equatable, Identifiable, Sendable {
    public let name: String
    public let exitCode: Int

    public var id: String { name }
    public var passed: Bool { exitCode == 0 }

    enum CodingKeys: String, CodingKey {
        case name
        case exitCode = "returncode"
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
    public let verifying: Int
    public let verified: Int
    public let completed: Int
    public let total: Int
}

public struct TaskTrendBucketView: Decodable, Equatable, Identifiable, Sendable {
    public let bucketStart: String
    public let submitted: Int
    public let completed: Int
    public let blocked: Int

    public var id: String { bucketStart }

    enum CodingKeys: String, CodingKey {
        case bucketStart = "bucket_start"
        case submitted
        case completed
        case blocked
    }
}

public struct TaskStateSliceView: Decodable, Equatable, Identifiable, Sendable {
    public let state: String
    public let count: Int

    public var id: String { state }
}

public struct DashboardBasicInfoView: Decodable, Equatable, Sendable {
    public let daemonConnection: String
    public let registeredProjects: Int
    public let discoveredProviders: Int
    public let availableExecutionTargets: Int
    public let runningTasks: Int
    public let tasksToday: Int
    public let routingDecisionsToday: Int
    public let quotaWarningCount: Int
    public let lastRefreshSync: String

    enum CodingKeys: String, CodingKey {
        case daemonConnection = "daemon_connection"
        case registeredProjects = "registered_projects"
        case discoveredProviders = "discovered_providers"
        case availableExecutionTargets = "available_execution_targets"
        case runningTasks = "running_tasks"
        case tasksToday = "tasks_today"
        case routingDecisionsToday = "routing_decisions_today"
        case quotaWarningCount = "quota_warning_count"
        case lastRefreshSync = "last_refresh_sync"
    }
}

public struct RiskItemView: Decodable, Equatable, Identifiable, Sendable {
    public let title: String
    public let detail: String
    public let severity: String
    public let destination: String?
    public let rawCode: String?

    public var id: String { "\(severity)-\(title)-\(rawCode ?? "")" }

    enum CodingKeys: String, CodingKey {
        case title
        case detail
        case severity
        case destination
        case rawCode = "raw_code"
    }
}

public struct QuotaObservationView: Decodable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let quotaPoolId: String
    public let windowId: String
    public let observedAt: String
    public let remainingFraction: Double?
    public let confidence: String
    public let measurementSource: String
    public let resetAt: String?
    public let state: String

    public var id: String { "\(providerId)-\(quotaPoolId)-\(windowId)-\(observedAt)" }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case quotaPoolId = "quota_pool_id"
        case windowId = "window_id"
        case observedAt = "observed_at"
        case remainingFraction = "remaining_fraction"
        case confidence
        case measurementSource = "measurement_source"
        case resetAt = "reset_at"
        case state
    }
}

public struct QuotaHistoryView: Decodable, Equatable, Sendable {
    public let observations: [QuotaObservationView]
    public let retentionLimit: Int

    enum CodingKeys: String, CodingKey {
        case observations
        case retentionLimit = "retention_limit"
    }
}

public struct DashboardSummaryView: Decodable, Equatable, Sendable {
    public let connection: HealthView
    public let counts: DashboardCountsView
    public let recentTasks: [TaskView]
    public let projects: ProjectListView
    public let basicInfo: DashboardBasicInfoView
    public let taskTrend: [TaskTrendBucketView]
    public let taskStateDistribution: [TaskStateSliceView]
    public let risks: [RiskItemView]
    public let quotaHistory: QuotaHistoryView
    public let providers: ProviderHealthListView
    public let activeStatus: ActiveStatusView
    public let importantBlockers: [String]
    public let recentEvents: [ActivityEventView]

    enum CodingKeys: String, CodingKey {
        case connection
        case counts
        case recentTasks = "recent_tasks"
        case projects
        case basicInfo = "basic_info"
        case taskTrend = "task_trend"
        case taskStateDistribution = "task_state_distribution"
        case risks
        case quotaHistory = "quota_history"
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
        public let boolValue: Bool?
        public let doubleValue: Double?
        public let objectValue: [String: StringValue]?
        public let arrayValue: [StringValue]?

        public init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let string = try? container.decode(String.self) {
                value = string
                boolValue = nil
                doubleValue = nil
                objectValue = nil
                arrayValue = nil
            } else if let bool = try? container.decode(Bool.self) {
                value = bool ? "true" : "false"
                boolValue = bool
                doubleValue = nil
                objectValue = nil
                arrayValue = nil
            } else if let double = try? container.decode(Double.self) {
                value = "\(double)"
                boolValue = nil
                doubleValue = double
                objectValue = nil
                arrayValue = nil
            } else if let object = try? container.decode([String: StringValue].self) {
                value = nil
                boolValue = nil
                doubleValue = nil
                objectValue = object
                arrayValue = nil
            } else if let array = try? container.decode([StringValue].self) {
                value = nil
                boolValue = nil
                doubleValue = nil
                objectValue = nil
                arrayValue = array
            } else {
                value = nil
                boolValue = nil
                doubleValue = nil
                objectValue = nil
                arrayValue = nil
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
    public var explanation: [String: StringValue]? { decision["explanation"]?.objectValue }
    public var policyId: String? { explanation?["policy_id"]?.value }
    public var whySelected: String? { explanation?["why_selected"]?.value }
    public var candidates: [[String: StringValue]] {
        explanation?["candidates"]?.arrayValue?.compactMap(\.objectValue) ?? []
    }

    /// How the policy for this decision was resolved, frozen at decision time.
    public var policyResolution: [String: StringValue]? {
        explanation?["policy_resolution"]?.objectValue
    }
    public var resolvedPolicy: String? { policyResolution?["resolved_policy"]?.value }
    public var policyResolutionSource: String? {
        policyResolution?["resolution_source"]?.value
    }
    public var manualExecutionTargetId: String? {
        policyResolution?["manual_execution_target_id"]?.value
    }
}

public struct WorkspaceView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let projectId: String?
    public let repoPath: String
    public let worktreePath: String
    public let branch: String
    public let baseSha: String
    public let workingSubpath: String?
    public let writerLocked: Bool

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case projectId = "project_id"
        case repoPath = "repo_path"
        case worktreePath = "worktree_path"
        case branch
        case baseSha = "base_sha"
        case workingSubpath = "working_subpath"
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
    public let projectId: String
    public let intent: String
    /// `nil` means "no task override" — the daemon then resolves project, then global.
    public let schedulingPolicy: String?
    /// Only ever set alongside a `MANUAL` policy; the daemon rejects other pairings.
    public let manualExecutionTargetId: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case requestId = "request_id"
        case projectId = "project_id"
        case intent
        case schedulingPolicy = "scheduling_policy"
        case manualExecutionTargetId = "manual_execution_target_id"
    }

    public init(
        taskId: String,
        requestId: String,
        projectId: String,
        intent: String,
        schedulingPolicy: String? = nil,
        manualExecutionTargetId: String? = nil
    ) {
        self.taskId = taskId
        self.requestId = requestId
        self.projectId = projectId
        self.intent = intent
        self.schedulingPolicy = schedulingPolicy
        self.manualExecutionTargetId = manualExecutionTargetId
    }
}

public struct ProjectPathRequest: Encodable, Equatable, Sendable {
    public let path: String

    public init(path: String) {
        self.path = path
    }
}

public struct ProjectRegisterRequest: Encodable, Equatable, Sendable {
    public let path: String
    public let displayName: String?
    public let securityBookmarkB64: String?

    enum CodingKeys: String, CodingKey {
        case path
        case displayName = "display_name"
        case securityBookmarkB64 = "security_bookmark_b64"
    }

    public init(path: String, displayName: String? = nil, securityBookmarkB64: String? = nil) {
        self.path = path
        self.displayName = displayName
        self.securityBookmarkB64 = securityBookmarkB64
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
