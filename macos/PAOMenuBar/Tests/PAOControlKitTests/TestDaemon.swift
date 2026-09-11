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

/// Build a /v1/health canned body that surfaces the WP0 supervisor fields.
/// Pass ``lastTickAt`` and ``intervalSeconds`` to assert ordering / cadence;
/// leave them empty when the test only needs the supervisor step list.
func supervisorHealthBody(
    lastTickAt: String = "2026-09-05T00:00:00Z",
    intervalSeconds: Double = 5.0,
    steps: [(name: String, lastRunAt: String, lastDurationMs: Double, consecutiveFailures: Int, inBackoff: Bool)] = [
        ("heartbeat", "2026-09-05T00:00:00Z", 0.3, 0, false)
    ]
) -> String {
    let stepJSON = steps
        .map { step in
            """
            {"name":"\(step.name)","last_run_at":"\(step.lastRunAt)","last_duration_ms":\(step.lastDurationMs),"consecutive_failures":\(step.consecutiveFailures),"in_backoff":\(step.inBackoff ? "true" : "false")}
            """
        }
        .joined(separator: ",")
    return """
    {"status":"ok","api_version":"v1","last_tick_at":"\(lastTickAt)","tick_interval_seconds":\(intervalSeconds),"supervisor_steps":[\(stepJSON)]}
    """
}

let tasksBody = """
{"tasks":[{"task_id":"t-1","request_id":"r-1","intent":"fix bug","state":"RUNNING","state_version":2,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"},{"task_id":"t-2","request_id":"r-2","intent":"add test","state":"BLOCKED","state_version":3,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:02:00Z"}],"total":2}
"""

let providersBody = """
{"providers":[{"provider_id":"minimax","display_name":"MiniMax CN","account_count":1,"quota_pools":[{"quota_pool_id":"pool","name":"shared","plan_id":"plan","state":"AVAILABLE","confidence":"ESTIMATED","measurement_source_type":"PROVIDER_API","observed_at":"2026-08-31T00:00:00Z","windows":[{"window_id":"5h","window_kind":"FIVE_HOUR","state":"AVAILABLE","confidence":"ESTIMATED","remaining_fraction":0.8,"reset_at":null}]}],"execution_targets":[{"execution_target_id":"m3-sub","model_sku_id":"m3","runtime_id":"opencode","enabled":true,"execution_verified":false,"runtime_available":true,"observed_availability":{"state":"EXHAUSTED_OBSERVED","observed_at":"2026-08-31T00:00:00Z","measurement_source":"LOCALLY_MEASURED","confidence":"ESTIMATED","sanitized_reason_code":"USAGE_LIMIT"}}]}]}
"""

let providerDiscoveryStatusBody = """
{"discovery_state":"DISCOVERED","last_discovered_at":"2026-08-31T00:00:00Z","provider_count":5,"execution_target_count":35,"last_error_code":null,"catalog_snapshot_id":"1.18.25","source_method":"opencode_cli_inspection"}
"""

let activeStatusBody = """
{"production_active":"DISABLED_BY_DESIGN","authorized":false,"blocking_reasons":["explicit owner approval missing","P3.5 Shadow evidence not accepted"],"gate":{"owner_approved":false}}
"""

let submitResponseBody = """
{"task_id":"menubar-abc","request_id":"menubar-req-abc","intent":"demo","project_id":"project-fixture","base_sha":"abc123","working_subpath":null,"state":"SUBMITTED","state_version":0,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:00:00Z"}
"""

let projectsBody = """
{"projects":[{"project_id":"project-fixture","display_name":"Fixture","canonical_repo_root":"/repo","git_root":"/repo","default_branch":"main","last_known_head":"abc123","created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:00:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":2,"current_branch":"main"}]}
"""

let dashboardBody = """
{
  "connection": {"status": "ok", "api_version": "v1"},
  "counts": {"running": 1, "ready": 0, "blocked": 1, "verifying": 0, "verified": 0, "completed": 0, "total": 2},
  "recent_tasks": [
    {"task_id": "t-1", "request_id": "r-1", "intent": "fix bug", "project_id": "project-fixture", "base_sha": "abc123", "working_subpath": null, "state": "RUNNING", "state_version": 2, "created_at": "2026-08-31T00:00:00Z", "updated_at": "2026-08-31T00:01:00Z"},
    {"task_id": "t-2", "request_id": "r-2", "intent": "add test", "project_id": "project-fixture", "base_sha": "abc123", "working_subpath": null, "state": "BLOCKED", "state_version": 3, "created_at": "2026-08-31T00:00:00Z", "updated_at": "2026-08-31T00:02:00Z"}
  ],
  "projects": {
    "projects": [
      {"project_id": "project-fixture", "display_name": "Fixture", "canonical_repo_root": "/repo", "git_root": "/repo", "default_branch": "main", "last_known_head": "abc123", "created_at": "2026-08-31T00:00:00Z", "updated_at": "2026-08-31T00:00:00Z", "working_subpath": null, "remote_url": null, "last_opened_at": null, "storage_availability": "ONLINE", "recent_task_count": 2, "current_branch": "main"}
    ]
  },
  "basic_info": {
    "daemon_connection": "CONNECTED",
    "registered_projects": 1,
    "discovered_providers": 1,
    "available_execution_targets": 1,
    "running_tasks": 1,
    "tasks_today": 2,
    "routing_decisions_today": 1,
    "quota_warning_count": 0,
    "last_refresh_sync": "2026-08-31T00:03:00Z"
  },
  "task_trend": [
    {"bucket_start": "2026-08-31T00:00:00Z", "submitted": 2, "completed": 0, "blocked": 1},
    {"bucket_start": "2026-08-31T01:00:00Z", "submitted": 0, "completed": 0, "blocked": 0}
  ],
  "task_state_distribution": [
    {"state": "BLOCKED", "count": 1},
    {"state": "RUNNING", "count": 1}
  ],
  "risks": [
    {"title": "1 execution target(s) are not verified", "detail": "Unverified targets cannot run real owner-dispatched tasks.", "severity": "WARNING", "destination": "models_providers", "raw_code": "EXECUTION_TARGET_UNVERIFIED"}
  ],
  "quota_history": {
    "retention_limit": 500,
    "observations": [
      {"provider_id": "minimax", "quota_pool_id": "pool", "window_id": "5h", "observed_at": "2026-08-31T00:00:00Z", "remaining_fraction": 0.8, "confidence": "ESTIMATED", "measurement_source": "PROVIDER_API", "reset_at": null, "state": "AVAILABLE"},
      {"provider_id": "minimax", "quota_pool_id": "pool", "window_id": "5h", "observed_at": "2026-08-31T01:00:00Z", "remaining_fraction": 0.7, "confidence": "ESTIMATED", "measurement_source": "PROVIDER_API", "reset_at": null, "state": "AVAILABLE"}
    ]
  },
  "providers": {
    "providers": [
      {"provider_id": "minimax", "display_name": "MiniMax CN", "account_count": 1, "quota_pools": [{"quota_pool_id": "pool", "name": "shared", "plan_id": "plan", "state": "AVAILABLE", "confidence": "ESTIMATED", "measurement_source_type": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z", "windows": [{"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE", "confidence": "ESTIMATED", "remaining_fraction": 0.8, "reset_at": null}]}], "execution_targets": [{"execution_target_id": "m3-sub", "model_sku_id": "m3", "runtime_id": "opencode", "enabled": true, "execution_verified": false, "runtime_available": true, "observed_availability": {"state": "EXHAUSTED_OBSERVED", "observed_at": "2026-08-31T00:00:00Z", "measurement_source": "LOCALLY_MEASURED", "confidence": "ESTIMATED", "sanitized_reason_code": "USAGE_LIMIT"}}]}
    ]
  },
  "active_status": {"production_active": "DISABLED_BY_DESIGN", "authorized": false, "blocking_reasons": ["explicit owner approval missing", "P3.5 Shadow evidence not accepted"], "gate": {"owner_approved": false}},
  "important_blockers": ["explicit owner approval missing"],
  "recent_events": [
    {"event_type": "TASK_SUBMITTED", "task_id": "t-1", "created_at": "2026-08-31T00:00:00Z", "summary": "task submitted"}
  ]
}
"""

let taskDetailBody = """
{"task":{"task_id":"t-1","request_id":"r-1","intent":"fix bug","project_id":"project-fixture","base_sha":"abc123","working_subpath":null,"state":"RUNNING","state_version":2,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"},"runs":[{"run_id":"run-1","task_id":"t-1","worker_id":"m3-sub","pid":123,"status":"RUNNING","started_at":"2026-08-31T00:00:30Z","finished_at":null}],"routing":{"task_id":"t-1","decision_id":"route-1","request_id":"r-1","created_at":"2026-08-31T00:00:15Z","decision":{"mode":"SHADOW","selected_execution_target_id":null,"fallback_reason":"quota confidence remained UNKNOWN"}},"verification":{"task_id":"t-1","task_state":"RUNNING","status":"NOT_VERIFIED","evidence_id":null,"failure_reason":null},"approvals":{"approvals":[]},"workspace":{"task_id":"t-1","project_id":"project-fixture","repo_path":"/repo","worktree_path":"/repo-wt","branch":"codex/t-1","base_sha":"abc123","working_subpath":null,"writer_locked":false},"events":[{"event_type":"TASK_SUBMITTED","task_id":"t-1","created_at":"2026-08-31T00:00:00Z","summary":"task submitted"}]}
"""

/// Connection-based quota body: one connected provider that has no quota
/// evidence yet. This is the required regression shape — CONNECTED with zero
/// pools must still render a card.
let quotaUnknownBody = """
{
  "state": "CONNECTED_BUT_QUOTA_UNKNOWN",
  "summary": {"connected_provider_count": 1, "quota_observable_provider_count": 0, "quota_unknown_provider_count": 1, "quota_warning_count": 0, "quota_exhausted_count": 0},
  "providers": [
    {"provider_id": "zai-coding-plan", "display_name": "GLM / Z.AI", "connection_state": "CONNECTED", "auth_state": "AUTH_UNKNOWN", "plan_surface": "Coding Plan", "region": null, "quota_state": "UNKNOWN", "confidence": "UNKNOWN", "measurement_source": null, "observed_at": null, "readonly_source_available": true, "collector_available": false, "last_refresh_status": null, "last_refresh_at": null, "failure_reason": "CREDENTIAL_NOT_AVAILABLE", "quota_pools": []}
  ],
  "history": {"observations": [], "retention_limit": 500}
}
"""

let quotaObservedBody = """
{
  "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
  "summary": {"connected_provider_count": 1, "quota_observable_provider_count": 1, "quota_unknown_provider_count": 0, "quota_warning_count": 0, "quota_exhausted_count": 0},
  "providers": [
    {"provider_id": "minimax-cn-coding-plan", "display_name": "MiniMax CN Coding Plan", "connection_state": "CONNECTED", "auth_state": "AUTHENTICATED", "plan_surface": "Coding Plan", "region": "cn", "quota_state": "OBSERVED", "confidence": "EXACT", "measurement_source": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z", "readonly_source_available": true, "collector_available": true, "last_refresh_status": "SUCCESS", "last_refresh_at": "2026-08-31T00:00:00Z", "failure_reason": null, "quota_pools": [{"quota_pool_id": "minimax-coding-plan-cn", "name": "minimax-coding-plan-cn", "plan_id": "coding-plan", "state": "AVAILABLE", "confidence": "EXACT", "measurement_source_type": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z", "windows": [{"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE", "confidence": "EXACT", "remaining_fraction": 0.42, "reset_at": "2026-08-31T05:00:00Z"}]}]}
  ],
  "history": {"observations": [], "retention_limit": 500}
}
"""

let quotaRefreshBody = """
{"refreshed_provider_ids": ["zai-coding-plan"], "overview": \(quotaUnknownBody)}
"""

let quotaUnmeteredBody = """
{
  "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
  "summary": {"connected_provider_count": 2, "quota_observable_provider_count": 2, "quota_unknown_provider_count": 0, "quota_warning_count": 0, "quota_exhausted_count": 0},
  "providers": [
    {"provider_id": "minimax-cn-coding-plan", "display_name": "MiniMax CN Coding Plan", "connection_state": "CONNECTED", "auth_state": "AUTHENTICATED", "plan_surface": "Coding Plan", "region": "cn", "quota_state": "OBSERVED", "confidence": "EXACT", "measurement_source": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z", "readonly_source_available": true, "collector_available": true, "last_refresh_status": "SUCCESS", "last_refresh_at": "2026-08-31T00:00:00Z", "failure_reason": null, "quota_pools": [{"quota_pool_id": "minimax-coding-plan-cn", "name": "minimax-coding-plan-cn", "plan_id": "coding-plan", "state": "AVAILABLE", "confidence": "EXACT", "measurement_source_type": "PROVIDER_API", "observed_at": "2026-08-31T00:00:00Z", "windows": [{"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE", "confidence": "EXACT", "remaining_fraction": 0.42, "reset_at": "2026-08-31T05:00:00Z"}]}]},
    {"provider_id": "opencode", "display_name": "OpenCode Free", "connection_state": "CONNECTED", "auth_state": "AUTH_FROM_ENV_PRESENCE", "plan_surface": "Free tier", "region": null, "quota_state": "OBSERVED", "confidence": "ESTIMATED", "measurement_source": "PROVIDER_API", "observed_at": "2026-09-07T00:00:00Z", "readonly_source_available": true, "collector_available": true, "last_refresh_status": "SUCCESS", "last_refresh_at": "2026-09-07T00:00:00Z", "failure_reason": null, "quota_pools": [], "pool_kind": "unmetered", "unmetered": {"rpm_observed": 12, "error_rate_1h": 0.05, "cooldown_until": null}}
  ],
  "history": {"observations": [], "retention_limit": 500}
}
"""

let schedulingSettingsBody = """
{
  "default_scheduling_policy": "BALANCED",
  "selectable_policies": ["BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "BURN_DOWN"],
  "mode": "MANUAL",
  "selectable_modes": ["MANUAL", "SUPERVISED_AUTO", "ACTIVE"]
}
"""

/// M1 WP1 — observed quota body that carries the burn sub-object on the
/// 5h window and a ``source_pressure`` on the provider card. Used by
/// the new decoder tests and any UI smoke that needs a populated chip.
let quotaObservedWithBurnBody = """
{
  "state": "CONNECTED_WITH_QUOTA_OBSERVATIONS",
  "summary": {"connected_provider_count": 1, "quota_observable_provider_count": 1, "quota_unknown_provider_count": 0, "quota_warning_count": 0, "quota_exhausted_count": 0},
  "providers": [
    {
      "provider_id": "minimax-cn-coding-plan",
      "display_name": "MiniMax CN Coding Plan",
      "connection_state": "CONNECTED",
      "auth_state": "AUTHENTICATED",
      "plan_surface": "Coding Plan",
      "region": "cn",
      "quota_state": "OBSERVED",
      "confidence": "EXACT",
      "measurement_source": "PROVIDER_API",
      "observed_at": "2026-08-31T00:00:00Z",
      "readonly_source_available": true,
      "collector_available": true,
      "last_refresh_status": "SUCCESS",
      "last_refresh_at": "2026-08-31T00:00:00Z",
      "failure_reason": null,
      "credential_source": "ENV_VAR",
      "quota_pools": [],
      "source_pressure": "ON_TRACK",
      "plan": {
        "provider_id": "minimax-cn-coding-plan",
        "plan_id": "coding-plan",
        "display_name": "MiniMax CN Coding Plan",
        "quota_semantics": "SHARED_POOL",
        "pool_id": "minimax-coding-plan-cn",
        "resource_kind": "TOKEN_PLAN_INCLUDED_QUOTA",
        "shared_across_models": true,
        "unit_kind": "TOKENS",
        "covered_model_ids": ["minimax-m2"],
        "state": "AVAILABLE",
        "confidence": "EXACT",
        "observed_at": "2026-08-31T00:00:00Z",
        "unknown_reason": null,
        "active_workload_scope": "CODING_TEXT",
        "workload_scope_notes": [],
        "binding_window": {"window_id": null, "window_kind": null, "remaining_fraction": null, "reset_at": null, "seconds_until_reset": null, "reason": "NO_KNOWN_REMAINING", "confidence": "UNKNOWN"},
        "model_consumption": [],
        "model_equivalents": [],
        "equivalent_capacity": [],
        "windows": [
          {"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE", "confidence": "EXACT", "remaining_fraction": 0.42, "remaining_units": null, "total_units": null, "unit": null, "reset_at": "2026-08-31T05:00:00Z", "burn": {"expected_used_fraction": 0.5, "actual_used_fraction": 0.58, "deviation": 0.08, "remaining_fraction": 0.42, "seconds_to_reset": 3600.0, "pressure": "ON_TRACK", "pressure_score": 0.13, "window_start_inferred": false}}
        ]
      }
    }
  ],
  "history": {"observations": [], "retention_limit": 500}
}
"""

func registerStandardRoutes(
    _ daemon: TestDaemon,
    quotaBody: String = quotaUnknownBody,
    schedulingBody: String = schedulingSettingsBody
) {
    daemon.route("GET", "/v1/health", body: healthBody)
    daemon.route("GET", "/v1/dashboard", body: dashboardBody)
    daemon.route("GET", "/v1/tasks?limit=20", body: tasksBody)
    // The dashboard fetches the authoritative collection at the daemon's list
    // limit; without this route the store would silently exercise its fallback.
    daemon.route(
        "GET", "/v1/tasks?limit=\(OrchestratorStore.taskListLimit)", body: tasksBody
    )
    daemon.route("GET", "/v1/tasks", body: tasksBody)
    daemon.route("GET", "/v1/projects", body: projectsBody)
    daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody)
    daemon.route("GET", "/v1/providers", body: providersBody)
    daemon.route("GET", "/v1/providers/status", body: providerDiscoveryStatusBody)
    daemon.route("POST", "/v1/providers/refresh", body: providerDiscoveryStatusBody)
    daemon.route("GET", "/v1/quota", body: quotaBody)
    daemon.route("POST", "/v1/quota/refresh", body: quotaRefreshBody)
    // M1 WP5a-1: scheduling settings wire body. Tests can pass an
    // alternative body via the ``schedulingBody`` parameter; the
    // default surfaces ``mode`` + ``selectable_modes`` for the
    // picker.
    daemon.route("GET", "/v1/settings/scheduling", body: schedulingBody)
    daemon.route("GET", "/v1/active-status", body: activeStatusBody)
}
