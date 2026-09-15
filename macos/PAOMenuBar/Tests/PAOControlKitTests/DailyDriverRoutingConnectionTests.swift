import Foundation
import XCTest

@testable import PAOControlKit

final class DailyDriverRoutingConnectionTests: XCTestCase {
    func testDisconnectedOpenCodeDoesNotMakeHomeReady() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            ownerExecutionEnabled: true,
            projects: [project()],
            providers: providers(runtimeId: "opencode", providerConnectionState: "DISCONNECTED")
        )

        XCTAssertFalse(result.isReady)
        XCTAssertEqual(result.blocker, .noRunnableTarget)
    }

    func testConnectedOpenCodeCanMakeHomeReady() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            ownerExecutionEnabled: true,
            projects: [project()],
            providers: providers(runtimeId: "opencode", providerConnectionState: "CONNECTED")
        )

        XCTAssertTrue(result.isReady)
        XCTAssertNil(result.blocker)
    }

    func testPiReadyRemainsRoutingConnectedWithoutConnectionRegistryRow() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            ownerExecutionEnabled: true,
            projects: [project()],
            providers: providers(runtimeId: "pi", providerConnectionState: nil)
        )

        XCTAssertTrue(result.isReady)
        XCTAssertNil(result.blocker)
    }

    private func project() -> ProjectView {
        decode(
            """
            {"project_id":"project-1","display_name":"Project One","canonical_repo_root":"/tmp/project","git_root":"/tmp/project","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":false,"unattended_allowed":false,"grace_seconds":180}
            """
        )
    }

    private func providers(
        runtimeId: String,
        providerConnectionState: String?
    ) -> ProviderHealthListView {
        let connectionState = providerConnectionState.map { "\"\($0)\"" } ?? "null"
        return decode(
            """
            {"providers":[{"provider_id":"provider-1","display_name":"Provider One","account_count":1,"quota_pools":[],"execution_targets":[{"execution_target_id":"target-1","model_sku_id":"model-1","runtime_id":"\(runtimeId)","enabled":true,"execution_verified":true,"execution_verified_stale":false,"runtime_available":true,"observed_availability":null}],"evidence_source":null,"auth_status":null,"execution_status":null,"connection_state":\(connectionState),"auth_state":null,"runtime_state":null,"plan_surface":null,"region":null,"last_checked":null}]}
            """
        )
    }

    private func decode<T: Decodable>(_ json: String) -> T {
        // swiftlint:disable:next force_try
        try! JSONDecoder().decode(T.self, from: Data(json.utf8))
    }
}
