import Foundation
import XCTest

@testable import PAOControlKit

/// Regression coverage for the Daily Driver's top-level "Ready to work" claim.
///
/// MANUAL and SUPERVISED_AUTO do not have the same quota precondition: manual
/// dispatch can collect quota at the execution boundary, while the autonomous
/// supervisor deliberately fails closed before planning unless host-observed
/// availability already exists.
final class DailyDriverQuotaReadinessTests: XCTestCase {
    func testSupervisedAutoMissingQuotaFailsClosed() {
        let result = readiness(mode: "SUPERVISED_AUTO", observedState: nil)
        XCTAssertFalse(result.isReady)
        XCTAssertEqual(result.blocker, .noAvailableCapacity)
    }

    func testSupervisedAutoUnknownQuotaFailsClosed() {
        let result = readiness(mode: "SUPERVISED_AUTO", observedState: "UNKNOWN")
        XCTAssertFalse(result.isReady)
        XCTAssertEqual(result.blocker, .noAvailableCapacity)
    }

    func testSupervisedAutoObservedWindowedQuotaIsReady() {
        let result = readiness(
            mode: "SUPERVISED_AUTO",
            observedState: "AVAILABLE_OBSERVED"
        )
        XCTAssertTrue(result.isReady)
        XCTAssertNil(result.blocker)
    }

    func testSupervisedAutoObservedUnmeteredQuotaIsReady() {
        let result = readiness(
            mode: "SUPERVISED_AUTO",
            observedState: "AVAILABLE_UNMETERED"
        )
        XCTAssertTrue(result.isReady)
        XCTAssertNil(result.blocker)
    }

    func testManualUnknownQuotaCanStillBeReadyForDispatchTimeRefresh() {
        for state in [String?.none, String?.some("UNKNOWN"), String?.some("RECOVERY_PROBE_DUE")] {
            let result = readiness(mode: "MANUAL", observedState: state)
            XCTAssertTrue(result.isReady, "manual state \(state ?? "nil")")
            XCTAssertNil(result.blocker)
        }
    }

    func testManualExplicitBlockedQuotaStatesAreNotReady() {
        for state in ["EXHAUSTED_OBSERVED", "COOLDOWN", "UNCERTAIN_LOCKED"] {
            let result = readiness(mode: "MANUAL", observedState: state)
            XCTAssertFalse(result.isReady, state)
            XCTAssertEqual(result.blocker, .noAvailableCapacity, state)
        }
    }

    private func readiness(
        mode: String,
        observedState: String?
    ) -> DailyDriverReadinessSnapshot {
        DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: mode,
            ownerExecutionEnabled: true,
            projects: [project()],
            providers: providers(observedState: observedState)
        )
    }

    private func project() -> ProjectView {
        decode(
            """
            {"project_id":"project-quota","display_name":"Quota Project","canonical_repo_root":"/tmp/project","git_root":"/tmp/project","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-17T00:00:00Z","updated_at":"2026-09-17T00:01:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":true,"unattended_allowed":false,"grace_seconds":180}
            """
        )
    }

    private func providers(observedState: String?) -> ProviderHealthListView {
        let observed: String
        if let observedState {
            observed = """
            {"state":"\(observedState)","measurement_source":"LOCALLY_MEASURED","confidence":"ESTIMATED","observed_at":"2026-09-17T00:00:00Z","sanitized_reason_code":null}
            """
        } else {
            observed = "null"
        }

        return decode(
            """
            {"providers":[{"provider_id":"zai-coding-plan","display_name":"GLM / Z.AI","account_count":0,"quota_pools":[],"execution_targets":[{"execution_target_id":"pi-zai-coding-plan-glm-5.3","model_sku_id":"glm-5.3","runtime_id":"pi","enabled":true,"execution_verified":true,"execution_verified_stale":false,"runtime_available":true,"observed_availability":\(observed)}],"evidence_source":"PI_RUNTIME_AUTH_READY","auth_status":"AUTHENTICATED","execution_status":null,"connection_state":"CONNECTED","auth_state":"AUTHENTICATED","runtime_state":"AVAILABLE","plan_surface":"Coding Plan","region":null,"last_checked":null}]}
            """
        )
    }

    private func decode<T: Decodable>(_ json: String) -> T {
        // swiftlint:disable:next force_try
        try! JSONDecoder().decode(T.self, from: Data(json.utf8))
    }
}
