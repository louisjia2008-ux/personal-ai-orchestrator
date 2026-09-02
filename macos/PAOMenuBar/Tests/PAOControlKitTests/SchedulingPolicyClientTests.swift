import Foundation

import XCTest

@testable import PAOControlKit

/// P4.2.6.1 §8/§24. The New Task scheduling policy previously lived only in SwiftUI
/// state, so it never reached the daemon. These tests hold the wire contract: the
/// owner's choice must appear in the submission body, MANUAL must carry its target,
/// and the KPI row must not regress to the old English aggregate label.
@MainActor
final class SchedulingPolicyClientTests: XCTestCase {

    private func submitBodies(_ daemon: TestDaemon) -> [String] {
        daemon.receivedRequests
            .filter { $0.method == "POST" && $0.path == "/v1/tasks" }
            .map(\.body)
    }

    // MARK: - Task policy reaches the daemon

    func testSubmittedTaskCarriesSelectedSchedulingPolicy() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("policy-submit")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        store.selectedSchedulingPolicy = "QUOTA_SAVER"
        await store.quickSubmit(projectId: "project-fixture", intent: "demo intent")

        let body = try XCTUnwrap(submitBodies(daemon).first)
        XCTAssertTrue(
            body.contains("\"scheduling_policy\":\"QUOTA_SAVER\""),
            "policy must be submitted, not kept in App state: \(body)"
        )
    }

    func testManualPolicySubmitsItsExecutionTarget() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("policy-manual")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        store.selectedSchedulingPolicy = "MANUAL"
        store.selectedManualExecutionTargetId = "zai-glm-53"
        await store.quickSubmit(projectId: "project-fixture", intent: "demo intent")

        let body = try XCTUnwrap(submitBodies(daemon).first)
        XCTAssertTrue(body.contains("\"scheduling_policy\":\"MANUAL\""))
        XCTAssertTrue(body.contains("\"manual_execution_target_id\":\"zai-glm-53\""))
    }

    func testManualPolicyWithoutTargetIsRefusedBeforeReachingTheDaemon() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("policy-manual-missing")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        store.selectedSchedulingPolicy = "MANUAL"
        store.selectedManualExecutionTargetId = nil
        await store.quickSubmit(projectId: "project-fixture", intent: "demo intent")

        // No silent fallback to another policy, and no half-formed submission.
        XCTAssertEqual(store.submitNotice, .manualTargetRequired)
        XCTAssertTrue(submitBodies(daemon).isEmpty)
    }

    func testNonManualPolicyDoesNotSendAManualTarget() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("policy-nonmanual")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        store.selectedSchedulingPolicy = "BALANCED"
        store.selectedManualExecutionTargetId = "left-over-selection"
        await store.quickSubmit(projectId: "project-fixture", intent: "demo intent")

        let body = try XCTUnwrap(submitBodies(daemon).first)
        XCTAssertTrue(body.contains("\"scheduling_policy\":\"BALANCED\""))
        // The key is omitted entirely, which the daemon reads as "no manual target".
        XCTAssertFalse(
            body.contains("left-over-selection"),
            "a stale manual selection must not ride along with a non-manual policy: \(body)"
        )
    }

    // MARK: - Wire shapes

    func testSubmitRequestEncodesPolicyKeys() throws {
        let request = SubmitRequest(
            taskId: "t", requestId: "r", projectId: "p", intent: "i",
            schedulingPolicy: "SPEED_FIRST", manualExecutionTargetId: nil
        )
        let data = try JSONEncoder().encode(request)
        let json = String(decoding: data, as: UTF8.self)

        XCTAssertTrue(json.contains("\"scheduling_policy\":\"SPEED_FIRST\""))
    }

    func testProviderConnectionListDecodesImportCandidates() throws {
        let json = """
        {"connected":[],"available_to_add":[],"import_candidates":[
          {"provider_id":"zai-coding-plan","display_name":"GLM / Z.AI","region":null,
           "plan_surface":"Coding Plan","model_skus":["glm-5.3"],"execution_verified":true,
           "auth_state":"AUTHENTICATED","credential_reference_type":"OPENCODE_AUTH",
           "evidence":[{"kind":"PRIOR_VERIFIED_EXECUTION","detail":"曾成功完成真实执行验证",
                        "observed_at":"2026-01-01T00:00:00+00:00"}]}]}
        """
        let view = try JSONDecoder().decode(
            ProviderConnectionListView.self, from: Data(json.utf8)
        )

        XCTAssertEqual(view.importCandidates.count, 1)
        XCTAssertEqual(view.importCandidates[0].providerId, "zai-coding-plan")
        XCTAssertEqual(view.importCandidates[0].planSurface, "Coding Plan")
        XCTAssertEqual(view.importCandidates[0].evidence.count, 1)
        // Candidacy is not connection.
        XCTAssertTrue(view.connected.isEmpty)
    }

    func testProviderConnectionListToleratesAnOlderDaemonWithoutCandidates() throws {
        let json = #"{"connected":[],"available_to_add":[]}"#
        let view = try JSONDecoder().decode(
            ProviderConnectionListView.self, from: Data(json.utf8)
        )

        XCTAssertTrue(view.importCandidates.isEmpty)
    }

    // MARK: - KPI labels

    func testVerificationKPIUsesOwnerFacingLabelNotTheEnglishAggregate() {
        // The old tile title "VERIFYING / VERIFIED" both read as raw protocol text and
        // wrapped, which is what broke the KPI row geometry.
        XCTAssertNotEqual(L10n.kpiVerification, "VERIFYING / VERIFIED")
        XCTAssertFalse(L10n.kpiVerification.contains("/"))
        for label in [L10n.kpiRunning, L10n.kpiReady, L10n.kpiBlocked, L10n.kpiCompleted] {
            XCTAssertFalse(label.isEmpty)
            XCTAssertNotEqual(label, label.uppercased() + " ")
        }
    }

    func testVerificationDetailKeepsBothCountsDistinct() {
        let detail = L10n.kpiVerificationDetail(verifying: 3, verified: 7)

        XCTAssertTrue(detail.contains("3"))
        XCTAssertTrue(detail.contains("7"))
    }

    func testEveryPolicyModeHasAnOwnerFacingNameAndExplanation() {
        for policy in ["BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "MANUAL"] {
            XCTAssertNotEqual(
                L10n.schedulingPolicyName(policy), policy,
                "\(policy) must render as an owner-facing name, not a raw enum"
            )
            XCTAssertFalse(L10n.schedulingPolicyDetail(policy).isEmpty)
        }
    }

    func testUnknownPolicyStaysVerbatimRatherThanBeingInvented() {
        XCTAssertEqual(L10n.schedulingPolicyName("CHEAPEST"), "CHEAPEST")
        XCTAssertEqual(L10n.policyResolutionSource("SOMETHING_NEW"), "SOMETHING_NEW")
    }

    func testPolicyResolutionSourcesAreOwnerReadable() {
        for source in ["TASK_OVERRIDE", "PROJECT_OVERRIDE", "GLOBAL_DEFAULT"] {
            XCTAssertNotEqual(L10n.policyResolutionSource(source), source)
        }
    }
}
