import XCTest

/// Minimal packaged-app UI regressions for the interactive dashboard.
///
/// These assert the B2 product information architecture — five first-level
/// destinations, the old backend-shaped ones absent — plus overview metric
/// navigation, task selection, and the new-task surface, against the built host
/// application. Labels are the zh-Hans product strings, which is what the owner
/// actually sees.
final class PAODashboardUITests: XCTestCase {
    /// The frozen first-level navigation, in order.
    private static let destinations = ["总览", "任务", "资源", "活动", "设置"]

    /// Concepts that were reorganized in B2. None may be a sidebar row again.
    private static let removedDestinations = [
        "项目", "模型与服务", "额度", "调度决策", "验证", "历史", "执行目标",
    ]

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSidebarOffersExactlyTheFiveProductDestinations() {
        let app = XCUIApplication()
        app.launch()

        let sidebar = app.outlines.firstMatch
        XCTAssertTrue(sidebar.waitForExistence(timeout: 10), "sidebar missing")

        // Exactly five rows, in the frozen order.
        let rows = sidebar.staticTexts.allElementsBoundByIndex.map(\.label)
        XCTAssertEqual(rows, Self.destinations, "first-level navigation changed")
    }

    func testRemovedFirstLevelDestinationsAreNotSidebarRows() {
        let app = XCUIApplication()
        app.launch()

        let sidebar = app.outlines.firstMatch
        XCTAssertTrue(sidebar.waitForExistence(timeout: 10))
        for removed in Self.removedDestinations {
            XCTAssertFalse(
                sidebar.staticTexts[removed].exists,
                "\(removed) must no longer be a first-level destination"
            )
        }
    }

    func testSidebarNavigationVisitsEveryDestination() {
        let app = XCUIApplication()
        app.launch()

        // Every sidebar row must be clickable and must visibly change the
        // detail content (asserted through the window title).
        for destination in Self.destinations {
            let row = app.staticTexts[destination].firstMatch
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(destination)")
            row.click()
            XCTAssertEqual(app.windows.firstMatch.title, destination)
        }
    }

    /// Providers, execution targets and quota collapsed into Resources. All three
    /// must still be reachable — as surfaces inside it, not as destinations.
    func testResourcesReachesProvidersExecutionTargetsAndQuota() {
        let app = XCUIApplication()
        app.launch()

        let resources = app.staticTexts["资源"].firstMatch
        XCTAssertTrue(resources.waitForExistence(timeout: 10))
        resources.click()
        for surface in ["模型与服务", "执行目标", "额度"] {
            let tab = app.radioButtons[surface].firstMatch
            XCTAssertTrue(tab.waitForExistence(timeout: 5), "Resources is missing \(surface)")
            tab.click()
        }
    }

    /// Projects stopped being a destination; it must still be reachable, and its
    /// registration entry point must still be there (full migration is B7).
    func testSettingsReachesProjects() {
        let app = XCUIApplication()
        app.launch()

        let settings = app.staticTexts["设置"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.click()
        let projectsTab = app.radioButtons["项目"].firstMatch
        XCTAssertTrue(projectsTab.waitForExistence(timeout: 5), "Settings is missing Projects")
        projectsTab.click()
        XCTAssertTrue(
            app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "添加项目"))
                .firstMatch.waitForExistence(timeout: 5),
            "project registration entry point disappeared"
        )
    }

    /// History moved under Activity.
    func testActivityShowsHistory() {
        let app = XCUIApplication()
        app.launch()

        let activity = app.staticTexts["活动"].firstMatch
        XCTAssertTrue(activity.waitForExistence(timeout: 10))
        activity.click()
        XCTAssertTrue(app.staticTexts["历史"].waitForExistence(timeout: 5))
    }

    func testOverviewMetricTileNavigatesToFilteredTasks() {
        let app = XCUIApplication()
        app.launch()

        let runningTile = app.staticTexts["RUNNING"].firstMatch
        XCTAssertTrue(runningTile.waitForExistence(timeout: 10))
        runningTile.click()
        XCTAssertEqual(app.windows.firstMatch.title, "任务")
    }

    func testTaskRowSelectionLoadsDetail() throws {
        let app = XCUIApplication()
        app.launch()

        // Create a task through the real new-task surface.
        app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch.click()
        let editor = app.textViews.firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.click()
        editor.typeText("UI 测试任务：侧栏导航回归")
        let submit = app.buttons["提交"].firstMatch
        XCTAssertTrue(submit.waitForExistence(timeout: 5))
        submit.click()

        // After submit the app navigates to Tasks with the new task selected.
        XCTAssertTrue(app.staticTexts["任务详情"].waitForExistence(timeout: 15))
        XCTAssertEqual(app.windows.firstMatch.title, "任务")
    }

    /// Routing stopped being a destination; its explanation now belongs to the
    /// task it explains, and the no-selection guidance stays actionable.
    func testTasksNeverPresentsADeadCanvas() {
        let app = XCUIApplication()
        app.launch()

        let tasks = app.staticTexts["任务"].firstMatch
        XCTAssertTrue(tasks.waitForExistence(timeout: 10))
        tasks.click()
        // Either routing detail for a selected task, or an actionable empty state
        // with a way forward; the canvas must never be a dead panel.
        let routingExplanation = app.staticTexts["调度说明"].firstMatch
        let newTaskButton = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch
        let addProvider = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "添加提供商")).firstMatch
        XCTAssertTrue(
            routingExplanation.waitForExistence(timeout: 5)
                || newTaskButton.exists
                || addProvider.exists
        )
    }
}
