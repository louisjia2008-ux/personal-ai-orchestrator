import Foundation
import XCTest

@testable import PAOControlKit

final class DailyDriverReadinessTests: XCTestCase {
    func testHistoricalFailedWorkDoesNotEnterReadinessContract() {
        // Readiness intentionally has no task-list input. A terminal failure stays
        // visible under Needs Attention but cannot make the app permanently
        // unable to report that it can accept new work.
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([target()])
        )
        XCTAssertTrue(result.isReady)
        XCTAssertNil(result.blocker)
    }

    func testUnknownSchedulingModeIsNotPresentedAsReady() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: nil,
            projects: [project(supervised: false)],
            providers: providers([target()])
        )
        XCTAssertEqual(result.blocker, .schedulingModeUnknown)
        XCTAssertFalse(result.isReady)
    }

    func testAnOnlineProjectIsRequiredForAnyNewWork() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false, storage: "OFFLINE")],
            providers: providers([target()])
        )
        XCTAssertEqual(result.blocker, .noOnlineProject)
    }

    func testSupervisedAutoRequiresAnOptedInOnlineProject() {
        let blocked = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "SUPERVISED_AUTO",
            projects: [project(supervised: false)],
            providers: providers([target()])
        )
        XCTAssertEqual(blocked.blocker, .supervisedAutoNeedsProject)

        let ready = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "SUPERVISED_AUTO",
            projects: [project(supervised: true)],
            providers: providers([target()])
        )
        XCTAssertTrue(ready.isReady)
    }

    func testVerifiedTargetAlsoRequiresRuntimeAvailability() {
        let unavailable = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([target(runtimeAvailable: false)])
        )
        XCTAssertEqual(unavailable.blocker, .noRunnableTarget)

        let unverified = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([target(verified: false)])
        )
        XCTAssertEqual(unverified.blocker, .noRunnableTarget)
    }

    func testExplicitExhaustionBlocksOnlyWhenEveryRunnableTargetIsUnavailable() {
        let exhaustedOnly = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([
                target(id: "a", observedState: "EXHAUSTED_OBSERVED"),
                target(id: "b", observedState: "COOLDOWN"),
            ])
        )
        XCTAssertEqual(exhaustedOnly.blocker, .noAvailableCapacity)

        let oneAvailable = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([
                target(id: "a", observedState: "EXHAUSTED_OBSERVED"),
                target(id: "b", observedState: "AVAILABLE"),
            ])
        )
        XCTAssertTrue(oneAvailable.isReady)
    }

    func testMissingAvailabilityIsUncertaintyNotFabricatedExhaustion() {
        let result = DailyDriverReadiness.derive(
            connection: .connected,
            schedulingMode: "MANUAL",
            projects: [project(supervised: false)],
            providers: providers([target(observedState: nil)])
        )
        XCTAssertTrue(result.isReady)
    }

    func testProductionActiveEvidenceRisksAreNotDailyDriverAttentionRisks() throws {
        let productionOnly = try risk(
            rawCode: "Owner approval required before ACTIVE mode",
            destination: "settings"
        )
        let shadowOnly = try risk(
            rawCode: "Shadow evidence incomplete for Production ACTIVE",
            destination: "settings"
        )
        let quota = try risk(rawCode: "QUOTA_EXHAUSTED", destination: "quota")
        let futureUnknown = try risk(rawCode: "FUTURE_OPERATIONAL_RISK", destination: "quota")

        XCTAssertTrue(DailyDriverRiskPresentation.isProductionActivationOnly(productionOnly))
        XCTAssertTrue(DailyDriverRiskPresentation.isProductionActivationOnly(shadowOnly))
        XCTAssertFalse(DailyDriverRiskPresentation.isProductionActivationOnly(quota))

        let actionable = DailyDriverRiskPresentation.actionable(
            [productionOnly, shadowOnly, quota, futureUnknown]
        )
        XCTAssertEqual(
            actionable.compactMap(\.rawCode),
            ["QUOTA_EXHAUSTED", "FUTURE_OPERATIONAL_RISK"]
        )
    }

    private func project(
        supervised: Bool,
        storage: String = "ONLINE"
    ) -> ProjectView {
        decode(
            """
            {"project_id":"project-1","display_name":"Project One","canonical_repo_root":"/tmp/project","git_root":"/tmp/project","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"\(storage)","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":\(supervised),"unattended_allowed":false,"grace_seconds":180}
            """
        )
    }

    private func target(
        id: String = "target-1",
        verified: Bool = true,
        runtimeAvailable: Bool = true,
        observedState: String? = "AVAILABLE"
    ) -> ExecutionTargetHealthView {
        let observed: String
        if let observedState {
            observed = """
            {"state":"\(observedState)","measurement_source":"PROVIDER_API","confidence":"EXACT","observed_at":"2026-09-15T00:00:00Z","sanitized_reason_code":null}
            """
        } else {
            observed = "null"
        }
        return decode(
            """
            {"execution_target_id":"\(id)","model_sku_id":"model-\(id)","runtime_id":"pi","enabled":true,"execution_verified":\(verified),"runtime_available":\(runtimeAvailable),"observed_availability":\(observed)}
            """
        )
    }

    private func providers(_ targets: [ExecutionTargetHealthView]) -> ProviderHealthListView {
        ProviderHealthListView(
            providers: [
                ProviderHealthView(
                    providerId: "provider-1",
                    displayName: "Provider One",
                    accountCount: 1,
                    quotaPools: [],
                    executionTargets: targets
                )
            ]
        )
    }

    private func risk(rawCode: String, destination: String) throws -> RiskItemView {
        let object: [String: Any] = [
            "id": "risk-\(rawCode)",
            "title": "Risk",
            "detail": "Detail",
            "severity": "WARNING",
            "destination": destination,
            "raw_code": rawCode,
            "count": 1,
        ]
        let data = try JSONSerialization.data(withJSONObject: object)
        return try JSONDecoder().decode(RiskItemView.self, from: data)
    }

    private func decode<T: Decodable>(_ json: String) -> T {
        // swiftlint:disable:next force_try
        try! JSONDecoder().decode(T.self, from: Data(json.utf8))
    }
}
