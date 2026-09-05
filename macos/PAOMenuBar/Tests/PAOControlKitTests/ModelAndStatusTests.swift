import Foundation

import XCTest

@testable import PAOControlKit

final class ModelAndStatusTests: XCTestCase {
    func testTaskListDecoding() throws {
        let view = try JSONDecoder().decode(TaskListView.self, from: Data(tasksBody.utf8))
        XCTAssertEqual(view.total, 2)
        XCTAssertEqual(view.tasks.map(\.taskId), ["t-1", "t-2"])
        XCTAssertEqual(view.tasks[0].state, "RUNNING")
        XCTAssertEqual(view.tasks[1].stateVersion, 3)
    }

    func testDispatchAndOwnerExecutionDecoding() throws {
        let dispatchBody = """
        {"dispatch_id":"owner-dispatch-d1","task":{"task_id":"task-1","request_id":"r1","intent":"i","state":"READY","state_version":1,"created_at":"2026-09-01T00:00:00Z","updated_at":"2026-09-01T00:00:00Z"},"request_id":"d1","authority":"OWNER_INITIATED_EXECUTION","execution_target_id":"zai-coding-plan-glm-5.3","status":"RESERVED","accepted":true,"reason":"owner initiated execution dispatch reserved","failure_code":null}
        """
        let dispatch = try JSONDecoder().decode(DispatchTaskView.self, from: Data(dispatchBody.utf8))
        XCTAssertEqual(dispatch.dispatchId, "owner-dispatch-d1")
        XCTAssertEqual(dispatch.authority, "OWNER_INITIATED_EXECUTION")
        XCTAssertEqual(dispatch.status, "RESERVED")
        XCTAssertTrue(dispatch.accepted)
        XCTAssertNil(dispatch.failureCode)
        XCTAssertEqual(dispatch.task.taskId, "task-1")

        let settingsBody = """
        {"owner_initiated_execution_enabled":true,"production_active":"DISABLED_BY_DESIGN"}
        """
        let settings = try JSONDecoder().decode(
            OwnerExecutionSettingsView.self,
            from: Data(settingsBody.utf8)
        )
        XCTAssertTrue(settings.ownerInitiatedExecutionEnabled)
        XCTAssertEqual(settings.productionActive, "DISABLED_BY_DESIGN")

        let request = DispatchRequest(
            requestId: "d1",
            taskStateVersion: 1,
            executionTargetId: "zai-coding-plan-glm-5.3"
        )
        let encoded = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )
        XCTAssertEqual(json["request_id"] as? String, "d1")
        XCTAssertEqual(json["task_state_version"] as? Int, 1)
        XCTAssertEqual(json["execution_target_id"] as? String, "zai-coding-plan-glm-5.3")
        // The client must never send an authority field; the server derives it.
        XCTAssertNil(json["authority"])
    }

    func testTaskDetailDecodesReadOnlyWorkerResultMetadata() throws {
        let body = """
        {"task":{"task_id":"t-1","request_id":"r-1","intent":"fix bug","state":"VERIFIED","state_version":5,"created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:04:00Z"},"runs":[{"run_id":"run-1","task_id":"t-1","worker_id":"m3-sub","pid":123,"status":"FINISHED","started_at":"2026-08-31T00:00:30Z","finished_at":"2026-08-31T00:03:00Z","result":{"exit_code":0,"stdout_bytes":120,"stderr_bytes":0,"stdout_sha256":"abc","stderr_sha256":"def","output_truncated":false,"timed_out":false}}],"routing":null,"verification":{"task_id":"t-1","task_state":"VERIFIED","status":"VERIFIED","evidence_id":"verify-1","failure_reason":null,"result":{"profile":"hello","passed":true,"changed_paths":["hello.txt"],"unexpected_paths":[],"stages":[{"name":"git diff --check","argv":["git","diff"],"returncode":0,"stdout":"","stderr":""}],"evidence_id":"verify-1","failure_reason":null}},"approvals":{"approvals":[]},"workspace":null,"events":[]}
        """
        let detail = try JSONDecoder().decode(TaskDetailView.self, from: Data(body.utf8))
        let run = try XCTUnwrap(detail.runs.first)
        XCTAssertEqual(run.exitCode, 0)
        XCTAssertEqual(run.stdoutBytes, 120)
        XCTAssertEqual(run.stdoutSHA256, "abc")
        XCTAssertEqual(run.outputTruncated, false)
        XCTAssertEqual(detail.verification.result?.changedPaths, ["hello.txt"])
        XCTAssertEqual(detail.verification.result?.stages.first?.exitCode, 0)
    }

    func testProviderAndQuotaDecoding() throws {
        let view = try JSONDecoder().decode(ProviderHealthListView.self, from: Data(providersBody.utf8))
        let provider = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(provider.providerId, "minimax")
        XCTAssertEqual(provider.accountCount, 1)
        let pool = try XCTUnwrap(provider.quotaPools.first)
        XCTAssertEqual(pool.confidence, "ESTIMATED")
        XCTAssertEqual(pool.measurementSourceType, "PROVIDER_API")
        let target = try XCTUnwrap(provider.executionTargets.first)
        XCTAssertEqual(target.executionVerified, false)
        XCTAssertFalse(target.isExecutionVerified)
        XCTAssertEqual(target.observedAvailability?.state, "EXHAUSTED_OBSERVED")
        XCTAssertEqual(target.observedAvailability?.sanitizedReasonCode, "USAGE_LIMIT")
        XCTAssertEqual(target.observedAvailability?.measurementSource, "LOCALLY_MEASURED")
    }

    func testProviderHealthViewDecodesDiscoveryEvidence() throws {
        let body = """
        {"providers":[{"provider_id":"zai-coding-plan","display_name":"GLM / Z.AI","account_count":0,"quota_pools":[],"execution_targets":[{"execution_target_id":"zai-coding-plan/glm-5.3","model_sku_id":"zai-coding-plan/glm-5.3","runtime_id":"opencode","enabled":true,"execution_verified":false,"runtime_available":true}],"evidence_source":"DISCOVERED_FROM_CATALOG","auth_status":"AUTH_FROM_ENV_PRESENCE","execution_status":"AVAILABLE_FOR_CATALOG","last_checked":"2026-08-31T00:00:00Z"}]}
        """
        let view = try JSONDecoder().decode(ProviderHealthListView.self, from: Data(body.utf8))
        let provider = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(provider.evidenceSource, "DISCOVERED_FROM_CATALOG")
        XCTAssertEqual(provider.authStatus, "AUTH_FROM_ENV_PRESENCE")
        XCTAssertEqual(provider.executionStatus, "AVAILABLE_FOR_CATALOG")
        XCTAssertEqual(provider.lastChecked, "2026-08-31T00:00:00Z")
    }

    func testProviderDiscoveryStatusDecoding() throws {
        let body = """
        {"discovery_state":"DISCOVERED","last_discovered_at":"2026-08-31T00:00:00Z","provider_count":5,"execution_target_count":35,"last_error_code":null,"catalog_snapshot_id":"1.18.25","source_method":"opencode_cli_inspection"}
        """
        let view = try JSONDecoder().decode(ProviderDiscoveryStatusView.self, from: Data(body.utf8))
        XCTAssertEqual(view.discoveryState, "DISCOVERED")
        XCTAssertEqual(view.providerCount, 5)
        XCTAssertEqual(view.executionTargetCount, 35)
        XCTAssertEqual(view.catalogSnapshotId, "1.18.25")
        XCTAssertEqual(view.sourceMethod, "opencode_cli_inspection")
        XCTAssertNil(view.lastErrorCode)
    }

    func testCredentialLikeFieldsAreIgnoredNotSurfaced() throws {
        let tainted = """
        {"providers":[{"provider_id":"minimax","display_name":"MiniMax CN","account_count":1,
        "credential_ref":"SECRET-REF","api_key":"SECRET-KEY","quota_pools":[],"execution_targets":[]}]}
        """
        let view = try JSONDecoder().decode(ProviderHealthListView.self, from: Data(tainted.utf8))
        let provider = try XCTUnwrap(view.providers.first)
        // Unknown fields are ignored; the model exposes no credential-shaped surface at all.
        let mirrored = String(data: try JSONEncoder().encode(view), encoding: .utf8) ?? ""
        XCTAssertFalse(mirrored.contains("SECRET-REF"))
        XCTAssertFalse(mirrored.contains("SECRET-KEY"))
        XCTAssertEqual(provider.providerId, "minimax")
    }

    func testActiveStatusDecodingAndRendering() throws {
        let view = try JSONDecoder().decode(ActiveStatusView.self, from: Data(activeStatusBody.utf8))
        XCTAssertEqual(view.productionActive, "DISABLED_BY_DESIGN")
        XCTAssertFalse(view.authorized)
        XCTAssertTrue(view.blockingReasons.contains("explicit owner approval missing"))
        XCTAssertEqual(view.gate["owner_approved"], false)
    }

    func testQuotaRenderingUsesObservedValuesExceptUnknown() {
        XCTAssertEqual(
            QuotaRendering.remainingText(fraction: 0.8, confidence: "ESTIMATED"),
            "80.0%"
        )
        XCTAssertEqual(QuotaRendering.remainingText(fraction: nil, confidence: "UNKNOWN"), "unknown")
        XCTAssertEqual(QuotaRendering.remainingText(fraction: 0.8123, confidence: "EXACT"), "81.2%")
        XCTAssertEqual(
            QuotaRendering.remainingText(fraction: 0.8, confidence: "UNKNOWN"),
            "unknown (confidence: UNKNOWN)"
        )
        XCTAssertEqual(QuotaRendering.confidenceBadge("WEIRD"), "UNKNOWN")
        XCTAssertEqual(QuotaRendering.confidenceBadge("ESTIMATED"), "ESTIMATED")
    }

    func testStatusSummaryPrecedence() throws {
        let tasks = try JSONDecoder().decode(TaskListView.self, from: Data(tasksBody.utf8)).tasks
        let providers = try JSONDecoder().decode(ProviderHealthListView.self, from: Data(providersBody.utf8))

        XCTAssertEqual(
            StatusSummary.derive(connection: .disconnected(reason: .daemonNotRunning),
                                 tasks: tasks, providers: providers),
            .disconnected(.daemonNotRunning)
        )
        // BLOCKED task outranks quota-limited and working.
        XCTAssertEqual(StatusSummary.derive(connection: .connected, tasks: tasks, providers: providers), .blocked)
        XCTAssertEqual(StatusSummary.derive(connection: .connected, tasks: tasks, providers: nil), .blocked)
        XCTAssertEqual(StatusSummary.derive(connection: .connected, tasks: [], providers: providers), .quotaLimited)
        XCTAssertEqual(StatusSummary.derive(connection: .connected, tasks: [], providers: nil), .healthy)
    }

    func testStatusSummaryWorkingOnlyForRunningWithoutBlockedOrQuota() throws {
        let runningOnly = """
        {"tasks":[{"task_id":"t","request_id":"r","intent":"i","state":"RUNNING","state_version":1,"created_at":"x","updated_at":"x"}],"total":1}
        """
        let tasks = try JSONDecoder().decode(TaskListView.self, from: Data(runningOnly.utf8)).tasks
        XCTAssertEqual(StatusSummary.derive(connection: .connected, tasks: tasks, providers: nil), .working)
    }

    func testSocketDiscoveryValidationAndPrecedence() {
        XCTAssertEqual(SocketDiscovery.validate(path: "/tmp/ok.sock"), .valid)
        let long = "/" + String(repeating: "a", count: 200)
        guard case .tooLong(let length) = SocketDiscovery.validate(path: long) else {
            return XCTFail("expected tooLong")
        }
        XCTAssertEqual(length, long.utf8.count)
        XCTAssertEqual(SocketDiscovery.validate(path: "relative/sock"), .notAbsolute)

        let defaults = UserDefaults(suiteName: "pao-tests-\(UUID().uuidString.prefix(6))")!
        defaults.removeObject(forKey: SocketDiscovery.userDefaultsKey)
        let fromEnv = SocketDiscovery.resolve(userDefaults: defaults,
                                             environment: [SocketDiscovery.environmentKey: "/tmp/env.sock"])
        XCTAssertEqual(fromEnv, "/tmp/env.sock")
        let fallback = SocketDiscovery.resolve(userDefaults: defaults, environment: [:])
        XCTAssertTrue(fallback.hasSuffix("Library/Caches/Personal AI Orchestrator/control.sock"))
        defaults.set("/tmp/configured.sock", forKey: SocketDiscovery.userDefaultsKey)
        XCTAssertEqual(SocketDiscovery.resolve(userDefaults: defaults, environment: [:]), "/tmp/configured.sock")
        defaults.removeObject(forKey: SocketDiscovery.userDefaultsKey)
    }

    func testApplicationSupportLayoutMatchesProductRuntimeContract() {
        let layout = AppSupportLayout.resolve(homeDirectory: "/Users/example")
        XCTAssertEqual(
            layout.runtimeConfigPath,
            "/Users/example/Library/Application Support/Personal AI Orchestrator/runtime.json"
        )
        XCTAssertEqual(
            layout.stateDatabasePath,
            "/Users/example/Library/Application Support/Personal AI Orchestrator/state.sqlite3"
        )
        XCTAssertEqual(
            layout.socketPath,
            "/Users/example/Library/Caches/Personal AI Orchestrator/control.sock"
        )
        XCTAssertEqual(SocketDiscovery.validate(path: layout.socketPath), .valid)
    }

    func testDaemonLaunchConfigurationUsesFixedDaemonArguments() {
        let layout = AppSupportLayout.resolve(homeDirectory: "/Users/example")
        let helper = URL(fileURLWithPath: "/App/Contents/Helpers/pao-daemon")
        let config = DaemonLaunchConfiguration(layout: layout, helperURL: helper)
        XCTAssertEqual(config.helperURL.path, "/App/Contents/Helpers/pao-daemon")
        XCTAssertEqual(config.socketValidation, .valid)
        XCTAssertEqual(config.arguments, [])
    }

    func testDaemonLifecycleStatusDisplayValuesAreStable() {
        XCTAssertEqual(DaemonLifecycleStatus.alreadyRunning.displayValue, "DAEMON_ALREADY_RUNNING")
        XCTAssertEqual(DaemonLifecycleStatus.starting.displayValue, "DAEMON_STARTING")
        XCTAssertEqual(DaemonLifecycleStatus.healthyPreexisting.displayValue, "DAEMON_HEALTHY_PREEXISTING")
        XCTAssertEqual(
            DaemonLifecycleStatus.healthyStartedByApp(pid: 123).displayValue,
            "DAEMON_HEALTHY_STARTED_BY_APP"
        )
        XCTAssertEqual(DaemonLifecycleStatus.failed(reason: "helper_missing").displayValue, "DAEMON_FAILED")
    }

    func testWidgetSnapshotBridgeWritesSanitizedReadOnlyPayload() throws {
        let dashboard = try JSONDecoder().decode(DashboardSummaryView.self, from: Data(dashboardBody.utf8))
        let snapshot = WidgetSnapshot(
            generatedAt: Date(timeIntervalSince1970: 1_778_390_400),
            connection: .connected,
            daemonLifecycle: .healthyStartedByApp(pid: 123),
            dashboard: dashboard
        )
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pao-widget-\(UUID().uuidString)", isDirectory: true)
        let bridge = WidgetSnapshotBridge(snapshotURL: directory.appendingPathComponent("snapshot.json"))

        try bridge.write(snapshot)

        let loaded = try bridge.load()
        XCTAssertEqual(loaded.connectionState, "CONNECTED")
        XCTAssertEqual(loaded.daemonLifecycle, "DAEMON_HEALTHY_STARTED_BY_APP")
        XCTAssertEqual(loaded.productionActive, "DISABLED_BY_DESIGN")
        XCTAssertEqual(loaded.counts.total, 2)

        let rendered = String(data: try JSONEncoder().encode(loaded), encoding: .utf8) ?? ""
        XCTAssertFalse(rendered.contains("credential_ref"))
        XCTAssertFalse(rendered.contains("api_key"))
        XCTAssertFalse(rendered.contains("socket"))
        XCTAssertFalse(rendered.contains("intent"))
        XCTAssertFalse(rendered.contains("cancel"))
        XCTAssertFalse(rendered.contains("submit"))
    }

    func testWidgetSnapshotStalenessFailsClosed() throws {
        let dashboard = try JSONDecoder().decode(DashboardSummaryView.self, from: Data(dashboardBody.utf8))
        let generatedAt = Date(timeIntervalSince1970: 1_778_390_400)
        let snapshot = WidgetSnapshot(
            generatedAt: generatedAt,
            connection: .connected,
            daemonLifecycle: .healthyPreexisting,
            dashboard: dashboard
        )

        XCTAssertFalse(snapshot.isStale(referenceDate: generatedAt.addingTimeInterval(299)))
        XCTAssertTrue(snapshot.isStale(referenceDate: generatedAt.addingTimeInterval(301)))

        let malformed = snapshot.generatedAt.replacingOccurrences(of: "2026", with: "not-a-date")
        let payload = """
        {"schema_version":1,"generated_at":"\(malformed)","connection_state":"CONNECTED",
        "daemon_lifecycle":"DAEMON_HEALTHY_PREEXISTING","production_active":"DISABLED_BY_DESIGN",
        "counts":{"running":0,"ready":0,"blocked":0,"verified":0,"completed":0,"total":0},
        "providers":[]}
        """
        let decoded = try JSONDecoder().decode(WidgetSnapshot.self, from: Data(payload.utf8))
        XCTAssertTrue(decoded.isStale(referenceDate: generatedAt))
    }

    func testWidgetSnapshotWriterFallsBackWhenPrimaryTimesOut() throws {
        let dashboard = try JSONDecoder().decode(DashboardSummaryView.self, from: Data(dashboardBody.utf8))
        let snapshot = WidgetSnapshot(
            generatedAt: Date(timeIntervalSince1970: 1_778_390_400),
            connection: .connected,
            daemonLifecycle: .healthyStartedByApp(pid: 123),
            dashboard: dashboard
        )
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pao-writer-\(UUID().uuidString)", isDirectory: true)
        let fallbackBridge = WidgetSnapshotBridge(
            snapshotURL: directory.appendingPathComponent("snapshot.json")
        )
        let writer = WidgetSnapshotWriter(
            primaryWrite: { _ in Thread.sleep(forTimeInterval: 1.0) },
            fallback: fallbackBridge,
            timeoutSeconds: 0.15,
            retryInterval: 60.0
        )

        writer.write(snapshot)

        let deadline = Date().addingTimeInterval(3.0)
        while !FileManager.default.fileExists(atPath: fallbackBridge.snapshotURL.path) {
            if Date() > deadline {
                XCTFail("fallback snapshot was not written before timeout")
                return
            }
            Thread.sleep(forTimeInterval: 0.05)
        }
        let loaded = try fallbackBridge.load()
        XCTAssertEqual(loaded.daemonLifecycle, "DAEMON_HEALTHY_STARTED_BY_APP")
        XCTAssertEqual(loaded.productionActive, "DISABLED_BY_DESIGN")
    }

    func testWidgetSnapshotWriterBlocksPrimaryUntilRetryWindowElapses() throws {
        let recorder = PrimaryCallRecorder()
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pao-writer-\(UUID().uuidString)", isDirectory: true)
        let fallbackBridge = WidgetSnapshotBridge(
            snapshotURL: directory.appendingPathComponent("snapshot.json")
        )
        let snapshot = WidgetSnapshot(
            generatedAt: Date(),
            connection: .connected,
            daemonLifecycle: .healthyPreexisting,
            dashboard: nil
        )
        let writer = WidgetSnapshotWriter(
            primaryWrite: { _ in
                recorder.record()
                Thread.sleep(forTimeInterval: 1.0)
            },
            fallback: fallbackBridge,
            timeoutSeconds: 0.15,
            retryInterval: 0.4
        )

        writer.write(snapshot)
        XCTAssertTrue(waitForFile(at: fallbackBridge.snapshotURL, timeout: 3.0))
        writer.write(snapshot)
        Thread.sleep(forTimeInterval: 0.6)

        XCTAssertEqual(recorder.count, 1, "primary must not be retried inside the blocked window")

        writer.write(snapshot)
        let deadline = Date().addingTimeInterval(3.0)
        while recorder.count < 2 && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTAssertEqual(recorder.count, 2, "primary must be retried after the retry window elapses")
    }

    private final class PrimaryCallRecorder: @unchecked Sendable {
        private let lock = NSLock()
        private var calls = 0

        var count: Int {
            lock.lock()
            defer { lock.unlock() }
            return calls
        }

        func record() {
            lock.lock()
            calls += 1
            lock.unlock()
        }
    }

    private func waitForFile(at url: URL, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: url.path) { return true }
            Thread.sleep(forTimeInterval: 0.05)
        }
        return false
    }

    func testRunningCancelConflictIdentification() {
        let conflict = PAOClientError.httpError(
            status: 409,
            code: "running_task_cancellation_requires_execution_supervisor"
        )
        XCTAssertTrue(conflict.isRunningCancelConflict)
        XCTAssertFalse(PAOClientError.httpError(status: 409, code: "task_state_is_terminal").isRunningCancelConflict)
        XCTAssertFalse(PAOClientError.daemonNotRunning.isRunningCancelConflict)
    }

    func testTaskViewDecodesSchedulingPolicyFieldsWhenPresent() throws {
        let json = """
        {"task_id":"t-9","request_id":"r-9","intent":"manual target","project_id":"p-1","base_sha":"abc","working_subpath":null,"state":"SUBMITTED","state_version":0,"created_at":"2026-09-04T00:00:00Z","updated_at":"2026-09-04T00:00:00Z","scheduling_policy":"MANUAL","manual_execution_target_id":"zai-coding-plan-glm-5.3"}
        """
        let task = try JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
        XCTAssertEqual(task.schedulingPolicy, "MANUAL")
        XCTAssertEqual(task.manualExecutionTargetId, "zai-coding-plan-glm-5.3")
    }

    func testTaskViewDecodesWithoutSchedulingPolicyFields() throws {
        // Older daemons (and many test fixtures) omit the new fields.
        let json = """
        {"task_id":"t-9","request_id":"r-9","intent":"legacy","state":"SUBMITTED","state_version":0,"created_at":"2026-09-04T00:00:00Z","updated_at":"2026-09-04T00:00:00Z"}
        """
        let task = try JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
        XCTAssertNil(task.schedulingPolicy)
        XCTAssertNil(task.manualExecutionTargetId)
    }

    func testExecutionTargetHealthViewDecodesExecutionVerifiedStaleWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "runtime_id":"opencode",
          "enabled":true,
          "execution_verified":true,
          "execution_verified_stale":true
        }
        """
        let view = try JSONDecoder().decode(
            ExecutionTargetHealthView.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.executionVerified, true)
        XCTAssertEqual(view.executionVerifiedStale, true)
        XCTAssertTrue(view.isExecutionVerified)
        XCTAssertTrue(view.isExecutionVerifiedStale)
    }

    func testExecutionTargetHealthViewDecodesWithoutExecutionVerifiedStale() throws {
        // Older daemons never emit the new flag — the lenient decoder must
        // leave it nil rather than failing the whole provider view.
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "runtime_id":"opencode",
          "enabled":true,
          "execution_verified":true
        }
        """
        let view = try JSONDecoder().decode(
            ExecutionTargetHealthView.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.executionVerifiedStale)
        XCTAssertFalse(view.isExecutionVerifiedStale)
    }

    func testDispatchRecommendationCandidateDecodesExecutionVerifiedStaleWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "execution_verified_stale":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.verified, true)
        XCTAssertEqual(view.executionVerifiedStale, true)
        XCTAssertTrue(view.isExecutionVerifiedStale)
    }

    func testDispatchRecommendationCandidateDecodesWithoutExecutionVerifiedStale() throws {
        // Older daemons do not emit the new flag — the lenient decoder must
        // leave it nil rather than failing the recommendation view.
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.executionVerifiedStale)
        XCTAssertFalse(view.isExecutionVerifiedStale)
    }

    // MARK: - §M1 WP1 sourcePressure on the dispatch candidate

    func testDispatchRecommendationCandidateDecodesSourcePressureWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "source_pressure":"STARVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.sourcePressure, "STARVED")
    }

    func testDispatchRecommendationCandidateDecodesWithoutSourcePressure() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.sourcePressure)
    }

    // MARK: - §M1 WP2 — min_tier / tier / tier_match_reason / modelTiersSource

    func testTaskViewDecodesMinTierWhenPresent() throws {
        let json = """
        {"task_id":"t-9","request_id":"r-9","intent":"flagship only","state":"SUBMITTED","state_version":0,"created_at":"2026-09-04T00:00:00Z","updated_at":"2026-09-04T00:00:00Z","min_tier":"T0"}
        """
        let task = try JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
        XCTAssertEqual(task.minTier, "T0")
    }

    func testTaskViewDecodesWithoutMinTier() throws {
        // Pre-WP2 daemons / older fixtures omit the field; the decoder
        // must leave ``minTier`` nil rather than failing the request.
        let json = """
        {"task_id":"t-9","request_id":"r-9","intent":"legacy","state":"SUBMITTED","state_version":0,"created_at":"2026-09-04T00:00:00Z","updated_at":"2026-09-04T00:00:00Z"}
        """
        let task = try JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
        XCTAssertNil(task.minTier)
    }

    func testSubmitRequestEncodesMinTierWhenProvided() throws {
        // ``SubmitRequest`` is Encodable — the SwiftUI picker in
        // commit 5 passes the chosen tier here. JSON keys must match
        // the daemon's snake_case schema.
        let request = SubmitRequest(
            taskId: "menubar-abc",
            requestId: "menubar-req-abc",
            projectId: "project-fixture",
            intent: "flagship only",
            schedulingPolicy: "BALANCED",
            manualExecutionTargetId: nil,
            minTier: "T0"
        )
        let data = try JSONEncoder().encode(request)
        let payload = String(data: data, encoding: .utf8) ?? ""
        XCTAssertTrue(payload.contains("\"min_tier\":\"T0\""))
    }

    func testSubmitRequestEncodesWithoutMinTier() throws {
        // The picker default leaves ``minTier`` nil; the encoder must
        // emit no ``min_tier`` key so the daemon applies its own
        // T1 default rather than seeing ``null``.
        let request = SubmitRequest(
            taskId: "menubar-abc",
            requestId: "menubar-req-abc",
            projectId: "project-fixture",
            intent: "default"
        )
        let data = try JSONEncoder().encode(request)
        let payload = String(data: data, encoding: .utf8) ?? ""
        XCTAssertFalse(payload.contains("min_tier"))
    }

    func testExecutionTargetHealthViewDecodesTierFieldsWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "runtime_id":"opencode",
          "enabled":true,
          "execution_verified":true,
          "runtime_available":true,
          "observed_availability":null,
          "tier":"T1",
          "tier_match_reason":"glob"
        }
        """
        let view = try JSONDecoder().decode(
            ExecutionTargetHealthView.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.tier, "T1")
        XCTAssertEqual(view.tierMatchReason, "glob")
    }

    func testExecutionTargetHealthViewDecodesWithoutTierFields() throws {
        // Pre-WP2 daemons (and fixtures that predate commit 4) omit
        // ``tier`` and ``tier_match_reason``. The decoder must leave
        // them nil rather than failing the request.
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "runtime_id":"opencode",
          "enabled":true,
          "execution_verified":true,
          "runtime_available":true,
          "observed_availability":null
        }
        """
        let view = try JSONDecoder().decode(
            ExecutionTargetHealthView.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.tier)
        XCTAssertNil(view.tierMatchReason)
    }

    func testDispatchRecommendationCandidateDecodesTierFieldsWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":22.5,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "source_pressure":"ON_TRACK",
          "tier":"T0",
          "tier_match_reason":"exact",
          "score_components":[],
          "reasons":["tier=T0 min_tier=T1 match=exact"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.tier, "T0")
        XCTAssertEqual(view.tierMatchReason, "exact")
    }

    func testDispatchRecommendationCandidateDecodesWithoutTierFields() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":22.5,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.tier)
        XCTAssertNil(view.tierMatchReason)
    }

    func testHealthViewDecodesModelTiersSourceWhenPresent() throws {
        let json = """
        {"status":"ok","api_version":"v1","last_tick_at":"2026-09-05T00:00:00Z","tick_interval_seconds":5.0,"supervisor_steps":[],"model_tiers_source":"owner_file"}
        """
        let view = try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))
        XCTAssertEqual(view.modelTiersSource, "owner_file")
    }

    func testHealthViewDecodesWithoutModelTiersSource() throws {
        // Pre-WP2 daemons omit ``model_tiers_source``; the Swift
        // dashboard renders the absence as "Tier table: not wired".
        let json = """
        {"status":"ok","api_version":"v1","last_tick_at":"2026-09-05T00:00:00Z","tick_interval_seconds":5.0,"supervisor_steps":[]}
        """
        let view = try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))
        XCTAssertNil(view.modelTiersSource)
    }

    func testRunViewDecodesPidAliveWhenPresent() throws {
        let json = """
        {
          "run_id":"r-1",
          "task_id":"t-1",
          "worker_id":"glm",
          "pid":40618,
          "pid_alive":false,
          "status":"RUNNING",
          "started_at":"2026-09-04T00:00:00Z"
        }
        """
        let view = try JSONDecoder().decode(RunView.self, from: Data(json.utf8))
        XCTAssertEqual(view.pid, 40618)
        XCTAssertEqual(view.pidAlive, false)
        XCTAssertFalse(view.isProcessAlive)
    }

    func testRunViewDecodesWithoutPidAlive() throws {
        // Older daemons do not emit pid_alive — the lenient decoder must
        // leave it nil so the UI can fall back to "running pid N" until
        // the new daemon reaches the dashboard.
        let json = """
        {
          "run_id":"r-1",
          "task_id":"t-1",
          "worker_id":"glm",
          "pid":40618,
          "status":"RUNNING",
          "started_at":"2026-09-04T00:00:00Z"
        }
        """
        let view = try JSONDecoder().decode(RunView.self, from: Data(json.utf8))
        XCTAssertNil(view.pidAlive)
        XCTAssertFalse(view.isProcessAlive)
    }

    func testHealthViewDecodesSupervisorFieldsWhenPresent() throws {
        let json = """
        {
          "status":"ok",
          "api_version":"v1",
          "last_tick_at":"2026-09-05T00:00:00Z",
          "tick_interval_seconds":5.0,
          "supervisor_steps":[
            {"name":"heartbeat","last_run_at":"2026-09-05T00:00:00Z","last_duration_ms":0.3,"consecutive_failures":0,"in_backoff":false}
          ]
        }
        """
        let view = try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))
        XCTAssertEqual(view.status, "ok")
        XCTAssertEqual(view.tickIntervalSeconds, 5.0)
        XCTAssertNotNil(view.lastTickAt)
        XCTAssertNotNil(view.lastTickDate)
        XCTAssertEqual(view.supervisorSteps.count, 1)
        XCTAssertEqual(view.supervisorSteps[0].name, "heartbeat")
        XCTAssertEqual(view.supervisorSteps[0].consecutiveFailures, 0)
        XCTAssertFalse(view.supervisorSteps[0].inBackoff)
        XCTAssertNotNil(view.step(named: "heartbeat"))
        XCTAssertNil(view.step(named: "unknown"))
    }

    func testHealthViewDecodesWithoutSupervisorFields() throws {
        // Older (pre-WP0) daemons never emit last_tick_at / supervisor_steps;
        // the lenient decoder must leave the new ones nil / empty rather
        // than rejecting the whole /v1/health response.
        let json = """
        {"status":"ok","api_version":"v1"}
        """
        let view = try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))
        XCTAssertEqual(view.status, "ok")
        XCTAssertTrue(view.isCompatible)
        XCTAssertNil(view.lastTickAt)
        XCTAssertNil(view.lastTickDate)
        XCTAssertNil(view.tickIntervalSeconds)
        XCTAssertTrue(view.supervisorSteps.isEmpty)
    }

    func testSupervisorStepViewDecodesWithoutOptionalFields() throws {
        // The minimum payload (name only) must still decode so the future
        // can register steps that just confirm a heartbeat. Other
        // optional fields default to zero/false when missing.
        let json = """
        {"name":"heartbeat"}
        """
        let view = try JSONDecoder().decode(SupervisorStepView.self, from: Data(json.utf8))
        XCTAssertEqual(view.name, "heartbeat")
        XCTAssertNil(view.lastRunAt)
        XCTAssertNil(view.lastRunDate)
        XCTAssertNil(view.lastDurationMs)
        XCTAssertEqual(view.consecutiveFailures, 0)
        XCTAssertFalse(view.inBackoff)
    }
    // MARK: - §M1 WP3 — headroomMin + score-component weight + BURN_DOWN

    func testDispatchRecommendationCandidateDecodesHeadroomMinWhenPresent() throws {
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "headroom_mean":0.7,
          "headroom_min":0.6,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertEqual(view.headroomMin, 0.6)
        XCTAssertEqual(view.headroomMean, 0.7)
    }

    func testDispatchRecommendationCandidateDecodesWithoutHeadroomMin() throws {
        // Pre-WP3 daemons do not emit ``headroom_min``; the decoder
        // must leave the field nil rather than failing the request.
        let json = """
        {
          "execution_target_id":"provider-x/model-a",
          "model_sku_id":"model-a",
          "eligible":true,
          "admitted":true,
          "score":1.0,
          "headroom_mean":0.7,
          "evidence_fresh":true,
          "runtime_available":true,
          "verified":true,
          "quota_state":"AVAILABLE_OBSERVED",
          "score_components":[],
          "reasons":["policy=BALANCED"]
        }
        """
        let view = try JSONDecoder().decode(
            DispatchRecommendationCandidate.self, from: Data(json.utf8)
        )
        XCTAssertNil(view.headroomMin)
        XCTAssertEqual(view.headroomMean, 0.7)
    }

    func testDispatchRecommendationScoreComponentDecodesWeightWhenPresent() throws {
        // M1 WP3 fix (F4): wire shape carries the **raw value**
        // and the per-row ``weight``; the UI multiplies them at
        // display time (``contribution`` is a computed
        // ``weight × value``).
        let json = """
        {"name":"quality_capability_fit","value":2.0,"weight":0.7}
        """
        let c = try JSONDecoder().decode(
            DispatchRecommendationScoreComponent.self, from: Data(json.utf8)
        )
        XCTAssertEqual(c.name, "quality_capability_fit")
        XCTAssertEqual(c.value, 2.0)
        XCTAssertEqual(c.weight, 0.7)
        XCTAssertEqual(c.contribution, 1.4, accuracy: 1e-9)
    }

    func testDispatchRecommendationScoreComponentDecodesWithoutWeight() throws {
        // Pre-WP2 daemons / fixtures omit ``weight``; the decoder
        // must leave the field nil and the contribution reads
        // as 0 (no multiplier to apply).
        let json = """
        {"name":"quality_capability_fit","value":2.0}
        """
        let c = try JSONDecoder().decode(
            DispatchRecommendationScoreComponent.self, from: Data(json.utf8)
        )
        XCTAssertEqual(c.name, "quality_capability_fit")
        XCTAssertEqual(c.value, 2.0)
        XCTAssertNil(c.weight)
        XCTAssertEqual(c.contribution, 0.0)
    }

    func testL10nBurnDownPolicyNameAndDetail() {
        // M1 WP3: BURN_DOWN is the new pressure-first preset. The
        // picker reads this label; the detail line explains the bias.
        XCTAssertEqual(L10n.schedulingPolicyName("BURN_DOWN"), L10n.policyBurnDown)
        XCTAssertEqual(
            L10n.schedulingPolicyDetail("BURN_DOWN"), L10n.policyBurnDownDetail
        )
        XCTAssertNotEqual(L10n.policyBurnDown, L10n.policyBurnDownDetail)
    }

    // M1 WP3 fix (F2): ``freshness`` is the sixth weight-named
    // score component. ``value`` carries the raw freshness
    // reading (``-5.0`` for missing / stale evidence,
    // ``_EVIDENCE_FRESH_DAYS - age_days`` otherwise) and ``weight``
    // is the constant ``FRESHNESS_WEIGHT`` the daemon applies.
    // The contribution is the computed ``weight × value``.
    func testDispatchRecommendationScoreComponentDecodesFreshness() throws {
        let json = """
        {"name":"freshness","value":-5.0,"weight":0.2}
        """
        let c = try JSONDecoder().decode(
            DispatchRecommendationScoreComponent.self, from: Data(json.utf8)
        )
        XCTAssertEqual(c.name, "freshness")
        XCTAssertEqual(c.value, -5.0)
        XCTAssertEqual(c.weight, 0.2)
        XCTAssertEqual(c.contribution, -1.0, accuracy: 1e-9)
    }
}
