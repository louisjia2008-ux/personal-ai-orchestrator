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

/// One process-wide serialization point for automation-mode writes.
///
/// Swift actors are re-entrant across `await`, so actor isolation alone is NOT
/// sufficient for a GET-then-PUT transaction. The explicit task chain below
/// prevents a second surface from starting its authoritative GET until the prior
/// mutation has fully completed. This closes the stale read/PUT race between
/// Home, Settings and the menu-bar emergency stop.
public actor AutomationModeMutationCoordinator {
    public static let shared = AutomationModeMutationCoordinator()

    private var mutationTail: Task<Void, Never>?

    public init() {}

    public func setMode(
        socketPath: String,
        mode: String
    ) async throws -> SchedulingSettingsView {
        let predecessor = mutationTail
        let operation = Task<SchedulingSettingsView, Error> {
            await predecessor?.value
            let client = PAOControlClient(socketPath: socketPath)
            let current = try await client.schedulingSettings()
            return try await client.setSchedulingMode(
                mode,
                defaultSchedulingPolicy: current.defaultSchedulingPolicy
            )
        }
        mutationTail = Task {
            _ = try? await operation.value
        }
        return try await operation.value
    }
}

/// Project automation writes are full-tuple PUTs. Actor isolation alone would
/// still be re-entrant while the network GET/PUT awaits, so these transactions
/// use the same explicit completion chain: a later edit cannot resolve siblings
/// until every earlier full-tuple mutation has committed or failed.
public actor ProjectAutomationMutationCoordinator {
    public static let shared = ProjectAutomationMutationCoordinator()

    private var mutationTail: Task<Void, Never>?

    public init() {}

    /// Returns nil only when the project disappeared between presentation and
    /// mutation. Optional arguments mean "leave this field authoritative".
    public func update(
        socketPath: String,
        projectId: String,
        supervisedAutoAllowed: Bool? = nil,
        unattendedAllowed: Bool? = nil,
        graceSeconds: Int? = nil
    ) async throws -> ProjectView? {
        let predecessor = mutationTail
        let operation = Task<ProjectView?, Error> {
            await predecessor?.value
            let client = PAOControlClient(socketPath: socketPath)
            let projects = try await client.projects()
            guard let current = projects.projects.first(where: { $0.projectId == projectId }) else {
                return nil
            }
            return try await client.setProjectSupervisedAutoSettings(
                projectId: projectId,
                supervisedAutoAllowed: supervisedAutoAllowed ?? current.supervisedAutoAllowed,
                unattendedAllowed: unattendedAllowed ?? current.unattendedAllowed,
                graceSeconds: graceSeconds ?? current.graceSeconds
            )
        }
        mutationTail = Task {
            _ = try? await operation.value
        }
        return try await operation.value
    }
}
