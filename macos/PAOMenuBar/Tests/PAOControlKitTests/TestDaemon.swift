import Foundation

import XCTest

@testable import PAOControlKit

/// Minimal in-test Unix Domain Socket HTTP server speaking the same HTTP/1.0-close
/// framing the real daemon uses. Routes are registered as canned responses.
final class TestDaemon {
    struct Route {
        let method: String
        let path: String
        let status: Int
        let body: String
    }

    private var routes: [Route] = []
    private var thread: Thread?
    private var serverFD: Int32 = -1
    private let lock = NSLock()
    private(set) var receivedRequests: [(method: String, path: String, body: String)] = []
    private var running = false

    func route(_ method: String, _ path: String, status: Int = 200, body: String) {
        routes.append(Route(method: method, path: path, status: status, body: body))
    }

    func start(socketPath: String) throws {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw NSError(domain: "socket", code: Int(errno)) }
        unlink(socketPath)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8)
        precondition(pathBytes.count <= 104)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: pathBytes) }
        let bindResult = withUnsafePointer(to: &address) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.bind(fd, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bindResult == 0, listen(fd, 8) == 0 else {
            close(fd)
            throw NSError(domain: "bind", code: Int(errno))
        }
        serverFD = fd
        running = true
        thread = Thread { [weak self] in self?.serve() }
        thread?.start()
    }

    func stop() {
        running = false
        if serverFD >= 0 {
            close(serverFD)
            serverFD = -1
        }
    }

    private func serve() {
        while running {
            let clientFD = accept(serverFD, nil, nil)
            if clientFD < 0 { break }
            var tv = timeval(tv_sec: 2, tv_usec: 0)
            setsockopt(clientFD, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            DispatchQueue.global().async { [weak self] in
                self?.handle(clientFD)
            }
        }
    }

    private func handle(_ fd: Int32) {
        defer { close(fd) }
        var buffer = [UInt8](repeating: 0, count: 65536)
        var data = Data()
        var tv = timeval(tv_sec: 2, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        while true {
            let n = read(fd, &buffer, buffer.count)
            if n <= 0 { break }
            data.append(contentsOf: buffer[..<n])
            if let headerEnd = data.range(of: Data("\r\n\r\n".utf8)) {
                let header = String(data: data[..<headerEnd.lowerBound], encoding: .utf8) ?? ""
                let contentLength = header
                    .components(separatedBy: "\r\n")
                    .compactMap { line -> Int? in
                        guard line.lowercased().hasPrefix("content-length:") else { return nil }
                        return Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces))
                    }
                    .first ?? 0
                if data.count - headerEnd.upperBound >= contentLength { break }
            }
        }
        guard let requestText = String(data: data, encoding: .utf8),
              let requestLine = requestText.components(separatedBy: "\r\n").first else { return }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else { return }
        let method = String(parts[0])
        let path = String(parts[1])
        var body = ""
        if let headerEnd = data.range(of: Data("\r\n\r\n".utf8)) {
            body = String(data: data[headerEnd.upperBound...], encoding: .utf8) ?? ""
        }
        lock.lock()
        receivedRequests.append((method, path, body))
        let route = routes.first { $0.method == method && $0.path == path }
        lock.unlock()
        let status = route?.status ?? 404
        let payload = route?.body ?? "{\"error\":\"not_found\"}"
        let response = "HTTP/1.0 \(status) X\r\nContent-Type: application/json\r\n"
            + "Content-Length: \(payload.utf8.count)\r\n\r\n\(payload)"
        let responseData = Data(response.utf8)
        responseData.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            var sent = 0
            while sent < raw.count {
                let written = write(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent)
                if written <= 0 { return }
                sent += written
            }
        }
    }
}

func temporarySocketPath(_ name: String) -> String {
    let base = NSTemporaryDirectory() as NSString
    return base.appendingPathComponent("pao-tests-\(name)-\(UUID().uuidString.prefix(8)).sock")
}

let healthBody = """
{"status":"ok","api_version":"v1"}
"""

let tasksBody = """
{"tasks":[{"task_id":"t-1","request_id":"r-1","intent":"fix bug","state":"RUNNING","state_version":2,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"},{"task_id":"t-2","request_id":"r-2","intent":"add test","state":"BLOCKED","state_version":3,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:02:00Z"}],"total":2}
"""

let providersBody = """
{"providers":[{"provider_id":"minimax","display_name":"MiniMax CN","account_count":1,"quota_pools":[{"quota_pool_id":"pool","name":"shared","plan_id":"plan","state":"AVAILABLE","confidence":"ESTIMATED","measurement_source_type":"PROVIDER_API","observed_at":"2026-08-31T00:00:00Z","windows":[{"window_id":"5h","window_kind":"FIVE_HOUR","state":"AVAILABLE","confidence":"ESTIMATED","remaining_fraction":0.8,"reset_at":null}]}],"execution_targets":[{"execution_target_id":"m3-sub","model_sku_id":"m3","runtime_id":"opencode","enabled":true,"runtime_available":true,"observed_availability":{"state":"EXHAUSTED_OBSERVED","observed_at":"2026-08-31T00:00:00Z","measurement_source":"LOCALLY_MEASURED","confidence":"ESTIMATED","sanitized_reason_code":"USAGE_LIMIT"}}]}]}
"""

let activeStatusBody = """
{"production_active":"DISABLED_BY_DESIGN","authorized":false,"blocking_reasons":["explicit owner approval missing","P3.5 Shadow evidence not accepted"],"gate":{"owner_approved":false}}
"""

let submitResponseBody = """
{"task_id":"menubar-abc","request_id":"menubar-req-abc","intent":"demo","state":"SUBMITTED","state_version":0,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:00:00Z"}
"""

let dashboardBody = """
{"connection":{"status":"ok","api_version":"v1"},"counts":{"running":1,"ready":0,"blocked":1,"verified":0,"completed":0,"total":2},"recent_tasks":[{"task_id":"t-1","request_id":"r-1","intent":"fix bug","state":"RUNNING","state_version":2,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"},{"task_id":"t-2","request_id":"r-2","intent":"add test","state":"BLOCKED","state_version":3,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:02:00Z"}],"providers":{"providers":[{"provider_id":"minimax","display_name":"MiniMax CN","account_count":1,"quota_pools":[{"quota_pool_id":"pool","name":"shared","plan_id":"plan","state":"AVAILABLE","confidence":"ESTIMATED","measurement_source_type":"PROVIDER_API","observed_at":"2026-08-31T00:00:00Z","windows":[{"window_id":"5h","window_kind":"FIVE_HOUR","state":"AVAILABLE","confidence":"ESTIMATED","remaining_fraction":0.8,"reset_at":null}]}],"execution_targets":[{"execution_target_id":"m3-sub","model_sku_id":"m3","runtime_id":"opencode","enabled":true,"runtime_available":true,"observed_availability":{"state":"EXHAUSTED_OBSERVED","observed_at":"2026-08-31T00:00:00Z","measurement_source":"LOCALLY_MEASURED","confidence":"ESTIMATED","sanitized_reason_code":"USAGE_LIMIT"}}]}]},"active_status":{"production_active":"DISABLED_BY_DESIGN","authorized":false,"blocking_reasons":["explicit owner approval missing","P3.5 Shadow evidence not accepted"],"gate":{"owner_approved":false}},"important_blockers":["explicit owner approval missing"],"recent_events":[{"event_type":"TASK_SUBMITTED","task_id":"t-1","created_at":"2026-08-31T00:00:00Z","summary":"task submitted"}]}
"""

let taskDetailBody = """
{"task":{"task_id":"t-1","request_id":"r-1","intent":"fix bug","state":"RUNNING","state_version":2,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"},"runs":[{"run_id":"run-1","task_id":"t-1","worker_id":"m3-sub","pid":123,"status":"RUNNING","started_at":"2026-08-31T00:00:30Z","finished_at":null}],"routing":{"task_id":"t-1","decision_id":"route-1","request_id":"r-1","created_at":"2026-08-31T00:00:15Z","decision":{"mode":"SHADOW","selected_execution_target_id":null,"fallback_reason":"quota confidence remained UNKNOWN"}},"verification":{"task_id":"t-1","task_state":"RUNNING","status":"NOT_VERIFIED","evidence_id":null,"failure_reason":null},"approvals":{"approvals":[]},"workspace":{"task_id":"t-1","repo_path":"/repo","worktree_path":"/repo-wt","branch":"codex/t-1","base_sha":"abc123","writer_locked":false},"events":[{"event_type":"TASK_SUBMITTED","task_id":"t-1","created_at":"2026-08-31T00:00:00Z","summary":"task submitted"}]}
"""

func registerStandardRoutes(_ daemon: TestDaemon) {
    daemon.route("GET", "/v1/health", body: healthBody)
    daemon.route("GET", "/v1/dashboard", body: dashboardBody)
    daemon.route("GET", "/v1/tasks?limit=20", body: tasksBody)
    daemon.route("GET", "/v1/tasks", body: tasksBody)
    daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody)
    daemon.route("GET", "/v1/providers", body: providersBody)
    daemon.route("GET", "/v1/quota", body: providersBody)
    daemon.route("GET", "/v1/active-status", body: activeStatusBody)
}
