import Foundation

public extension PAOControlClient {
    /// Change the orchestrator automation mode while preserving the daemon's
    /// authoritative routing policy. The caller must first read current
    /// scheduling settings; passing a client-side fallback would silently
    /// rewrite owner policy.
    func setSchedulingMode(
        _ mode: String,
        defaultSchedulingPolicy: String
    ) async throws -> SchedulingSettingsView {
        struct ModeBody: Encodable {
            let defaultSchedulingPolicy: String
            let mode: String

            enum CodingKeys: String, CodingKey {
                case defaultSchedulingPolicy = "default_scheduling_policy"
                case mode
            }
        }
        return try await perform(
            method: "PUT",
            path: "/v1/settings/scheduling",
            encodedBody: JSONEncoder().encode(
                ModeBody(defaultSchedulingPolicy: defaultSchedulingPolicy, mode: mode)
            )
        )
    }

    /// Acknowledge an unacked AUTO_GRACE task at the exact authoritative state
    /// version fetched immediately before submission.
    func autoAck(
        taskId: String,
        taskStateVersion: Int
    ) async throws -> TaskView {
        struct Body: Encodable {
            let taskStateVersion: Int
            enum CodingKeys: String, CodingKey {
                case taskStateVersion = "task_state_version"
            }
        }
        return try await perform(
            method: "POST",
            path: "/v1/tasks/\(taskId)/auto/ack",
            encodedBody: JSONEncoder().encode(Body(taskStateVersion: taskStateVersion))
        )
    }

    /// Veto an AUTO_PLANNED/AUTO_GRACE lifecycle. requestId is the durable
    /// idempotency key for this owner operation.
    func autoVeto(
        taskId: String,
        requestId: String,
        taskStateVersion: Int
    ) async throws -> TaskView {
        struct Body: Encodable {
            let requestId: String
            let taskStateVersion: Int
            enum CodingKeys: String, CodingKey {
                case requestId = "request_id"
                case taskStateVersion = "task_state_version"
            }
        }
        return try await perform(
            method: "POST",
            path: "/v1/tasks/\(taskId)/auto/veto",
            encodedBody: JSONEncoder().encode(
                Body(requestId: requestId, taskStateVersion: taskStateVersion)
            )
        )
    }

    /// Accelerate a live supervised-auto grace window. The daemon still owns
    /// every execution-admission gate and returns shared dispatch truth.
    func autoDispatchNow(
        taskId: String,
        taskStateVersion: Int
    ) async throws -> DispatchTaskView {
        struct Body: Encodable {
            let taskStateVersion: Int
            enum CodingKeys: String, CodingKey {
                case taskStateVersion = "task_state_version"
            }
        }
        return try await perform(
            method: "POST",
            path: "/v1/tasks/\(taskId)/auto/dispatch-now",
            encodedBody: JSONEncoder().encode(Body(taskStateVersion: taskStateVersion))
        )
    }

    /// Persist the complete project supervised-auto tuple. The caller resolves
    /// unchanged siblings from the authoritative current ProjectView so a
    /// single UI edit cannot reset another field.
    func setProjectSupervisedAutoSettings(
        projectId: String,
        supervisedAutoAllowed: Bool,
        unattendedAllowed: Bool,
        graceSeconds: Int
    ) async throws -> ProjectView {
        struct Body: Encodable {
            let supervisedAutoAllowed: Bool
            let unattendedAllowed: Bool
            let graceSeconds: Int
            enum CodingKeys: String, CodingKey {
                case supervisedAutoAllowed = "supervised_auto_allowed"
                case unattendedAllowed = "unattended_allowed"
                case graceSeconds = "grace_seconds"
            }
        }
        return try await perform(
            method: "PUT",
            path: "/v1/projects/\(projectId)/settings",
            encodedBody: JSONEncoder().encode(
                Body(
                    supervisedAutoAllowed: supervisedAutoAllowed,
                    unattendedAllowed: unattendedAllowed,
                    graceSeconds: graceSeconds
                )
            )
        )
    }
}
