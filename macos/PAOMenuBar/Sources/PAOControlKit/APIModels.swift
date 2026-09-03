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

/// Daemon build identity, so the dashboard can prove both halves of the stack
/// came from the same source revision rather than assuming it.
public struct BuildView: Decodable, Equatable, Sendable {
    public let commitSHA: String
    public let shortSHA: String
    public let apiVersion: String
    public let configuration: String
    public let builtAt: String

    enum CodingKeys: String, CodingKey {
        case commitSHA = "commit_sha"
        case shortSHA = "short_sha"
        case apiVersion = "api_version"
        case configuration
        case builtAt = "built_at"
    }

    public init(
        commitSHA: String,
        shortSHA: String,
        apiVersion: String,
        configuration: String,
        builtAt: String
    ) {
        self.commitSHA = commitSHA
        self.shortSHA = shortSHA
        self.apiVersion = apiVersion
        self.configuration = configuration
        self.builtAt = builtAt
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

// MARK: - Quota (shared subscription plan)

/// One quota window of a shared plan pool.
///
/// `remainingFraction` is populated only for EXACT/ESTIMATED windows, so the
/// view layer cannot draw a bar for a figure the provider never gave us.
public struct QuotaPlanWindowView: Codable, Equatable, Identifiable, Sendable {
    public let windowId: String
    public let windowKind: String
    public let state: String
    public let confidence: String
    public let remainingFraction: Double?
    public let remainingUnits: Double?
    public let totalUnits: Double?
    public let unit: String?
    public let resetAt: String?

    public var id: String { windowId }

    /// A window only draws a bar when the provider actually reported a figure.
    public var isReadable: Bool { confidence != "UNKNOWN" && remainingFraction != nil }

    enum CodingKeys: String, CodingKey {
        case windowId = "window_id"
        case windowKind = "window_kind"
        case state
        case confidence
        case remainingFraction = "remaining_fraction"
        case remainingUnits = "remaining_units"
        case totalUnits = "total_units"
        case unit
        case resetAt = "reset_at"
    }

    public init(
        windowId: String,
        windowKind: String,
        state: String,
        confidence: String,
        remainingFraction: Double? = nil,
        remainingUnits: Double? = nil,
        totalUnits: Double? = nil,
        unit: String? = nil,
        resetAt: String? = nil
    ) {
        self.windowId = windowId
        self.windowKind = windowKind
        self.state = state
        self.confidence = confidence
        self.remainingFraction = remainingFraction
        self.remainingUnits = remainingUnits
        self.totalUnits = totalUnits
        self.unit = unit
        self.resetAt = resetAt
    }
}

/// Which window currently limits the plan, and how soon it resets.
///
/// Scarcity and reset horizon are separate fields: 20% remaining that resets in
/// 30 minutes is not the same situation as 20% remaining that resets in six days.
public struct BindingWindowView: Codable, Equatable, Sendable {
    public let windowId: String?
    public let windowKind: String?
    public let remainingFraction: Double?
    public let resetAt: String?
    public let secondsUntilReset: Double?
    public let reason: String
    public let confidence: String

    enum CodingKeys: String, CodingKey {
        case windowId = "window_id"
        case windowKind = "window_kind"
        case remainingFraction = "remaining_fraction"
        case resetAt = "reset_at"
        case secondsUntilReset = "seconds_until_reset"
        case reason
        case confidence
    }

    public init(
        windowId: String? = nil,
        windowKind: String? = nil,
        remainingFraction: Double? = nil,
        resetAt: String? = nil,
        secondsUntilReset: Double? = nil,
        reason: String = "NO_KNOWN_REMAINING",
        confidence: String = "UNKNOWN"
    ) {
        self.windowId = windowId
        self.windowKind = windowKind
        self.remainingFraction = remainingFraction
        self.resetAt = resetAt
        self.secondsUntilReset = secondsUntilReset
        self.reason = reason
        self.confidence = confidence
    }
}

/// What one model consumed of the shared pool.
///
/// Contribution, never entitlement — there is deliberately no remaining field,
/// so the view cannot render this as a per-model balance.
public struct ModelConsumptionView: Codable, Equatable, Identifiable, Sendable {
    public let modelId: String
    public let consumedUnits: Double
    public let unitKind: String
    public let providerUnitLabel: String?
    public let callCount: Int?
    public let periodStart: String?
    public let periodEnd: String?
    public let confidence: String
    public let measurementSource: String

    public var id: String { modelId }

    enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
        case consumedUnits = "consumed_units"
        case unitKind = "unit_kind"
        case providerUnitLabel = "provider_unit_label"
        case callCount = "call_count"
        case periodStart = "period_start"
        case periodEnd = "period_end"
        case confidence
        case measurementSource = "measurement_source"
    }

    public init(
        modelId: String,
        consumedUnits: Double,
        unitKind: String,
        providerUnitLabel: String? = nil,
        callCount: Int? = nil,
        periodStart: String? = nil,
        periodEnd: String? = nil,
        confidence: String = "UNKNOWN",
        measurementSource: String = "UNKNOWN"
    ) {
        self.modelId = modelId
        self.consumedUnits = consumedUnits
        self.unitKind = unitKind
        self.providerUnitLabel = providerUnitLabel
        self.callCount = callCount
        self.periodStart = periodStart
        self.periodEnd = periodEnd
        self.confidence = confidence
        self.measurementSource = measurementSource
    }
}

/// A provider's per-scope *view* of one shared pool.
///
/// They are equivalents, not balances, and the view layer must label them so.
/// `scopeId` is not `modelId`: MiniMax's `model_remains` entries are workload
/// scopes on the observed account, and `scopeKind` records which it is — an
/// owner told "video shares this quota" would look for a model that does not
/// exist. `workloadScope` says what the scope meters, which is what lets the
/// coding dashboard show `general` and file `video` under Advanced Details
/// instead of rendering the two as equals.
public struct ModelEquivalentWindowView: Codable, Equatable, Identifiable, Sendable {
    public let scopeId: String
    public let scopeKind: String
    /// Stored optional so a daemon predating this field still decodes; the
    /// absence of a classification is UNKNOWN, which is a real value here.
    private let workloadScopeRaw: String?
    public let windowId: String
    public let remainingFraction: Double?
    public let remainingUnits: Double?
    public let totalUnits: Double?
    public let unitKind: String
    public let confidence: String

    public var id: String { "\(scopeId)/\(windowId)" }

    /// True only when the account's catalog confirmed this entry names a model.
    public var isModel: Bool { scopeKind == "MODEL" }

    /// CODING_TEXT | VIDEO_GENERATION | IMAGE_GENERATION | AUDIO | UNKNOWN
    public var workloadScope: String { workloadScopeRaw ?? "UNKNOWN" }

    /// Whether this scope belongs to the workload the plan is projecting.
    /// An unclassified scope counts as in-scope: a provider that names its
    /// entries after models is still describing the coding pool.
    public func belongs(to workload: String) -> Bool {
        workloadScope == workload || workloadScope == "UNKNOWN"
    }

    enum CodingKeys: String, CodingKey {
        case scopeId = "scope_id"
        case scopeKind = "scope_kind"
        case workloadScopeRaw = "workload_scope"
        case windowId = "window_id"
        case remainingFraction = "remaining_fraction"
        case remainingUnits = "remaining_units"
        case totalUnits = "total_units"
        case unitKind = "unit_kind"
        case confidence
    }

    public init(
        scopeId: String,
        scopeKind: String = "UNKNOWN",
        workloadScope: String = "UNKNOWN",
        windowId: String,
        remainingFraction: Double? = nil,
        remainingUnits: Double? = nil,
        totalUnits: Double? = nil,
        unitKind: String = "UNKNOWN",
        confidence: String = "UNKNOWN"
    ) {
        self.scopeId = scopeId
        self.scopeKind = scopeKind
        self.workloadScopeRaw = workloadScope
        self.windowId = windowId
        self.remainingFraction = remainingFraction
        self.remainingUnits = remainingUnits
        self.totalUnits = totalUnits
        self.unitKind = unitKind
        self.confidence = confidence
    }
}

/// Derived "roughly how many more tasks fit". Always ESTIMATED.
///
/// `estimatedRemainingTasks` is nil whenever history cannot support a figure;
/// `unavailableReason` then says why. Absent is not zero, and the two must
/// never render the same way.
public struct EquivalentCapacityView: Codable, Equatable, Identifiable, Sendable {
    public let modelId: String
    public let windowId: String
    public let taskClass: String
    public let estimatedRemainingTasks: Double?
    public let sampleCount: Int
    public let smallSample: Bool
    public let confidence: String
    public let unavailableReason: String?

    public var id: String { "\(modelId)/\(windowId)/\(taskClass)" }

    public var hasEstimate: Bool { estimatedRemainingTasks != nil }

    enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
        case windowId = "window_id"
        case taskClass = "task_class"
        case estimatedRemainingTasks = "estimated_remaining_tasks"
        case sampleCount = "sample_count"
        case smallSample = "small_sample"
        case confidence
        case unavailableReason = "unavailable_reason"
    }

    public init(
        modelId: String,
        windowId: String,
        taskClass: String = "default",
        estimatedRemainingTasks: Double? = nil,
        sampleCount: Int = 0,
        smallSample: Bool = false,
        confidence: String = "ESTIMATED",
        unavailableReason: String? = nil
    ) {
        self.modelId = modelId
        self.windowId = windowId
        self.taskClass = taskClass
        self.estimatedRemainingTasks = estimatedRemainingTasks
        self.sampleCount = sampleCount
        self.smallSample = smallSample
        self.confidence = confidence
        self.unavailableReason = unavailableReason
    }
}

/// Plan-first projection of one connected subscription.
///
/// The three concepts stay separate all the way into the view: `windows` is the
/// shared plan balance, `modelConsumption` is what each model spent, and
/// `equivalentCapacity` is a derived estimate. Rendering them as one list would
/// merge facts of different kinds.
public struct QuotaPlanView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let planId: String
    /// The provider's own product name — "GLM Coding Plan", never a renamed
    /// symmetric label.
    public let displayName: String
    public let planLevel: String?
    public let quotaSemantics: String
    public let poolId: String
    public let resourceKind: String
    public let sharedAcrossModels: Bool
    public let unitKind: String
    public let coveredModelIds: [String]
    public let state: String
    public let confidence: String
    public let observedAt: String?
    public let unknownReason: String?
    /// Stored optional so a daemon predating these fields still decodes.
    private let activeWorkloadScopeRaw: String?
    private let workloadScopeNotesRaw: [String]?
    public let windows: [QuotaPlanWindowView]
    public let bindingWindow: BindingWindowView?
    public let modelConsumption: [ModelConsumptionView]
    public let modelEquivalents: [ModelEquivalentWindowView]
    public let equivalentCapacity: [EquivalentCapacityView]

    public var id: String { poolId }

    /// True when at least one window carries a provider-reported figure.
    /// A plan can be partially readable: some windows known, others not.
    public var hasReadableWindow: Bool { windows.contains(where: \.isReadable) }

    /// The workload every figure in this plan is scoped to. The orchestrator
    /// schedules CODING_TEXT today, so a MiniMax plan carries its `general`
    /// scope here and `video` never reaches `windows`.
    public var activeWorkloadScope: String { activeWorkloadScopeRaw ?? "UNKNOWN" }

    /// Sanitized codes for observed scopes this workload does not read, e.g.
    /// `VIDEO_SCOPE_IGNORED_FOR_CODING`. Advanced Details only — they explain
    /// an absence, they are not failures.
    public var workloadScopeNotes: [String] { workloadScopeNotesRaw ?? [] }

    /// Scope views belonging to the workload this plan projects.
    public var inScopeEquivalents: [ModelEquivalentWindowView] {
        modelEquivalents.filter { $0.belongs(to: activeWorkloadScope) }
    }

    /// Real provider observations for workloads this build does not schedule.
    /// Kept visible under Advanced Details so the evidence is not lost, and
    /// kept out of the primary card so it cannot read as a coding balance.
    public var outOfScopeEquivalents: [ModelEquivalentWindowView] {
        modelEquivalents.filter { !$0.belongs(to: activeWorkloadScope) }
    }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case planId = "plan_id"
        case displayName = "display_name"
        case planLevel = "plan_level"
        case quotaSemantics = "quota_semantics"
        case poolId = "pool_id"
        case resourceKind = "resource_kind"
        case sharedAcrossModels = "shared_across_models"
        case unitKind = "unit_kind"
        case coveredModelIds = "covered_model_ids"
        case state
        case confidence
        case observedAt = "observed_at"
        case unknownReason = "unknown_reason"
        case activeWorkloadScopeRaw = "active_workload_scope"
        case workloadScopeNotesRaw = "workload_scope_notes"
        case windows
        case bindingWindow = "binding_window"
        case modelConsumption = "model_consumption"
        case modelEquivalents = "model_equivalents"
        case equivalentCapacity = "equivalent_capacity"
    }

    public init(
        providerId: String,
        planId: String,
        displayName: String,
        planLevel: String? = nil,
        quotaSemantics: String = "UNKNOWN",
        poolId: String,
        resourceKind: String = "UNKNOWN",
        sharedAcrossModels: Bool = true,
        unitKind: String = "UNKNOWN",
        coveredModelIds: [String] = [],
        state: String = "UNKNOWN",
        confidence: String = "UNKNOWN",
        observedAt: String? = nil,
        unknownReason: String? = nil,
        activeWorkloadScope: String = "UNKNOWN",
        workloadScopeNotes: [String] = [],
        windows: [QuotaPlanWindowView] = [],
        bindingWindow: BindingWindowView? = nil,
        modelConsumption: [ModelConsumptionView] = [],
        modelEquivalents: [ModelEquivalentWindowView] = [],
        equivalentCapacity: [EquivalentCapacityView] = []
    ) {
        self.providerId = providerId
        self.planId = planId
        self.displayName = displayName
        self.planLevel = planLevel
        self.quotaSemantics = quotaSemantics
        self.poolId = poolId
        self.resourceKind = resourceKind
        self.sharedAcrossModels = sharedAcrossModels
        self.unitKind = unitKind
        self.coveredModelIds = coveredModelIds
        self.state = state
        self.confidence = confidence
        self.observedAt = observedAt
        self.unknownReason = unknownReason
        self.activeWorkloadScopeRaw = activeWorkloadScope
        self.workloadScopeNotesRaw = workloadScopeNotes
        self.windows = windows
        self.bindingWindow = bindingWindow
        self.modelConsumption = modelConsumption
        self.modelEquivalents = modelEquivalents
        self.equivalentCapacity = equivalentCapacity
    }
}

// MARK: - Quota (connection-based)

/// One connected provider on the Quota page.
///
/// A connected provider always renders, even with zero quota evidence:
/// `quotaState == "UNKNOWN"` is a truthful state, not an absence. UNKNOWN
/// never carries a fabricated `remainingFraction`.
public struct QuotaProviderCardView: Codable, Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let connectionState: String
    public let authState: String?
    public let planSurface: String?
    public let region: String?
    /// OBSERVED | UNKNOWN
    public let quotaState: String
    /// EXACT | ESTIMATED | UNKNOWN
    public let confidence: String
    public let measurementSource: String?
    public let observedAt: String?
    /// Whether a documented read-only quota endpoint exists for this surface
    /// at all, independent of whether a credential is configured right now.
    public let readonlySourceAvailable: Bool
    public let collectorAvailable: Bool
    public let lastRefreshStatus: String?
    public let lastRefreshAt: String?
    public let failureReason: String?
    /// Sanitized provenance of the credential used for this surface's quota
    /// read. Never the credential itself.
    public let credentialSource: String
    public let quotaPools: [QuotaPoolHealthView]
    /// Plan-first projection. Present whenever any plan evidence exists — the
    /// plan balance may be UNKNOWN while model consumption is known, and the
    /// card renders both rather than hiding the pair.
    public let plan: QuotaPlanView?

    public var id: String { providerId }

    public var isObserved: Bool { quotaState == "OBSERVED" }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case connectionState = "connection_state"
        case authState = "auth_state"
        case planSurface = "plan_surface"
        case region
        case quotaState = "quota_state"
        case confidence
        case measurementSource = "measurement_source"
        case observedAt = "observed_at"
        case readonlySourceAvailable = "readonly_source_available"
        case collectorAvailable = "collector_available"
        case lastRefreshStatus = "last_refresh_status"
        case lastRefreshAt = "last_refresh_at"
        case failureReason = "failure_reason"
        case credentialSource = "credential_source"
        case quotaPools = "quota_pools"
        case plan
    }

    public init(
        providerId: String,
        displayName: String,
        connectionState: String,
        authState: String? = nil,
        planSurface: String? = nil,
        region: String? = nil,
        quotaState: String,
        confidence: String,
        measurementSource: String? = nil,
        observedAt: String? = nil,
        readonlySourceAvailable: Bool = false,
        collectorAvailable: Bool = false,
        lastRefreshStatus: String? = nil,
        lastRefreshAt: String? = nil,
        failureReason: String? = nil,
        credentialSource: String = "NONE",
        quotaPools: [QuotaPoolHealthView] = [],
        plan: QuotaPlanView? = nil
    ) {
        self.providerId = providerId
        self.displayName = displayName
        self.connectionState = connectionState
        self.authState = authState
        self.planSurface = planSurface
        self.region = region
        self.quotaState = quotaState
        self.confidence = confidence
        self.measurementSource = measurementSource
        self.observedAt = observedAt
        self.readonlySourceAvailable = readonlySourceAvailable
        self.collectorAvailable = collectorAvailable
        self.lastRefreshStatus = lastRefreshStatus
        self.lastRefreshAt = lastRefreshAt
        self.failureReason = failureReason
        self.credentialSource = credentialSource
        self.quotaPools = quotaPools
        self.plan = plan
    }

    /// Decodes leniently for the two keys added in P4.2.6.5.
    ///
    /// A freshly built app can be pointed at a daemon that predates them —
    /// during an upgrade, or while the deterministic launcher is reconciling a
    /// stale daemon. Treating `credential_source` and `plan` as required would
    /// turn that transient mismatch into a blank Quota page, which is exactly
    /// the failure mode this page exists to avoid.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        providerId = try container.decode(String.self, forKey: .providerId)
        displayName = try container.decode(String.self, forKey: .displayName)
        connectionState = try container.decode(String.self, forKey: .connectionState)
        authState = try container.decodeIfPresent(String.self, forKey: .authState)
        planSurface = try container.decodeIfPresent(String.self, forKey: .planSurface)
        region = try container.decodeIfPresent(String.self, forKey: .region)
        quotaState = try container.decode(String.self, forKey: .quotaState)
        confidence = try container.decode(String.self, forKey: .confidence)
        measurementSource = try container.decodeIfPresent(
            String.self, forKey: .measurementSource
        )
        observedAt = try container.decodeIfPresent(String.self, forKey: .observedAt)
        readonlySourceAvailable =
            try container.decodeIfPresent(Bool.self, forKey: .readonlySourceAvailable) ?? false
        collectorAvailable =
            try container.decodeIfPresent(Bool.self, forKey: .collectorAvailable) ?? false
        lastRefreshStatus = try container.decodeIfPresent(
            String.self, forKey: .lastRefreshStatus
        )
        lastRefreshAt = try container.decodeIfPresent(String.self, forKey: .lastRefreshAt)
        failureReason = try container.decodeIfPresent(String.self, forKey: .failureReason)
        credentialSource =
            try container.decodeIfPresent(String.self, forKey: .credentialSource) ?? "NONE"
        quotaPools =
            try container.decodeIfPresent([QuotaPoolHealthView].self, forKey: .quotaPools) ?? []
        plan = try container.decodeIfPresent(QuotaPlanView.self, forKey: .plan)
    }
}

/// Summary derived from connected providers, not merely observed pools.
/// UNKNOWN is never counted as healthy.
public struct QuotaSummaryView: Codable, Equatable, Sendable {
    public let connectedProviderCount: Int
    public let quotaObservableProviderCount: Int
    public let quotaUnknownProviderCount: Int
    public let quotaWarningCount: Int
    public let quotaExhaustedCount: Int

    enum CodingKeys: String, CodingKey {
        case connectedProviderCount = "connected_provider_count"
        case quotaObservableProviderCount = "quota_observable_provider_count"
        case quotaUnknownProviderCount = "quota_unknown_provider_count"
        case quotaWarningCount = "quota_warning_count"
        case quotaExhaustedCount = "quota_exhausted_count"
    }

    public init(
        connectedProviderCount: Int,
        quotaObservableProviderCount: Int,
        quotaUnknownProviderCount: Int,
        quotaWarningCount: Int,
        quotaExhaustedCount: Int
    ) {
        self.connectedProviderCount = connectedProviderCount
        self.quotaObservableProviderCount = quotaObservableProviderCount
        self.quotaUnknownProviderCount = quotaUnknownProviderCount
        self.quotaWarningCount = quotaWarningCount
        self.quotaExhaustedCount = quotaExhaustedCount
    }
}

/// The three distinct owner-facing states of the Quota page.
public enum QuotaPageState: String, Codable, Sendable {
    case noConnectedProvider = "NO_CONNECTED_PROVIDER"
    case connectedButQuotaUnknown = "CONNECTED_BUT_QUOTA_UNKNOWN"
    case connectedWithQuotaObservations = "CONNECTED_WITH_QUOTA_OBSERVATIONS"
}

public struct QuotaOverviewView: Codable, Equatable, Sendable {
    public let state: String
    public let summary: QuotaSummaryView
    public let providers: [QuotaProviderCardView]
    public let history: QuotaHistoryView

    /// Unrecognized daemon states fail safe to "connected but unknown" rather
    /// than pretending the page is empty.
    public var pageState: QuotaPageState {
        QuotaPageState(rawValue: state)
            ?? (providers.isEmpty ? .noConnectedProvider : .connectedButQuotaUnknown)
    }

    public init(
        state: String,
        summary: QuotaSummaryView,
        providers: [QuotaProviderCardView],
        history: QuotaHistoryView
    ) {
        self.state = state
        self.summary = summary
        self.providers = providers
        self.history = history
    }
}

public struct QuotaRefreshResultView: Codable, Equatable, Sendable {
    public let refreshedProviderIds: [String]
    public let overview: QuotaOverviewView

    enum CodingKeys: String, CodingKey {
        case refreshedProviderIds = "refreshed_provider_ids"
        case overview
    }

    public init(refreshedProviderIds: [String], overview: QuotaOverviewView) {
        self.refreshedProviderIds = refreshedProviderIds
        self.overview = overview
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
    /// English fallback from the daemon. Owner-facing UI localizes from
    /// `rawCode` + `count` instead, so risks follow the product language.
    public let title: String
    public let detail: String
    public let severity: String
    public let destination: String?
    public let rawCode: String?
    /// How many entities the risk covers, when it is a counting risk.
    public let count: Int?

    public var id: String { "\(severity)-\(title)-\(rawCode ?? "")" }

    enum CodingKeys: String, CodingKey {
        case title
        case detail
        case severity
        case destination
        case rawCode = "raw_code"
        case count
    }

    public init(
        title: String,
        detail: String,
        severity: String,
        destination: String? = nil,
        rawCode: String? = nil,
        count: Int? = nil
    ) {
        self.title = title
        self.detail = detail
        self.severity = severity
        self.destination = destination
        self.rawCode = rawCode
        self.count = count
    }
}

public struct QuotaObservationView: Codable, Equatable, Identifiable, Sendable {
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

public struct QuotaHistoryView: Codable, Equatable, Sendable {
    public let observations: [QuotaObservationView]
    public let retentionLimit: Int

    enum CodingKeys: String, CodingKey {
        case observations
        case retentionLimit = "retention_limit"
    }

    public init(observations: [QuotaObservationView], retentionLimit: Int) {
        self.observations = observations
        self.retentionLimit = retentionLimit
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
    /// Authoritative routing plan: declared roles, per-role lifecycle status, and
    /// each role's independent decision history. Optional because a daemon
    /// predating the contract does not send it — `routingSummary` then falls back
    /// to the legacy single decision. See docs/ROUTING_ROLE_CONTRACT.md.
    public let routingPlan: RoutingPlanView?

    enum CodingKeys: String, CodingKey {
        case task
        case runs
        case routing
        case verification
        case approvals
        case workspace
        case events
        case routingPlan = "routing_plan"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        task = try container.decode(TaskView.self, forKey: .task)
        runs = try container.decode([RunView].self, forKey: .runs)
        routing = try container.decodeIfPresent(RoutingDecisionView.self, forKey: .routing)
        verification = try container.decode(VerificationReportView.self, forKey: .verification)
        approvals = try container.decode(ApprovalListView.self, forKey: .approvals)
        workspace = try container.decodeIfPresent(WorkspaceView.self, forKey: .workspace)
        events = try container.decode([ActivityEventView].self, forKey: .events)
        routingPlan = try container.decodeIfPresent(RoutingPlanView.self, forKey: .routingPlan)
    }

    /// Normalized routing projection. Views consume this instead of reassembling
    /// plan/decision bookkeeping, so routing semantics stay in one tested place.
    public var routingSummary: TaskRoutingSummary? {
        TaskRoutingSummary.make(plan: routingPlan, legacyDecision: routing)
    }
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
