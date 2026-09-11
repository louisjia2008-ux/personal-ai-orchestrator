import Foundation

import XCTest

@testable import PAOControlKit

/// B3: the task collection is the operational surface of the app, so what it
/// contains, what it hides, and why it is empty are all pinned here.
///
/// The defects these guard against are silent by nature: a grouping that quietly
/// drops a state, a filter that empties the list without saying which narrowing
/// did it, and a truncated collection presented as the whole store.
final class TaskWorkspaceTests: XCTestCase {

    // MARK: - Groupings

    func testGroupsCoverEveryAuthoritativeState() {
        // A state in no group is a task no filter can reach.
        let grouped = TaskStateGroup.allCases.reduce(into: Set<String>()) {
            $0.formUnion($1.states)
        }
        XCTAssertEqual(grouped, Set(TaskStates.all))
    }

    func testGroupsAreDisjoint() {
        // Overlap would make two filters claim the same task, and the counts
        // beside them disagree.
        var seen = Set<String>()
        for group in TaskStateGroup.allCases {
            let overlap = seen.intersection(group.states)
            XCTAssertTrue(overlap.isEmpty, "\(group.rawValue) overlaps \(overlap)")
            seen.formUnion(group.states)
        }
    }

    func testGroupMembershipIsAuthoritativeNotInvented() {
        XCTAssertEqual(TaskStateGroup.queued.states, ["SUBMITTED", "READY"])
        XCTAssertEqual(TaskStateGroup.active.states, ["RUNNING", "WORKER_FINISHED", "VERIFYING"])
        // FAILED is not BLOCKED, so the pair is named for what they have in
        // common — the owner has to do something — rather than for one of them.
        XCTAssertEqual(TaskStateGroup.needsAttention.states, ["BLOCKED", "FAILED"])
        XCTAssertEqual(TaskStateGroup.completed.states, ["VERIFIED", "COMPLETED"])
        // A cancelled task did not complete. Filing it under completion would
        // report a stop as a success.
        XCTAssertEqual(TaskStateGroup.cancelled.states, ["CANCELLED"])
    }

    func testAStateThisBuildDoesNotKnowBelongsToNoGroup() {
        XCTAssertNil(TaskStateGroup.group(for: "AWAITING_HUMAN_REVIEW"))
        XCTAssertFalse(TaskStates.isKnown("AWAITING_HUMAN_REVIEW"))
    }

    // MARK: - Overview handoff

    func testCompositeCountersMapOntoGroupsWithTheSameMembership() {
        // The Overview tiles count composite states through `MetricsFilter`. If
        // the group and the counter disagreed, clicking a tile would open a list
        // that does not contain what the tile counted.
        for state in TaskStates.all {
            XCTAssertEqual(
                MetricsFilter.matches(state: state, filter: "READY"),
                TaskStateGroup.queued.contains(state),
                "READY counter and queued group disagree about \(state)"
            )
            XCTAssertEqual(
                MetricsFilter.matches(state: state, filter: "VERIFIED"),
                TaskStateGroup.completed.contains(state),
                "VERIFIED counter and completed group disagree about \(state)"
            )
        }
    }

    func testMetricsFilterResolvesToTheEquivalentSelection() {
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter(""), .all)
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter("READY"), .group(.queued))
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter("VERIFIED"), .group(.completed))
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter("RUNNING"), .state("RUNNING"))
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter("BLOCKED"), .state("BLOCKED"))
        XCTAssertEqual(TaskStateSelection.fromMetricsFilter("COMPLETED"), .state("COMPLETED"))
    }

    func testSelectionSurvivesStorageRoundTrip() {
        let cases: [TaskStateSelection] = [
            .all, .group(.needsAttention), .state("RUNNING"), .state("AWAITING_HUMAN_REVIEW"),
        ]
        for selection in cases {
            XCTAssertEqual(
                TaskStateSelection(storageValue: selection.storageValue), selection
            )
        }
        // Anything unparseable falls back to the selection that hides nothing.
        XCTAssertEqual(TaskStateSelection(storageValue: "nonsense"), .all)
        XCTAssertEqual(TaskStateSelection(storageValue: "group:invented"), .all)
    }

    func testAnUnknownStateIsStillSelectableByItsMachineValue() {
        // The exact-state case is what keeps the filter total.
        let selection = TaskStateSelection.state("AWAITING_HUMAN_REVIEW")
        XCTAssertTrue(selection.matches("AWAITING_HUMAN_REVIEW"))
        XCTAssertFalse(selection.matches("RUNNING"))
    }

    // MARK: - Cancellability

    func testStopIsOfferedOnlyWhereTheKernelAllowsCancellation() {
        for state in ["SUBMITTED", "READY", "RUNNING", "WORKER_FINISHED", "VERIFYING", "BLOCKED"] {
            XCTAssertTrue(TaskStates.isCancellable(state), state)
        }
        for state in ["VERIFIED", "COMPLETED", "FAILED", "CANCELLED"] {
            XCTAssertFalse(TaskStates.isCancellable(state), state)
        }
    }

    // MARK: - Search

    private let projectNames = ["proj-pintrace": "PinTrace", "proj-atlas": "Atlas"]

    func testSearchMatchesTitleIdRequestProjectAndState() {
        let task = makeTask(
            id: "PT-0042", intent: "Fix coordinate drift", project: "proj-pintrace",
            state: "RUNNING", requestId: "rq-7781"
        )
        for needle in ["coordinate", "PT-00", "rq-77", "pintrace", "RUNNING"] {
            var filter = TaskFilter(query: needle)
            XCTAssertTrue(
                filter.matchesQuery(task, projectName: projectNames["proj-pintrace"]), needle
            )
            filter.query = needle.uppercased()
            XCTAssertTrue(
                filter.matchesQuery(task, projectName: projectNames["proj-pintrace"]),
                "search must be case-insensitive: \(needle)"
            )
        }
    }

    func testSearchMatchesTheProjectDisplayNameNotOnlyItsId() {
        // The owner searches for the name they gave the project, which need not
        // appear anywhere in the opaque id the daemon assigned it.
        let task = makeTask(id: "PT-1", intent: "x", project: "proj-7f21", state: "RUNNING")
        let filter = TaskFilter(query: "PinTrace")
        XCTAssertTrue(filter.matchesQuery(task, projectName: "PinTrace"))
        XCTAssertFalse(
            filter.matchesQuery(task, projectName: nil),
            "with no resolved name there is nothing to match on"
        )
    }

    func testEmptySearchMatchesEverything() {
        let task = makeTask(id: "PT-1", intent: "x", project: nil, state: "RUNNING")
        XCTAssertTrue(TaskFilter(query: "   ").matchesQuery(task, projectName: nil))
    }

    // MARK: - Filtering

    private var mixedTasks: [TaskView] {
        [
            makeTask(id: "A", intent: "alpha", project: "proj-pintrace", state: "RUNNING"),
            makeTask(id: "B", intent: "beta", project: "proj-pintrace", state: "SUBMITTED"),
            makeTask(id: "C", intent: "gamma", project: "proj-atlas", state: "BLOCKED"),
            makeTask(id: "D", intent: "delta", project: nil, state: "COMPLETED"),
            makeTask(id: "E", intent: "epsilon", project: "proj-atlas", state: "CANCELLED"),
        ]
    }

    func testGroupFilterSelectsEveryStateInTheGroup() {
        let filter = TaskFilter(state: .group(.queued))
        XCTAssertEqual(filter.apply(to: mixedTasks).map(\.taskId), ["B"])
    }

    func testProjectFilterNarrowsToOneProject() {
        let filter = TaskFilter(projectId: "proj-atlas")
        XCTAssertEqual(filter.apply(to: mixedTasks).map(\.taskId), ["C", "E"])
    }

    func testStateAndProjectFiltersCompose() {
        let filter = TaskFilter(state: .group(.needsAttention), projectId: "proj-atlas")
        XCTAssertEqual(filter.apply(to: mixedTasks).map(\.taskId), ["C"])
    }

    func testFilteringOperatesOverTheWholeLoadedCollection() {
        // B0's guarantee, re-pinned at the filter: nothing narrows to a preview.
        let many = (0..<40).map {
            makeTask(id: "T-\($0)", intent: "task \($0)", project: nil, state: "COMPLETED")
        }
        XCTAssertEqual(TaskFilter(state: .group(.completed)).apply(to: many).count, 40)
    }

    // MARK: - Resolved collection state

    private let connected = ConnectionState.connected

    func testNotYetLoadedIsLoadingRatherThanEmpty() {
        // Claiming the store holds nothing before anything was asked would be a
        // fabricated fact about the daemon.
        let state = TaskCollection.resolve(
            tasks: nil, connection: connected, filter: TaskFilter()
        )
        XCTAssertEqual(state, .loading)
    }

    func testDisconnectedIsNotEmpty() {
        let state = TaskCollection.resolve(
            tasks: TaskListView(tasks: [], total: 0),
            connection: .disconnected(reason: .daemonNotRunning),
            filter: TaskFilter()
        )
        XCTAssertEqual(state, .disconnected(reason: .daemonNotRunning))
    }

    func testNoTasksAtAllIsItsOwnState() {
        let state = TaskCollection.resolve(
            tasks: TaskListView(tasks: [], total: 0),
            connection: connected,
            filter: TaskFilter()
        )
        XCTAssertEqual(state, .empty)
    }

    func testSearchWithNoMatchIsDistinguishableFromFilterWithNoMatch() {
        let list = TaskListView(tasks: mixedTasks, total: mixedTasks.count)
        let searched = TaskCollection.resolve(
            tasks: list, connection: connected, filter: TaskFilter(query: "zzz")
        )
        XCTAssertEqual(searched, .noSearchMatch)

        let filtered = TaskCollection.resolve(
            tasks: list, connection: connected,
            filter: TaskFilter(projectId: "proj-nonexistent")
        )
        XCTAssertEqual(filtered, .noFilterMatch)
    }

    func testTheNarrowingThatEmptiedTheListDecidesTheEmptyState() {
        // Filter keeps rows, search removes the rest: the owner should be told to
        // change the words, not to clear the filter.
        let list = TaskListView(tasks: mixedTasks, total: mixedTasks.count)
        let state = TaskCollection.resolve(
            tasks: list,
            connection: connected,
            filter: TaskFilter(query: "zzz", projectId: "proj-atlas")
        )
        XCTAssertEqual(state, .noSearchMatch)
    }

    func testPopulatedCarriesOnlyTheMatchingTasks() {
        let list = TaskListView(tasks: mixedTasks, total: mixedTasks.count)
        let state = TaskCollection.resolve(
            tasks: list, connection: connected,
            filter: TaskFilter(query: "alpha"),
            projectNames: projectNames
        )
        XCTAssertEqual(state.tasks.map(\.taskId), ["A"])
        XCTAssertTrue(state.isPopulated)
    }

    func testATruncatedCollectionStillRendersTheTasksItHas() {
        // Truncation is stated beside the list, never by emptying it.
        let list = TaskListView(tasks: mixedTasks, total: 900)
        let state = TaskCollection.resolve(
            tasks: list, connection: connected, filter: TaskFilter()
        )
        XCTAssertEqual(state.tasks.count, mixedTasks.count)
        XCTAssertLessThan(list.tasks.count, list.total)
    }

    // MARK: - Menu contents

    func testPresentStatesKeepCanonicalOrderAndKeepUnknownStates() {
        let tasks =
            mixedTasks
            + [makeTask(id: "F", intent: "f", project: nil, state: "AWAITING_HUMAN_REVIEW")]
        let states = TaskCollection.presentStates(in: tasks)
        XCTAssertEqual(states, ["SUBMITTED", "RUNNING", "BLOCKED", "CANCELLED", "COMPLETED", "AWAITING_HUMAN_REVIEW"])
        XCTAssertEqual(
            states.last, "AWAITING_HUMAN_REVIEW",
            "an unrecognized state must stay reachable, appended verbatim"
        )
    }

    func testPresentProjectsResolveDisplayNamesAndSkipTasksWithoutOne() {
        let projects = TaskCollection.presentProjects(
            in: mixedTasks, projectNames: projectNames
        )
        XCTAssertEqual(projects.map(\.name), ["Atlas", "PinTrace"])
        XCTAssertEqual(projects.count, 2, "a task with no project is not invented into one")
    }

    func testAProjectWithNoResolvedNameKeepsItsId() {
        let projects = TaskCollection.presentProjects(in: mixedTasks, projectNames: [:])
        XCTAssertEqual(Set(projects.map(\.name)), ["proj-atlas", "proj-pintrace"])
    }

    // MARK: - Helpers

    private func makeTask(
        id: String, intent: String, project: String?, state: String,
        requestId: String? = nil
    ) -> TaskView {
        let projectJSON = project.map { "\"\($0)\"" } ?? "null"
        let json = """
            {"task_id":"\(id)","request_id":"\(requestId ?? "rq-\(id)")",
             "intent":"\(intent)","project_id":\(projectJSON),"base_sha":null,
             "working_subpath":null,"state":"\(state)","state_version":1,
             "created_at":"2026-09-03T11:40:00Z","updated_at":"2026-09-03T11:41:00Z"}
            """
        // swiftlint:disable:next force_try
        return try! JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
    }
}
