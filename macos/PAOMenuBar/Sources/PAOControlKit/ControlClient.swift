import Foundation

#if canImport(Glibc)
import Glibc
#endif

/// Deterministic HTTP/1.1-over-Unix-Domain-Socket transport for the P4 control API.
///
/// The daemon answers with `Connection: close` semantics (HTTP/1.0-style), so one request
/// per connection with read-until-EOF is the complete and safe framing model.
public struct PAOControlClient: Sendable {
    public let socketPath: String
    public let timeoutSeconds: Double

    public init(socketPath: String, timeoutSeconds: Double = 10.0) {
        self.socketPath = socketPath
        self.timeoutSeconds = timeoutSeconds
    }

    // MARK: - Typed API surface

    public func health() async throws -> HealthView {
        try await get("/v1/health")
    }

    public func build() async throws -> BuildView {
        try await get("/v1/build")
    }

    public func listTasks(limit: Int? = nil) async throws -> TaskListView {
        let path = limit.map { "/v1/tasks?limit=\($0)" } ?? "/v1/tasks"
        return try await get(path)
    }

    public func dashboard() async throws -> DashboardSummaryView {
        try await get("/v1/dashboard")
    }

    public func projects() async throws -> ProjectListView {
        try await get("/v1/projects")
    }

    public func resolveProject(path: String) async throws -> ProjectView {
        try await post("/v1/projects/resolve", body: ProjectPathRequest(path: path))
    }

    public func registerProject(
        path: String,
        displayName: String? = nil,
        securityBookmarkB64: String? = nil
    ) async throws -> ProjectView {
        try await post(
            "/v1/projects",
            body: ProjectRegisterRequest(
                path: path,
                displayName: displayName,
                securityBookmarkB64: securityBookmarkB64
            )
        )
    }

    public func markProjectOpened(_ projectId: String) async throws -> ProjectView {
        struct EmptyBody: Encodable {}
        return try await post("/v1/projects/\(projectId)/opened", body: EmptyBody())
    }

    public func removeProject(_ projectId: String) async throws -> ProjectRemoveView {
        struct EmptyBody: Encodable {}
        return try await post("/v1/projects/\(projectId)/remove", body: EmptyBody())
    }

    public func getTask(_ taskId: String) async throws -> TaskView {
        try await get("/v1/tasks/\(taskId)")
    }

    public func taskDetail(_ taskId: String) async throws -> TaskDetailView {
        try await get("/v1/tasks/\(taskId)/detail")
    }

    public func runs(taskId: String) async throws -> RunListView {
        try await get("/v1/tasks/\(taskId)/runs")
    }

    public func verificationReport(taskId: String) async throws -> VerificationReportView {
        try await get("/v1/tasks/\(taskId)/verification")
    }

    public func routingDecision(taskId: String) async throws -> RoutingDecisionView {
        try await get("/v1/tasks/\(taskId)/routing")
    }

    public func submit(_ request: SubmitRequest) async throws -> TaskView {
        try await post("/v1/tasks", body: request)
    }

    public func cancel(taskId: String, requestId: String? = nil) async throws -> CancelView {
        struct CancelBody: Encodable {
            let requestId: String?

            enum CodingKeys: String, CodingKey {
                case requestId = "request_id"
            }
        }
        return try await post("/v1/tasks/\(taskId)/cancel", body: CancelBody(requestId: requestId))
    }

    public func dispatch(taskId: String, request: DispatchRequest) async throws -> DispatchTaskView {
        try await post("/v1/tasks/\(taskId)/dispatch", body: request)
    }

    public func getDispatch(requestId: String) async throws -> DispatchTaskView {
        try await get("/v1/dispatches/\(requestId)")
    }

    public func ownerExecutionSettings() async throws -> OwnerExecutionSettingsView {
        try await get("/v1/settings/owner-execution")
    }

    public func setOwnerExecutionEnabled(_ enabled: Bool) async throws -> OwnerExecutionSettingsView {
        struct EnabledBody: Encodable {
            let ownerInitiatedExecutionEnabled: Bool

            enum CodingKeys: String, CodingKey {
                case ownerInitiatedExecutionEnabled = "owner_initiated_execution_enabled"
            }
        }
        let body = EnabledBody(ownerInitiatedExecutionEnabled: enabled)
        let data = try await rawRequest(
            method: "PUT",
            path: "/v1/settings/owner-execution",
            body: JSONEncoder().encode(body)
        )
        do {
            return try JSONDecoder().decode(OwnerExecutionSettingsView.self, from: data)
        } catch {
            throw PAOClientError.malformedResponse
        }
    }

    public func providers() async throws -> ProviderHealthListView {
        try await get("/v1/providers")
    }

    public func providerConnections() async throws -> ProviderConnectionListView {
        try await get("/v1/provider-connections")
    }

    public func connectProvider(providerId: String) async throws -> ProviderConnectionView {
        struct ConnectBody: Encodable {
            let providerId: String

            enum CodingKeys: String, CodingKey {
                case providerId = "provider_id"
            }
        }
        return try await post(
            "/v1/provider-connections",
            body: ConnectBody(providerId: providerId)
        )
    }

    public func disconnectProvider(providerId: String) async throws -> ProviderConnectionView {
        struct DisconnectBody: Encodable {
            let confirm: Bool
        }
        return try await post(
            "/v1/provider-connections/\(providerId)/disconnect",
            body: DisconnectBody(confirm: true)
        )
    }

    /// Materialise owner-approved import candidates as real connections.
    ///
    /// The daemon re-validates every id against current evidence, so a stale App
    /// view cannot import a surface whose evidence has since disappeared.
    public func importProviderConnections(
        providerIds: [String]
    ) async throws -> ImportConnectionsView {
        struct ImportBody: Encodable {
            let providerIds: [String]

            enum CodingKeys: String, CodingKey {
                case providerIds = "provider_ids"
            }
        }
        return try await post(
            "/v1/provider-connections/import",
            body: ImportBody(providerIds: providerIds)
        )
    }

    public func schedulingSettings() async throws -> SchedulingSettingsView {
        try await get("/v1/settings/scheduling")
    }

    public func setDefaultSchedulingPolicy(
        _ policy: String
    ) async throws -> SchedulingSettingsView {
        struct PolicyBody: Encodable {
            let defaultSchedulingPolicy: String

            enum CodingKeys: String, CodingKey {
                case defaultSchedulingPolicy = "default_scheduling_policy"
            }
        }
        let data = try await rawRequest(
            method: "PUT",
            path: "/v1/settings/scheduling",
            body: JSONEncoder().encode(PolicyBody(defaultSchedulingPolicy: policy))
        )
        do {
            return try JSONDecoder().decode(SchedulingSettingsView.self, from: data)
        } catch {
            throw PAOClientError.malformedResponse
        }
    }

    /// `schedulingPolicy: nil` clears the project override, restoring the global default.
    public func setProjectSchedulingPolicy(
        projectId: String,
        schedulingPolicy: String?,
        manualExecutionTargetId: String? = nil
    ) async throws -> ProjectView {
        struct ProjectPolicyBody: Encodable {
            let schedulingPolicy: String?
            let manualExecutionTargetId: String?

            enum CodingKeys: String, CodingKey {
                case schedulingPolicy = "scheduling_policy"
                case manualExecutionTargetId = "manual_execution_target_id"
            }
        }
        let body = ProjectPolicyBody(
            schedulingPolicy: schedulingPolicy,
            manualExecutionTargetId: manualExecutionTargetId
        )
        let data = try await rawRequest(
            method: "PUT",
            path: "/v1/projects/\(projectId)/scheduling",
            body: JSONEncoder().encode(body)
        )
        do {
            return try JSONDecoder().decode(ProjectView.self, from: data)
        } catch {
            throw PAOClientError.malformedResponse
        }
    }

    public func providerDiscoveryStatus() async throws -> ProviderDiscoveryStatusView {
        try await get("/v1/providers/status")
    }

    public func refreshProviders() async throws -> ProviderDiscoveryStatusView {
        struct EmptyBody: Encodable {}
        return try await post("/v1/providers/refresh", body: EmptyBody())
    }

    /// Connection-based quota projection: every connected provider is present,
    /// including ones with no quota evidence yet.
    public func quota() async throws -> QuotaOverviewView {
        try await get("/v1/quota")
    }

    /// Explicit read-only quota collection. Distinct from `refreshProviders()`,
    /// which only re-runs catalog/credential discovery and reads no quota.
    public func refreshQuota(providerId: String? = nil) async throws -> QuotaRefreshResultView {
        struct EmptyBody: Encodable {}
        let path = providerId.map { "/v1/providers/\($0)/quota/refresh" } ?? "/v1/quota/refresh"
        return try await post(path, body: EmptyBody())
    }

    public func activeStatus() async throws -> ActiveStatusView {
        try await get("/v1/active-status")
    }

    public func approvals(taskId: String) async throws -> ApprovalListView {
        try await get("/v1/tasks/\(taskId)/approvals")
    }

    // MARK: - Transport

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try await perform(method: "GET", path: path, encodedBody: nil)
    }

    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await perform(method: "POST", path: path, encodedBody: JSONEncoder().encode(body))
    }

    public func perform<T: Decodable>(
        method: String,
        path: String,
        encodedBody: Data?
    ) async throws -> T {
        let data = try await rawRequest(method: method, path: path, body: encodedBody ?? Data())
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw PAOClientError.malformedResponse
        }
    }

    public func rawRequest(method: String, path: String, body: Data) async throws -> Data {
        let request = PAOHTTP.encodeRequest(method: method, path: path, body: body)
        let responseData = try await PAOSocket.send(socketPath: socketPath, request: request,
                                                    timeoutSeconds: timeoutSeconds)
        guard let response = PAOHTTP.parseResponse(responseData) else {
            throw PAOClientError.malformedResponse
        }
        guard let code = response.errorCode else {
            if response.status >= 400 {
                throw PAOClientError.httpError(status: response.status, code: "error")
            }
            return response.body
        }
        throw PAOClientError.httpError(status: response.status, code: code)
    }
}

public enum PAOHTTP {
    public static func encodeRequest(method: String, path: String, body: Data) -> Data {
        var request = "\(method) \(path) HTTP/1.1\r\n"
        request += "Host: localhost\r\n"
        request += "Content-Type: application/json\r\n"
        request += "Content-Length: \(body.count)\r\n"
        request += "Connection: close\r\n"
        request += "\r\n"
        var data = Data(request.utf8)
        data.append(body)
        return data
    }

    public struct ParsedResponse {
        public let status: Int
        public let body: Data
        public let errorCode: String?
    }

    public static func parseResponse(_ data: Data) -> ParsedResponse? {
        guard let headerEnd = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let headerData = data[..<headerEnd.lowerBound]
        guard let headerText = String(data: headerData, encoding: .utf8) else { return nil }
        let lines = headerText.components(separatedBy: "\r\n")
        guard let statusLine = lines.first else { return nil }
        let parts = statusLine.split(separator: " ")
        guard parts.count >= 2, let status = Int(parts[1]) else { return nil }
        var errorCode: String?
        if let bodyText = String(data: data[headerEnd.upperBound...], encoding: .utf8),
           let payload = try? JSONSerialization.jsonObject(with: Data(bodyText.utf8)) as? [String: Any],
           let code = payload["error"] as? String {
            errorCode = code
        }
        return ParsedResponse(status: status, body: data[headerEnd.upperBound...], errorCode: errorCode)
    }
}

enum PAOSocket {
    static func send(socketPath: String, request: Data, timeoutSeconds: Double) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let data = try sendSync(socketPath: socketPath, request: request,
                                            timeoutSeconds: timeoutSeconds)
                    continuation.resume(returning: data)
                } catch let error as PAOClientError {
                    continuation.resume(throwing: error)
                } catch {
                    continuation.resume(throwing: PAOClientError.malformedResponse)
                }
            }
        }
    }

    static func sendSync(socketPath: String, request: Data, timeoutSeconds: Double) throws -> Data {
        let bytes = socketPath.utf8
        guard bytes.count <= 104 else {
            throw PAOClientError.socketPathTooLong(bytes.count)
        }
        guard socketPath.hasPrefix("/") else {
            throw PAOClientError.invalidSocketPath
        }

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw PAOClientError.malformedResponse
        }
        defer { close(fd) }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(bytes)
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            destination.copyBytes(from: pathBytes)
        }
        let connectResult = withUnsafePointer(to: &address) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                connect(fd, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connectResult == 0 else {
            throw mapConnectError(errno)
        }

        var tv = timeval(tv_sec: Int(timeoutSeconds), tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        // A peer closing mid-write must surface as an error, never as a fatal SIGPIPE.
        var noSigPipe: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))

        try request.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            var sent = 0
            while sent < raw.count {
                let written = write(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent)
                if written < 0 && errno == EINTR {
                    continue
                }
                if written <= 0 {
                    throw PAOClientError.transportFailure
                }
                sent += written
            }
        }

        var response = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let readCount = read(fd, &buffer, buffer.count)
            if readCount < 0 && errno == EINTR {
                continue
            }
            if readCount < 0 {
                throw PAOClientError.transportFailure
            }
            if readCount == 0 {
                break
            }
            response.append(contentsOf: buffer[..<readCount])
            if response.count > 8 * 1024 * 1024 {
                throw PAOClientError.malformedResponse
            }
        }
        return response
    }

    static func mapConnectError(_ err: Int32) -> PAOClientError {
        switch err {
        case ENOENT:
            return .daemonNotRunning
        case ECONNREFUSED:
            return .staleSocket
        case EACCES, EPERM:
            return .accessDenied
        default:
            return .transportFailure
        }
    }
}
