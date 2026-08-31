import XCTest

/// Minimal packaged-app UI regressions for the interactive dashboard.
/// Exercises real sidebar navigation, overview metric navigation, task
/// selection, and the new-task surface against the built host application.
final class PAODashboardUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSidebarNavigationVisitsEverySection() {
        let app = XCUIApplication()
        app.launch()

        // Every sidebar row must be clickable and must visibly change the
        // detail content (asserted through the window title).
        let sections = ["任务", "Agents / 执行目标", "提供商", "额度", "调度决策", "验证", "历史", "设置", "总览"]
        for section in sections {
            let row = app.staticTexts[section].firstMatch
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(section)")
            row.click()
            XCTAssertEqual(app.windows.firstMatch.title, section)
        }
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

    func testEmptyStatesOfferNewTaskWhenConnected() {
        let app = XCUIApplication()
        app.launch()

        let routing = app.staticTexts["调度决策"].firstMatch
        XCTAssertTrue(routing.waitForExistence(timeout: 10))
        routing.click()
        // Either a task picker is offered or an actionable empty state with a
        // new-task entry point; the section must never be a dead panel.
        let picker = app.menus.firstMatch
        let newTaskButton = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch
        XCTAssertTrue(picker.waitForExistence(timeout: 5) || newTaskButton.exists)
    }
}
