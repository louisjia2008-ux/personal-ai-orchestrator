import Foundation
import XCTest

@testable import PAOControlKit

@MainActor
final class QuickSubmitResultTests: XCTestCase {
    func testFailureAfterSuccessReturnsFailureWithoutThePreviousTask() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit-result-failure")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)

        let first = await store.quickSubmit(projectId: "project-fixture", intent: "first")
        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))

        daemon.route(
            "POST", "/v1/tasks", status: 503,
            body: #"{"error":"unavailable"}"#, replaceExisting: true
        )
        let second = await store.quickSubmit(projectId: "project-fixture", intent: "second")
        XCTAssertEqual(second, .failed(detail: "unavailable (HTTP 503)"))
        XCTAssertEqual(store.submitNotice, second)
        XCTAssertNil(store.lastSubmittedTaskId)
        // Holding an earlier result is safe; it cannot turn the second into success.
        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertEqual(submitCount(daemon), 2)
    }

    func testDuplicateAfterSuccessReturnsBlockedWithoutAnotherPost() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit-result-duplicate")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)

        let first = await store.quickSubmit(projectId: "project-fixture", intent: "first")
        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        let duplicate = await store.quickSubmit(projectId: "project-fixture", intent: " first\n")

        XCTAssertEqual(duplicate, .duplicateBlocked(windowSeconds: 5))
        XCTAssertEqual(store.submitNotice, duplicate)
        XCTAssertNil(store.lastSubmittedTaskId)
        XCTAssertEqual(submitCount(daemon), 1)
    }

    func testEveryValidationExitAfterSuccessReturnsNoSubmittedTask() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit-result-validation")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)
        let cases: [(projectId: String?, intent: String, policy: String, result: SubmitNotice?)] = [
            (nil, "missing project", "BALANCED", .projectRequired),
            ("", "empty project", "BALANCED", .projectRequired),
            ("project-fixture", "missing target", "MANUAL", .manualTargetRequired),
            ("project-fixture", " \t\n", "BALANCED", nil),
        ]

        for (index, validation) in cases.enumerated() {
            store.selectedSchedulingPolicy = "BALANCED"
            let success = await store.quickSubmit(
                projectId: "project-fixture", intent: "accepted \(index)"
            )
            XCTAssertEqual(success, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
            XCTAssertNotNil(store.lastSubmittedTaskId)

            store.selectedSchedulingPolicy = validation.policy
            let result = await store.quickSubmit(
                projectId: validation.projectId, intent: validation.intent
            )
            XCTAssertEqual(result, validation.result)
            XCTAssertEqual(store.submitNotice, validation.result)
            XCTAssertNil(store.lastSubmittedTaskId)
        }
        XCTAssertEqual(submitCount(daemon), cases.count)
    }

    func testMalformedResponseAfterSuccessDoesNotReturnSuccess() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit-result-malformed")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)
        await store.quickSubmit(projectId: "project-fixture", intent: "first")

        daemon.route("POST", "/v1/tasks", status: 201, body: "{}", replaceExisting: true)
        let result = await store.quickSubmit(projectId: "project-fixture", intent: "second")

        XCTAssertEqual(result, .failed(detail: PAOClientError.malformedResponse.displayDetail))
        XCTAssertEqual(store.submitNotice, result)
        XCTAssertNil(store.lastSubmittedTaskId)
    }

    func testOverlappingSuccessesReturnTheirOwnTaskDespiteReverseCompletionOrder() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let started = expectation(description: "first submit started")
        let release = DispatchSemaphore(value: 0)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        let path = temporarySocketPath("submit-result-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        let firstRequest = Task {
            await store.quickSubmit(projectId: "project-fixture", intent: "first")
        }
        await fulfillment(of: [started], timeout: 2)
        daemon.route(
            "POST", "/v1/tasks", status: 201,
            body: submitResponseBody.replacingOccurrences(of: "abc", with: "second"),
            replaceExisting: true
        )
        let second = await store.quickSubmit(projectId: "project-fixture", intent: "second")
        XCTAssertEqual(second, .submitted(taskId: "menubar-second", state: "SUBMITTED"))

        release.signal()
        let first = await firstRequest.value
        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertEqual(store.submitNotice, second)
        XCTAssertEqual(store.lastSubmittedTaskId, "menubar-second")
        XCTAssertEqual(submitCount(daemon), 2)
    }

    func testOlderFailureCannotOverwriteANewerSuccessfulAttempt() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let started = expectation(description: "first failing submit started")
        let release = DispatchSemaphore(value: 0)
        daemon.route(
            "POST", "/v1/tasks", status: 503, body: #"{"error":"unavailable"}"#
        ) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        let path = temporarySocketPath("submit-result-error-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        let firstRequest = Task {
            await store.quickSubmit(projectId: "project-fixture", intent: "first")
        }
        await fulfillment(of: [started], timeout: 2)
        daemon.route(
            "POST", "/v1/tasks", status: 201, body: submitResponseBody, replaceExisting: true
        )
        let second = await store.quickSubmit(projectId: "project-fixture", intent: "second")
        release.signal()
        let first = await firstRequest.value

        XCTAssertEqual(first, .failed(detail: "unavailable (HTTP 503)"))
        XCTAssertEqual(second, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertEqual(store.submitNotice, second)
        XCTAssertEqual(store.lastSubmittedTaskId, "menubar-abc")
    }

    func testSuccessAwaitingRefreshCannotBorrowAnotherInvocationsTask() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "first submit accepted and refreshing")
        let release = DispatchSemaphore(value: 0)
        daemon.route("GET", "/v1/health", body: healthBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit-result-refresh-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        let firstRequest = Task {
            await store.quickSubmit(projectId: "project-fixture", intent: "first")
        }
        await fulfillment(of: [started], timeout: 2)
        XCTAssertEqual(store.lastSubmittedTaskId, "menubar-abc")
        daemon.route("GET", "/v1/health", body: healthBody, replaceExisting: true)
        daemon.route(
            "POST", "/v1/tasks", status: 201,
            body: submitResponseBody.replacingOccurrences(of: "abc", with: "second"),
            replaceExisting: true
        )
        let second = await store.quickSubmit(projectId: "project-fixture", intent: "second")
        release.signal()
        let first = await firstRequest.value

        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertEqual(second, .submitted(taskId: "menubar-second", state: "SUBMITTED"))
        XCTAssertEqual(store.submitNotice, second)
        XCTAssertEqual(store.lastSubmittedTaskId, "menubar-second")
    }

    func testNewerValidationExitInvalidatesAnOlderSuccessfulPresentation() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let started = expectation(description: "submit started before validation failure")
        let release = DispatchSemaphore(value: 0)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        let path = temporarySocketPath("submit-result-validation-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        let firstRequest = Task {
            await store.quickSubmit(projectId: "project-fixture", intent: "first")
        }
        await fulfillment(of: [started], timeout: 2)
        let second = await store.quickSubmit(projectId: nil, intent: "second")
        release.signal()
        let first = await firstRequest.value

        XCTAssertEqual(first, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertEqual(second, .projectRequired)
        XCTAssertEqual(store.submitNotice, .projectRequired)
        XCTAssertNil(store.lastSubmittedTaskId)
        XCTAssertEqual(submitCount(daemon), 1)
    }

    func testAcceptedResultSurvivesAFailedFollowUpRefresh() async throws {
        let daemon = TestDaemon()
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        daemon.route("GET", "/v1/health", status: 503, body: #"{"error":"unavailable"}"#)
        let path = temporarySocketPath("submit-result-refresh")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)

        let result = await store.quickSubmit(projectId: "project-fixture", intent: "first")

        // Creation succeeded even though current daemon state cannot be refreshed.
        // The returned receipt must not be reconstructed from shared presentation.
        XCTAssertEqual(result, .submitted(taskId: "menubar-abc", state: "SUBMITTED"))
        XCTAssertFalse(store.connection.isConnected)
        XCTAssertNil(store.submitNotice)
        XCTAssertNil(store.lastSubmittedTaskId)
    }

    /// Source contracts supplement the socket tests because the executable's
    /// private SwiftUI views cannot be driven from PAOControlKit's unit target.
    /// Native UI acceptance remains a separate gate.
    func testSheetDispatchAndNavigationUseOnlyTheCurrentInvocationResult() throws {
        let source = try viewSource("DashboardView.swift")
        let sheet = try XCTUnwrap(source.components(separatedBy: "private struct NewTaskSheet: View {").last)
            .components(separatedBy: "// MARK: - Projects")[0]
        let submit = try XCTUnwrap(sheet.components(separatedBy: "private func submit() {").last)

        XCTAssertFalse(submit.contains("store.lastSubmittedTaskId"))
        XCTAssertFalse(submit.contains("store.submitNotice"))
        XCTAssertTrue(submit.contains("let result = await store.quickSubmit"))
        XCTAssertTrue(submit.contains("case .submitted(let taskId, _) = result else { return }"))
        let success = try XCTUnwrap(submit.range(of: "case .submitted(let taskId, _) = result"))
        let dispatch = try XCTUnwrap(submit.range(of: "await store.dispatch(taskId: taskId"))
        let navigate = try XCTUnwrap(submit.range(of: "onSubmitted(taskId)"))
        XCTAssertLessThan(success.lowerBound, dispatch.lowerBound)
        XCTAssertLessThan(dispatch.lowerBound, navigate.lowerBound)
        XCTAssertTrue(submit.contains("guard !submitting else { return }"))
        XCTAssertTrue(sheet.contains(".onDisappear { submissionId = nil }"))
        let afterDispatch = String(submit[dispatch.upperBound..<navigate.lowerBound])
        XCTAssertTrue(afterDispatch.contains("guard submissionId == requestId else { return }"))
    }

    func testQuickSubmitPreservesFailedAndNewerDraftsAndIgnoresDismissedRequests() throws {
        let source = try viewSource("QuickSubmitView.swift")
        let submit = try XCTUnwrap(source.components(separatedBy: "private func submit() {").last)

        XCTAssertFalse(submit.contains("store.lastSubmittedTaskId"))
        XCTAssertFalse(submit.contains("store.submitNotice"))
        XCTAssertTrue(submit.contains("let result = await store.quickSubmit"))
        XCTAssertTrue(submit.contains("let result, case .submitted = result,"))
        XCTAssertTrue(submit.contains("intent == value else { return }"))
        XCTAssertTrue(submit.contains("guard !submitting else { return }"))
        XCTAssertTrue(submit.contains("guard submissionId == requestId,"))
        XCTAssertTrue(source.contains(".onDisappear { submissionId = nil }"))
        let success = try XCTUnwrap(submit.range(of: "case .submitted = result"))
        let clearDraft = try XCTUnwrap(submit.range(of: "intent = \"\""))
        XCTAssertLessThan(success.lowerBound, clearDraft.lowerBound)
    }

    private func manualStore(_ path: String) -> OrchestratorStore {
        OrchestratorStore(socketPath: path, autoStartDaemon: false, autoRefresh: false)
    }

    private func submitCount(_ daemon: TestDaemon) -> Int {
        daemon.receivedRequests.filter { $0.method == "POST" && $0.path == "/v1/tasks" }.count
    }

    private func viewSource(_ name: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(contentsOf: root.appendingPathComponent("Sources/PAOMenuBar/\(name)"))
    }
}
