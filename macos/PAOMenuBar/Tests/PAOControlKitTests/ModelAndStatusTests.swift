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

    func testProviderAndQuotaDecoding() throws {
        let view = try JSONDecoder().decode(ProviderHealthListView.self, from: Data(providersBody.utf8))
        let provider = try XCTUnwrap(view.providers.first)
        XCTAssertEqual(provider.providerId, "minimax")
        XCTAssertEqual(provider.accountCount, 1)
        let pool = try XCTUnwrap(provider.quotaPools.first)
        XCTAssertEqual(pool.confidence, "ESTIMATED")
        XCTAssertEqual(pool.measurementSourceType, "PROVIDER_API")
        let target = try XCTUnwrap(provider.executionTargets.first)
        XCTAssertEqual(target.observedAvailability?.state, "EXHAUSTED_OBSERVED")
        XCTAssertEqual(target.observedAvailability?.sanitizedReasonCode, "USAGE_LIMIT")
        XCTAssertEqual(target.observedAvailability?.measurementSource, "LOCALLY_MEASURED")
    }

    func testProviderHealthViewDecodesDiscoveryEvidence() throws {
        let body = """
        {"providers":[{"provider_id":"zai-coding-plan","display_name":"GLM / Z.AI","account_count":0,"quota_pools":[],"execution_targets":[{"execution_target_id":"zai-coding-plan/glm-5.3","model_sku_id":"zai-coding-plan/glm-5.3","runtime_id":"opencode","enabled":true,"runtime_available":true}],"evidence_source":"DISCOVERED_FROM_CATALOG","auth_status":"AUTH_FROM_ENV_PRESENCE","execution_status":"AVAILABLE_FOR_CATALOG","last_checked":"2026-08-31T00:00:00Z"}]}
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

    func testEstimatedNeverRendersAsPrecisePercentage() {
        XCTAssertEqual(
            QuotaRendering.remainingText(fraction: 0.8, confidence: "ESTIMATED"),
            "unknown (confidence: ESTIMATED)"
        )
        XCTAssertEqual(QuotaRendering.remainingText(fraction: nil, confidence: "UNKNOWN"), "unknown")
        XCTAssertEqual(QuotaRendering.remainingText(fraction: 0.8123, confidence: "EXACT"), "81.2%")
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
}
