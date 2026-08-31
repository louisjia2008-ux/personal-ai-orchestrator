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
    public let runtimeAvailable: Bool?
    public let observedAvailability: ObservedAvailabilityView?

    public var id: String { executionTargetId }

    enum CodingKeys: String, CodingKey {
        case executionTargetId = "execution_target_id"
        case modelSkuId = "model_sku_id"
        case runtimeId = "runtime_id"
        case enabled
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

    public var id: String { providerId }

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case accountCount = "account_count"
        case quotaPools = "quota_pools"
        case executionTargets = "execution_targets"
    }
}

public struct ProviderHealthListView: Codable, Equatable, Sendable {
    public let providers: [ProviderHealthView]
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
