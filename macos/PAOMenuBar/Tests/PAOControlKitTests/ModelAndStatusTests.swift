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
        let config = DaemonLaunchConfiguration(layout: layout)
        XCTAssertEqual(config.pythonExecutable, "/usr/bin/env")
        XCTAssertEqual(config.socketValidation, .valid)
        XCTAssertEqual(
            config.arguments,
            [
                "python",
                "-m", "personal_ai_orchestrator.daemon",
                "--config", layout.runtimeConfigPath,
                "--state-db", layout.stateDatabasePath,
                "--runtime-state-root", layout.runtimeStateRoot,
                "--control-socket", layout.socketPath,
                "--host", "127.0.0.1",
                "--port", "8765",
            ]
        )
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
