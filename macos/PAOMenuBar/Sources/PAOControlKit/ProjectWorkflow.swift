import Foundation
import CryptoKit

public struct ProjectVerificationView: Codable, Equatable, Sendable {
    public let projectId: String
    public let configured: Bool
    public var commandLines: [String]
    public var allowedPaths: [String]
    public var timeoutSeconds: Int
    public var preparationCommandLines: [String]?

    enum CodingKeys: String, CodingKey {
        case projectId = "project_id", configured
        case commandLines = "command_lines", allowedPaths = "allowed_paths"
        case timeoutSeconds = "timeout_seconds"
        case preparationCommandLines = "preparation_command_lines"
    }
}

public struct TaskPreparationView: Decodable, Sendable {
    public let taskId: String
    public let status: String
    public let cleanupState: String?
    public let stages: [Stage]
    public struct Stage: Decodable, Sendable {
        public let name: String
        public let argv: [String]
        public let returncode: Int?
        public let stdout: String
        public let stderr: String
        public let truncated: Bool
        public let error: String?
    }
    enum CodingKeys: String, CodingKey {
        case taskId = "task_id", status, stages, cleanupState = "cleanup_state"
    }
}

public enum ManualRecoveryOperation: String, Decodable, Sendable {
    case retry = "RETRY"
    case recover = "RECOVER"
    case reopen = "REOPEN"
}

public struct TaskVerificationHistoryView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let entries: [Entry]
    public let truncated: Bool
    public struct Entry: Decodable, Equatable, Identifiable, Sendable {
        public let evidenceId: String
        public let evidenceSHA256: String
        public let verificationEventId: Int
        public let recordedAt: String
        public let verdict: String
        public let status: String
        public let available: Bool
        public let patchSHA256: String?
        public let patchByteCount: Int?
        public var id: Int { verificationEventId }
        enum CodingKeys: String, CodingKey {
            case evidenceId = "evidence_id", evidenceSHA256 = "evidence_sha256"
            case verificationEventId = "verification_event_id", recordedAt = "recorded_at"
            case verdict, status, available, patchSHA256 = "patch_sha256"
            case patchByteCount = "patch_byte_count"
        }
    }
    enum CodingKeys: String, CodingKey { case taskId = "task_id", entries, truncated }
}

public struct ManualWorkflowView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let priorDispatchId: String?
    public let failureCode: String?
    public let retryAvailable: Bool
    public let blockingReason: String?
    public let recoveryOperation: ManualRecoveryOperation?
    enum CodingKeys: String, CodingKey {
        case taskId = "task_id", priorDispatchId = "prior_dispatch_id"
        case failureCode = "failure_code", retryAvailable = "retry_available"
        case blockingReason = "blocking_reason"
        case recoveryOperation = "recovery_operation"
    }
}

public struct TaskPatchView: Decodable, Equatable, Sendable {
    public let taskId: String
    public let baseSHA: String
    public let patchData: Data
    public var patch: String { String(decoding: patchData, as: UTF8.self) }
    public let sha256: String
    public let byteCount: Int
    public let source: String
    public let workspaceStatus: String
    public let evidenceId: String?
    enum CodingKeys: String, CodingKey {
        case taskId = "task_id", baseSHA = "base_sha", patchBase64 = "patch_base64", sha256
        case byteCount = "byte_count", source, workspaceStatus = "workspace_status"
        case evidenceId = "evidence_id"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try values.decode(String.self, forKey: .taskId)
        baseSHA = try values.decode(String.self, forKey: .baseSHA)
        sha256 = try values.decode(String.self, forKey: .sha256)
        byteCount = try values.decode(Int.self, forKey: .byteCount)
        source = try values.decode(String.self, forKey: .source)
        workspaceStatus = try values.decode(String.self, forKey: .workspaceStatus)
        evidenceId = try values.decodeIfPresent(String.self, forKey: .evidenceId)
        let encoded = try values.decode(String.self, forKey: .patchBase64)
        guard let bytes = Data(base64Encoded: encoded), bytes.count == byteCount,
              bytes.count <= 1024 * 1024,
              SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == sha256,
              ["VERIFIED_SNAPSHOT", "UNVERIFIED_WORKTREE"].contains(source) else {
            throw DecodingError.dataCorruptedError(
                forKey: .patchBase64, in: values, debugDescription: "Patch attestation mismatch"
            )
        }
        patchData = bytes
    }
}

public struct ExecutionProbeView: Decodable, Sendable {
    public let result: String
    public let reasonCode: String
    enum CodingKeys: String, CodingKey { case result, reasonCode = "reason_code" }
}

/// The same host projection consumed by the CLI and other local clients.
/// Completeness and verification are displayed as separate server facts.
public struct CrossClientResultView: Decodable, Equatable, Sendable {
    public let resultId: String
    public let binding: Binding
    public let goal: String
    public let goalRedacted: Bool
    public let completeness: String
    public let verification: Verification
    public let changedFiles: [String]
    public let tests: [Check]
    public let limitations: [String]
    public let truncated: Truncation
    public var hasOmissions: Bool {
        goalRedacted || truncated.any || completeness == "TRUNCATED"
    }

    public struct Binding: Decodable, Equatable, Sendable {
        public let taskId: String
        public let runId: String?
        public let verificationEvidenceSHA256: String?
        public let patchSHA256: String?
        public let patchByteCount: Int?
        enum CodingKeys: String, CodingKey {
            case taskId = "task_id", runId = "run_id"
            case verificationEvidenceSHA256 = "verification_evidence_sha256"
            case patchSHA256 = "patch_sha256", patchByteCount = "patch_byte_count"
        }
    }
    public struct Verification: Decodable, Equatable, Sendable {
        public let status: String
        public let evidenceId: String?
        enum CodingKeys: String, CodingKey { case status, evidenceId = "evidence_id" }
    }
    public struct Check: Decodable, Equatable, Sendable {
        public let name: String
        public let status: String
    }
    public struct Truncation: Decodable, Equatable, Sendable {
        public let goal: Bool
        public let changedFiles: Bool
        public let tests: Bool
        enum CodingKeys: String, CodingKey { case goal, tests, changedFiles = "changed_files" }
        public var any: Bool { goal || changedFiles || tests }
    }
    enum CodingKeys: String, CodingKey {
        case resultId = "result_id", binding, goal, goalRedacted = "goal_redacted", completeness
        case verification, changedFiles = "changed_files", tests, limitations, truncated
    }
}

private struct CrossClientResultPayload: Decodable {
    let result: CrossClientResultView
}

struct LocalEnvelope<Payload: Decodable>: Decodable {
    let storeId: String
    let processEpoch: String
    let payload: Payload
    enum CodingKeys: String, CodingKey {
        case storeId = "store_id", processEpoch = "process_epoch", payload
    }
}

struct EmptyLocalPayload: Decodable {}

extension PAOControlClient {
    public func probeExecutionTarget(_ targetId: String) async throws -> ExecutionProbeView {
        try await perform(method: "POST", path: "/v1/execution-targets/\(targetId)/probe",
                          encodedBody: Data("{}".utf8), timeoutSeconds: 110)
    }
    public func projectVerification(_ projectId: String) async throws -> ProjectVerificationView {
        try await perform(method: "GET", path: "/v1/projects/\(projectId)/verification", encodedBody: nil)
    }

    public func saveProjectVerification(
        _ projectId: String, commands: [String], allowedPaths: [String], timeout: Int,
        preparationCommands: [String] = []
    ) async throws -> ProjectVerificationView {
        struct Body: Encodable {
            let command_lines: [String]
            let allowed_paths: [String]
            let timeout_seconds: Int
            let preparation_command_lines: [String]
        }
        return try await perform(
            method: "PUT", path: "/v1/projects/\(projectId)/verification",
            encodedBody: JSONEncoder().encode(Body(
                command_lines: commands, allowed_paths: allowedPaths, timeout_seconds: timeout,
                preparation_command_lines: preparationCommands
            ))
        )
    }

    private func localRequest<P: Decodable>(_ body: [String: Any]) async throws -> LocalEnvelope<P> {
        try await perform(method: "POST", path: "/v2/local-client",
                          encodedBody: JSONSerialization.data(withJSONObject: body))
    }

    private func localContext() async throws -> LocalEnvelope<EmptyLocalPayload> {
        try await localRequest([
            "protocol_version": 2, "operation": "CONTEXT", "request_id": UUID().uuidString
        ])
    }

    public func manualWorkflow(_ taskId: String) async throws -> ManualWorkflowView {
        let response: LocalEnvelope<ManualWorkflowView> = try await localRequest([
            "protocol_version": 2, "operation": "WORKFLOW", "request_id": UUID().uuidString,
            "task_id": taskId
        ])
        return response.payload
    }

    public func retryManualTask(task: TaskView, priorDispatchId: String,
                                operation: ManualRecoveryOperation = .retry) async throws {
        let context = try await localContext()
        let _: LocalEnvelope<EmptyLocalPayload> = try await localRequest([
            "protocol_version": 2, "operation": operation.rawValue, "request_id": UUID().uuidString,
            "task_id": task.taskId, "task_state_version": task.stateVersion,
            "prior_dispatch_id": priorDispatchId,
            "expected_store_id": context.storeId, "expected_process_epoch": context.processEpoch
        ])
    }

    private func evidenceQuery(_ evidenceId: String?) throws -> String {
        guard let evidenceId else { return "" }
        guard evidenceId.count == 31, evidenceId.hasPrefix("verify-"),
              evidenceId.dropFirst(7).allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw PAOClientError.malformedResponse
        }
        return "?evidence_id=\(evidenceId)"
    }

    public func taskVerificationHistory(_ taskId: String) async throws -> TaskVerificationHistoryView {
        let result: TaskVerificationHistoryView = try await perform(
            method: "GET", path: "/v1/tasks/\(taskId)/verification-history", encodedBody: nil
        )
        guard result.taskId == taskId else { throw PAOClientError.malformedResponse }
        return result
    }

    public func taskHistoricalResult(_ taskId: String, evidenceId: String) async throws -> CrossClientResultView {
        let response: CrossClientResultPayload = try await perform(
            method: "GET",
            path: "/v1/tasks/\(taskId)/verification-history\(try evidenceQuery(evidenceId))",
            encodedBody: nil
        )
        guard response.result.binding.taskId == taskId,
              response.result.verification.evidenceId == evidenceId,
              ["HISTORICAL_VERIFIED", "HISTORICAL_FAILED"].contains(response.result.verification.status) else {
            throw PAOClientError.malformedResponse
        }
        return response.result
    }

    public func taskPatch(_ taskId: String, evidenceId: String? = nil) async throws -> TaskPatchView {
        let result: TaskPatchView = try await perform(
            method: "GET", path: "/v1/tasks/\(taskId)/patch\(try evidenceQuery(evidenceId))", encodedBody: nil
        )
        guard result.taskId == taskId, evidenceId == nil || result.evidenceId == evidenceId else {
            throw PAOClientError.malformedResponse
        }
        return result
    }

    public func taskPreparation(_ taskId: String) async throws -> TaskPreparationView {
        try await perform(method: "GET", path: "/v1/tasks/\(taskId)/preparation", encodedBody: nil)
    }

    public func taskCrossClientResult(_ taskId: String) async throws -> CrossClientResultView {
        let response: LocalEnvelope<CrossClientResultPayload> = try await localRequest([
            "protocol_version": 2, "operation": "DETAIL", "request_id": UUID().uuidString,
            "task_id": taskId
        ])
        guard response.payload.result.binding.taskId == taskId else {
            throw PAOClientError.malformedResponse
        }
        return response.payload.result
    }
}
