import Foundation

/// Bounded host-owned projections. Worker output and local files are deliberately
/// not part of these models; COMPLETE describes delivery of this report only.
public struct HostResultReport: Decodable, Equatable, Sendable {
    public struct Binding: Decodable, Equatable, Sendable {
        public let storeId: String
        public let taskId: String
        public let runId: String?
        public let baseCommit: String?
        public let resultCommit: String?
        enum CodingKeys: String, CodingKey {
            case storeId = "store_id", taskId = "task_id", runId = "run_id"
            case baseCommit = "base_commit", resultCommit = "result_commit"
        }
    }
    public struct Verification: Decodable, Equatable, Sendable {
        public let status: String
        public let evidenceId: String?
        enum CodingKeys: String, CodingKey { case status, evidenceId = "evidence_id" }
    }
    public struct Test: Decodable, Equatable, Sendable {
        public let name: String
        public let status: String
    }
    public struct Artifact: Decodable, Equatable, Sendable {
        public let kind: String
        public let reference: String
        public let sha256: String
    }
    public let schemaVersion: Int
    public let kind: String
    public let resultId: String
    public let binding: Binding
    public let taskState: String
    public let goal: String
    public let goalRedacted: Bool
    public let completeness: String
    public let verification: Verification
    public let changedFiles: [String]
    public let tests: [Test]
    public let artifacts: [Artifact]
    public let limitations: [String]
    public let truncated: [String: Bool]
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", resultId = "result_id", taskState = "task_state"
        case goalRedacted = "goal_redacted", changedFiles = "changed_files"
        case kind, binding, goal, completeness, verification, tests, artifacts, limitations, truncated
    }
}

public struct ManualContinuation: Decodable, Equatable, Sendable {
    public let schemaVersion: Int
    public let resultId: String
    public let binding: HostResultReport.Binding
    public let completed: [String]
    public let pending: [String]
    public let nextStep: String
    public let manualOnly: Bool
    public let text: String
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", resultId = "result_id", nextStep = "next_step"
        case manualOnly = "manual_only", binding, completed, pending, text
    }
}

public struct SafeTaskState: Decodable, Equatable, Sendable {
    public let taskId: String
    public let state: String
    public let stateVersion: Int
    enum CodingKeys: String, CodingKey {
        case taskId = "task_id", state, stateVersion = "state_version"
    }
}

public struct RecoveryCandidate: Decodable, Equatable, Sendable {
    public let taskId: String
    public let taskStateVersion: Int
    public let priorDispatchId: String
    enum CodingKeys: String, CodingKey {
        case taskId = "task_id", taskStateVersion = "task_state_version"
        case priorDispatchId = "prior_dispatch_id"
    }
}

public struct SafeTaskDetail: Decodable, Equatable, Sendable {
    public struct Approval: Decodable, Equatable, Sendable {
        public let status: String
    }
    public let task: SafeTaskState
    public let approvals: [Approval]
    public let result: HostResultReport
    public let recoveryCandidate: RecoveryCandidate?
    public let recoveryStatusAvailable: Bool
    enum CodingKeys: String, CodingKey {
        case task, approvals, result, recoveryCandidate = "recovery_candidate"
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        task = try c.decode(SafeTaskState.self, forKey: .task)
        approvals = try c.decode([Approval].self, forKey: .approvals)
        result = try c.decode(HostResultReport.self, forKey: .result)
        recoveryCandidate = try c.decodeIfPresent(RecoveryCandidate.self, forKey: .recoveryCandidate)
        recoveryStatusAvailable = c.contains(.recoveryCandidate)
    }
}

public struct TaskContinuationPayload: Decodable, Equatable, Sendable {
    public let task: SafeTaskState
    public let result: HostResultReport
    public let continuation: ManualContinuation
    public func matches(_ current: TaskView) -> Bool {
        task.taskId == current.taskId && task.stateVersion == current.stateVersion && task.state == current.state
    }
}

public struct RecoveryPayload: Decodable, Equatable, Sendable {
    public struct Receipt: Decodable, Equatable, Sendable {
        public let requestId: String
        public let taskId: String
        public let priorDispatchId: String
        public let taskStateVersion: Int
        public let storeId: String
        public let processEpoch: String
        enum CodingKeys: String, CodingKey {
            case requestId = "request_id", taskId = "task_id", priorDispatchId = "prior_dispatch_id"
            case taskStateVersion = "task_state_version", storeId = "store_id", processEpoch = "process_epoch"
        }
    }
    public let recovery: Receipt
    public let currentTask: SafeTaskState
    public let launchPerformed: Bool
    public let nextStep: String
    enum CodingKeys: String, CodingKey {
        case recovery, currentTask = "current_task", launchPerformed = "launch_performed", nextStep = "next_step"
    }
}

public struct LocalClientEnvelope<Payload: Decodable & Equatable & Sendable>: Decodable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestId: String
    public let operation: String
    public let storeId: String
    public let processEpoch: String
    public let payload: Payload
    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version", requestId = "request_id", operation
        case storeId = "store_id", processEpoch = "process_epoch", payload
    }
}

/// Captured once at the owner's recovery click. Reconciliation preserves these
/// fields and the request ID; it cannot silently become a new RECOVER or START.
public struct RecoveryRequest: Encodable, Equatable, Sendable {
    public let requestId: String
    public let candidate: RecoveryCandidate
    public let storeId: String
    public let processEpoch: String
    public let operation: String
    public init(requestId: String, candidate: RecoveryCandidate, storeId: String,
                processEpoch: String, operation: String = "RECOVER") {
        self.requestId = requestId
        self.candidate = candidate
        self.storeId = storeId
        self.processEpoch = processEpoch
        self.operation = operation
    }
    public func matches(_ envelope: LocalClientEnvelope<SafeTaskDetail>, task: TaskView) -> Bool {
        candidate == envelope.payload.recoveryCandidate && storeId == envelope.storeId
            && processEpoch == envelope.processEpoch && candidate.taskId == task.taskId
            && candidate.taskStateVersion == task.stateVersion && envelope.payload.task.state == task.state
    }
    public var statusRequest: RecoveryRequest {
        RecoveryRequest(requestId: requestId, candidate: candidate, storeId: storeId,
                        processEpoch: processEpoch, operation: "RECOVERY_STATUS")
    }
    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version", operation, requestId = "request_id"
        case taskId = "task_id", taskStateVersion = "task_state_version", priorDispatchId = "prior_dispatch_id"
        case expectedStoreId = "expected_store_id", expectedProcessEpoch = "expected_process_epoch"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(2, forKey: .protocolVersion)
        try c.encode(operation, forKey: .operation)
        try c.encode(requestId, forKey: .requestId)
        try c.encode(candidate.taskId, forKey: .taskId)
        try c.encode(candidate.taskStateVersion, forKey: .taskStateVersion)
        try c.encode(candidate.priorDispatchId, forKey: .priorDispatchId)
        try c.encode(storeId, forKey: .expectedStoreId)
        try c.encode(processEpoch, forKey: .expectedProcessEpoch)
    }
}

public extension PAOControlClient {
    private func readLocalTask<T: Decodable & Equatable & Sendable>(
        _ taskId: String, operation: String, requestId: String
    ) async throws -> LocalClientEnvelope<T> {
        let body = try JSONSerialization.data(withJSONObject: [
            "protocol_version": 2, "operation": operation,
            "request_id": requestId, "task_id": taskId,
        ])
        let result: LocalClientEnvelope<T> = try await perform(
            method: "POST", path: "/v2/local-client", encodedBody: body
        )
        guard result.protocolVersion == 2, result.operation == operation,
              result.requestId == requestId else { throw PAOClientError.malformedResponse }
        return result
    }

    func safeTaskDetail(_ taskId: String, requestId: String = UUID().uuidString) async throws
        -> LocalClientEnvelope<SafeTaskDetail> {
        let result: LocalClientEnvelope<SafeTaskDetail> = try await readLocalTask(
            taskId, operation: "DETAIL", requestId: requestId
        )
        guard result.payload.task.taskId == taskId,
              result.payload.result.binding.taskId == taskId,
              result.payload.result.binding.storeId == result.storeId,
              result.payload.result.taskState == result.payload.task.state,
              result.payload.result.schemaVersion == 1,
              result.payload.result.kind == "HOST_VERIFICATION_REPORT",
              result.payload.recoveryCandidate.map({
                  $0.taskId == taskId && $0.taskStateVersion == result.payload.task.stateVersion
              }) ?? true else { throw PAOClientError.malformedResponse }
        return result
    }

    func taskContinuation(_ taskId: String, requestId: String = UUID().uuidString) async throws
        -> LocalClientEnvelope<TaskContinuationPayload> {
        let result: LocalClientEnvelope<TaskContinuationPayload> = try await readLocalTask(
            taskId, operation: "CONTINUATION", requestId: requestId
        )
        let payload = result.payload
        guard payload.task.taskId == taskId, payload.result.binding.taskId == taskId,
              payload.result.binding.storeId == result.storeId, payload.result.schemaVersion == 1,
              payload.result.kind == "HOST_VERIFICATION_REPORT", payload.result.taskState == payload.task.state,
              payload.continuation.schemaVersion == 1,
              payload.continuation.manualOnly, payload.continuation.binding == payload.result.binding,
              payload.continuation.resultId == payload.result.resultId
        else { throw PAOClientError.malformedResponse }
        return result
    }

    func recoverTask(_ request: RecoveryRequest) async throws -> RecoveryPayload {
        let result: LocalClientEnvelope<RecoveryPayload> = try await perform(
            method: "POST", path: "/v2/local-client", encodedBody: JSONEncoder().encode(request)
        )
        let receipt = result.payload.recovery
        guard ["RECOVER", "RECOVERY_STATUS"].contains(request.operation),
              result.protocolVersion == 2, result.requestId == request.requestId,
              result.operation == request.operation, result.storeId == request.storeId,
              receipt.requestId == request.requestId, receipt.taskId == request.candidate.taskId,
              receipt.priorDispatchId == request.candidate.priorDispatchId,
              receipt.taskStateVersion == request.candidate.taskStateVersion,
              receipt.storeId == request.storeId, receipt.processEpoch == request.processEpoch,
              result.payload.currentTask.taskId == request.candidate.taskId,
              !result.payload.launchPerformed else { throw PAOClientError.malformedResponse }
        return result.payload
    }
}

public enum TaskActionKind: String, CaseIterable, Identifiable, Sendable {
    case approval, blocked, recovery, running
    public var id: String { rawValue }
    public static func classify(_ task: TaskView, detail: SafeTaskDetail?) -> TaskActionKind? {
        // Never attach a cached observation to a newer generation of the task.
        let current = detail?.task.stateVersion == task.stateVersion && detail?.task.state == task.state
            ? detail : nil
        if current?.approvals.contains(where: { $0.status == "PENDING" }) == true { return .approval }
        if current?.recoveryCandidate != nil { return .recovery }
        if ["BLOCKED", "FAILED"].contains(task.state) { return .blocked }
        if ["RUNNING", "VERIFYING"].contains(task.state) { return .running }
        return nil
    }
}
