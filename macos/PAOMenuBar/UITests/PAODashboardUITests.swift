import XCTest

/// Minimal packaged-app UI regressions for the interactive dashboard.
///
/// These assert the B2 product information architecture — five first-level
/// destinations, the old backend-shaped ones absent — plus overview metric
/// navigation, task selection, and the new-task surface, against the built host
/// application. Labels are the zh-Hans product strings, which is what the owner
/// actually sees.
///
/// Navigation is driven by accessibility identifiers, not title text: since B5
/// every page also renders its title in the shared content header, so a text
/// query cannot distinguish a sidebar row from a page header. Geometry
/// assertions compare accessibility frames, which carry sub-pixel rendering
/// variance; tolerances are layout-scale (a few points), never pixel-scale.
final class PAODashboardUITests: XCTestCase {
    /// The frozen first-level navigation: raw destination → zh-Hans title.
    private static let destinations: [(raw: String, title: String)] = [
        ("overview", "总览"),
        ("tasks", "任务"),
        ("resources", "资源"),
        ("activity", "活动"),
        ("settings", "设置"),
    ]

    /// Concepts that were reorganized in B2. None may be a sidebar row again.
    private static let removedDestinations = [
        "项目", "模型与服务", "额度", "调度决策", "验证", "历史", "执行目标",
    ]

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// A sidebar row, identified by destination.
    private func sidebarRow(_ raw: String, in app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(identifier: "sidebar.\(raw)").firstMatch
    }

    func testSidebarOffersExactlyTheFiveProductDestinations() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let sidebar = app.outlines.firstMatch
        XCTAssertTrue(sidebar.waitForExistence(timeout: 10), "sidebar missing")

        // Exactly the five identified rows, each reachable and each titling
        // the window with its own product name.
        for (raw, title) in Self.destinations {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(
                row.waitForExistence(timeout: 5),
                "sidebar row missing for \(raw); first-level navigation changed"
            )
            row.click()
            XCTAssertEqual(app.windows.firstMatch.title, title)
        }
    }

    func testRemovedFirstLevelDestinationsAreNotSidebarRows() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

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
        DashboardWindow.open(in: app)

        // Every sidebar row must be clickable and must visibly change the
        // detail content (asserted through the window title).
        for (raw, title) in Self.destinations {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(raw)")
            row.click()
            XCTAssertEqual(app.windows.firstMatch.title, title)
        }
    }

    /// Providers, execution targets and quota are one workspace.
    ///
    /// B2 collapsed the three destinations into a segmented control; B4 replaced
    /// the segments with a selection/detail workspace, because a tab strip made
    /// the owner visit three surfaces and join them by memory to read one
    /// provider. What B2 actually protected was that all three capabilities
    /// survive inside Resources, so that is what this asserts — not the widget
    /// that happened to carry them.
    func testResourcesOwnsProvidersExecutionTargetsAndQuota() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let resources = sidebarRow("resources", in: app)
        XCTAssertTrue(resources.waitForExistence(timeout: 10))
        resources.click()

        // The resource collection, searchable as one list rather than split
        // across tabs.
        XCTAssertTrue(
            app.searchFields.firstMatch.waitForExistence(timeout: 5),
            "the resource collection lost its search field"
        )

        // Both daemon operations, still named for what they do. Discovery reads
        // no quota and the quota read runs no discovery; one merged "Refresh"
        // would hide which of the two ran.
        for operation in ["刷新发现", "刷新额度"] {
            XCTAssertTrue(
                app.buttons.matching(
                    NSPredicate(format: "label CONTAINS %@", operation)
                ).firstMatch.waitForExistence(timeout: 5),
                "Resources no longer offers \(operation) as its own operation"
            )
        }

        // The segmented host is gone: its three tabs must not be back.
        for surface in ["模型与服务", "执行目标"] {
            XCTAssertFalse(
                app.radioButtons[surface].firstMatch.exists,
                "\(surface) is a tab again; Resources is one workspace"
            )
        }
    }

    /// Projects stopped being a destination; it must still be reachable, and its
    /// registration entry point must still be there (full migration is B7).
    func testSettingsReachesProjects() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let settings = sidebarRow("settings", in: app)
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

    /// History moved under Activity: the destination shows daemon events, or
    /// says there are none. It no longer draws a second "History" heading over
    /// them — the page is the history.
    func testActivityShowsHistory() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let activity = sidebarRow("activity", in: app)
        XCTAssertTrue(activity.waitForExistence(timeout: 10))
        activity.click()
        let row = app.descendants(matching: .any)
            .matching(identifier: "dashboard.eventRow").firstMatch
        XCTAssertTrue(
            row.waitForExistence(timeout: 5) || app.staticTexts["暂无事件。"].exists,
            "Activity shows neither events nor its empty state"
        )
    }

    /// Clicking the Running tile opens Tasks narrowed to running tasks.
    ///
    /// The tile is addressed by its shared KPI identifier: the tile's visible
    /// text is localized (运行中 / Running) and a former version of this test
    /// queried the machine string "RUNNING", which no surface renders.
    func testOverviewMetricTileNavigatesToFilteredTasks() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let runningTile = app.buttons.matching(identifier: "overview.kpiTile").firstMatch
        XCTAssertTrue(runningTile.waitForExistence(timeout: 10), "KPI tiles missing")
        runningTile.click()
        XCTAssertEqual(app.windows.firstMatch.title, "任务")
    }

    func testTaskRowSelectionLoadsDetail() throws {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        // Create a task through the real new-task surface.
        // Unique per run: the daemon suppresses duplicate submissions, so a
        // fixed intent made this test pass once and then assert against a task
        // the daemon had refused to create.
        let marker = String(UUID().uuidString.prefix(8))
        let intent = "UI 测试任务：侧栏导航回归 \(marker)"
        app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch.click()
        let editor = app.textViews.firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.click()
        editor.typeText(intent)
        let submit = app.buttons["提交"].firstMatch
        XCTAssertTrue(submit.waitForExistence(timeout: 5))
        submit.click()

        // After submit the app navigates to Tasks with the new task selected.
        // Submission is a round trip to the daemon, so this waits for the
        // destination rather than sampling it in the same run loop turn.
        let arrivedAtTasks = expectation(
            for: NSPredicate(format: "title == %@", "任务"),
            evaluatedWith: app.windows.firstMatch
        )
        wait(for: [arrivedAtTasks], timeout: 15)

        // B3's Task Detail is headed by the task itself rather than by a
        // "Task detail" panel title, so the task's own intent is the assertion.
        // Matched on value as well as label: a SwiftUI `Text` publishes its
        // string as the element's value, and a label-only predicate matched
        // nothing however plainly the task was on screen.
        let carriesIntent = NSPredicate(
            format: "label CONTAINS %@ OR value CONTAINS %@", marker, marker
        )
        XCTAssertTrue(
            app.staticTexts.matching(carriesIntent).firstMatch.waitForExistence(timeout: 15),
            "the submitted task is not shown in the task workspace"
        )
    }

    /// Routing stopped being a destination; its explanation now belongs to the
    /// task it explains, and the no-selection guidance stays actionable.
    ///
    /// B3 keeps this contract: the Tasks canvas either shows a selected task or
    /// an empty state that says what is missing and what to do about it.
    func testTasksNeverPresentsADeadCanvas() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let tasks = sidebarRow("tasks", in: app)
        XCTAssertTrue(tasks.waitForExistence(timeout: 10))
        tasks.click()
        // Either routing detail for a selected task, or an actionable empty state
        // with a way forward; the canvas must never be a dead panel.
        let routing = app.staticTexts["调度决策"].firstMatch
        let newTaskButton = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch
        let addProvider = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "添加提供商")).firstMatch
        XCTAssertTrue(
            routing.waitForExistence(timeout: 5)
                || newTaskButton.exists
                || addProvider.exists
        )
    }

    // MARK: - B5 layout system structure

    /// The core defect this repair addresses: each page carried its own header
    /// geometry, so the title, the divider and the first content baseline moved
    /// when the owner switched destinations, and the sidebar changed width with
    /// them.
    ///
    /// The page title is now the window's toolbar title, which is one piece of
    /// chrome for all five; what remains to protect is that the content region
    /// under it is identical on every destination — same origin, same size — so
    /// nothing below the toolbar can jump either.
    func testContentRegionHoldsItsGeometryAcrossDestinations() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        var reference: CGRect?
        for (raw, title) in Self.destinations {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(raw)")
            row.click()

            // The page title itself: carried by the window, so it cannot differ
            // in position between destinations by construction.
            XCTAssertEqual(app.windows.firstMatch.title, title)

            // The sidebar, measured from each destination in turn. Its width is
            // what sets every page's content leading edge, and it is what used
            // to change: the workspace pages pushed the split wider than the
            // window and the sidebar was clipped to absorb it.
            let probe = sidebarRow("overview", in: app)
            XCTAssertTrue(probe.waitForExistence(timeout: 5), "sidebar row vanished on \(raw)")
            let frame = probe.frame
            if let reference {
                XCTAssertEqual(
                    frame.minX, reference.minX, accuracy: 1.0,
                    "\(raw) moved the sidebar"
                )
                XCTAssertEqual(
                    frame.width, reference.width, accuracy: 1.0,
                    "\(raw) changed the sidebar width; every page's content edge moves with it"
                )
            } else {
                reference = frame
            }
        }
    }

    /// The smallest window the dashboard supports still puts every destination
    /// inside its pane.
    ///
    /// The regression this pins: a wrapping, centered `Text` under
    /// `fixedSize(vertical:)` reported a very tall height when the split view
    /// probed its detail pane's minimum width. That height became the pane's
    /// minimum, so at 1000x700 Tasks — the one destination showing that empty
    /// state in a detail pane — pushed the whole navigation split taller than
    /// the window, and the sidebar slid up under the title bar with it. Every
    /// other destination stayed put, which is exactly what made it invisible
    /// until someone shrank the window and clicked Tasks.
    func testSmallWindowKeepsEveryDestinationInItsPane() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let window = app.windows.firstMatch
        XCTAssertTrue(window.waitForExistence(timeout: 20))
        DashboardWindow.place(window, atSize: "1000x700", test: self)

        var reference: CGRect?
        for (raw, _) in Self.destinations {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(raw)")
            row.click()

            let probe = sidebarRow("overview", in: app)
            XCTAssertTrue(probe.waitForExistence(timeout: 5))
            let frame = probe.frame

            // Inside the window, below the title bar — never under it.
            XCTAssertGreaterThan(
                frame.minY, window.frame.minY + 30,
                "\(raw) drew the sidebar under the title bar at 1000x700"
            )
            if let reference {
                XCTAssertEqual(
                    frame.minY, reference.minY, accuracy: 1.0,
                    "\(raw) moved the sidebar at 1000x700"
                )
            } else {
                reference = frame
            }
        }
    }

    /// The global toolbar actions keep one position on every destination.
    ///
    /// Pixel evidence cannot settle this: a system permission prompt floats over
    /// that corner of the window during an unattended capture. Accessibility
    /// frames can, and they are what the contract is actually about.
    func testGlobalToolbarActionsHoldTheirPosition() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        var reference: CGRect?
        for (raw, _) in Self.destinations {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(raw)")
            row.click()

            let newTask = app.buttons
                .matching(NSPredicate(format: "label CONTAINS %@", "新建任务")).firstMatch
            XCTAssertTrue(
                newTask.waitForExistence(timeout: 5),
                "\(raw) lost the global new-task action"
            )
            let frame = newTask.frame
            if let reference {
                XCTAssertEqual(
                    frame.minY, reference.minY, accuracy: 1.0,
                    "\(raw) toolbar sits at a different height"
                )
                XCTAssertEqual(
                    frame.height, reference.height, accuracy: 1.0,
                    "\(raw) toolbar control changed size"
                )
            } else {
                reference = frame
            }
        }
    }

    /// Tasks and Resources are one workspace grammar: collection → detail from
    /// the same width token, with the old segmented tabs still gone. If either
    /// page regains a private split or a tab strip, this fails.
    func testTasksAndResourcesShareTheWorkspaceSplit() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        var collectionWidths: [String: CGFloat] = [:]
        for raw in ["tasks", "resources"] {
            let row = sidebarRow(raw, in: app)
            XCTAssertTrue(row.waitForExistence(timeout: 10))
            row.click()

            let collection = app.descendants(matching: .any)
                .matching(identifier: "workspace.collection").firstMatch
            let detail = app.descendants(matching: .any)
                .matching(identifier: "workspace.detail").firstMatch
            XCTAssertTrue(
                collection.waitForExistence(timeout: 5),
                "\(raw) lost its collection column"
            )
            XCTAssertTrue(
                detail.waitForExistence(timeout: 5),
                "\(raw) lost its detail pane"
            )
            // Detail must sit beside the collection, not below or over it.
            XCTAssertLessThan(
                collection.frame.maxX, detail.frame.minX + 1,
                "\(raw) detail overlaps its collection"
            )
            collectionWidths[raw] = collection.frame.width

            // The pre-B4 Resources surface was three segmented tabs; neither
            // workspace may reintroduce a tab strip.
            XCTAssertEqual(
                app.segmentedControls.count, 0,
                "\(raw) rendered a segmented control; workspaces are not tabs"
            )
        }

        // Both pages start their divider from the same token, so their
        // collection widths must agree on a fresh launch — and must land on the
        // canonical width rather than on whatever maximum the split view chose.
        let tasksWidth = collectionWidths["tasks"] ?? 0
        let resourcesWidth = collectionWidths["resources"] ?? 0
        // Measured the same way on both pages, so a difference is a real
        // difference. The canonical value itself is pinned by the layout
        // contract unit test; an accessibility frame is the union of a
        // column's contents and is not the column's layout width.
        XCTAssertEqual(
            tasksWidth, resourcesWidth, accuracy: 4,
            "Tasks and Resources split positions disagree: \(tasksWidth) vs \(resourcesWidth)"
        )
    }

    /// The Overview KPI row is one row: five tiles, equal width, equal height,
    /// one shared baseline. B4 shipped tiles whose heights drifted with their
    /// content; this pins the contract they now share. Frames carry sub-pixel
    /// rendering variance, so equality means "within a few points".
    func testOverviewKPITilesShareGeometry() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let tiles = app.buttons.matching(identifier: "overview.kpiTile")
        XCTAssertTrue(
            tiles.firstMatch.waitForExistence(timeout: 10),
            "Overview KPI tiles are not exposed with the shared identifier"
        )
        let frames = tiles.allElementsBoundByIndex.map(\.frame)
        XCTAssertEqual(frames.count, 5, "the KPI row must stay five tiles")

        let heightSpread = frames.map(\.height).max()! - frames.map(\.height).min()!
        let widthSpread = frames.map(\.width).max()! - frames.map(\.width).min()!
        let baselineSpread = frames.map(\.minY).max()! - frames.map(\.minY).min()!
        XCTAssertLessThanOrEqual(heightSpread, 3, "KPI tile heights differ: \(frames)")
        XCTAssertLessThanOrEqual(widthSpread, 3, "KPI tile widths differ: \(frames)")
        XCTAssertLessThanOrEqual(baselineSpread, 2, "KPI tiles are not on one baseline: \(frames)")
    }

    /// Activity must occupy the main content pane as one table: rows share a
    /// leading edge, instead of the B4 layout where content hugged the left of
    /// the window. With no daemon there are no rows; the empty state is then
    /// the honest assertion.
    func testActivitySpansTheMainPane() {
        let app = XCUIApplication()
        app.launch()
        DashboardWindow.open(in: app)

        let activity = sidebarRow("activity", in: app)
        XCTAssertTrue(activity.waitForExistence(timeout: 10))
        activity.click()

        let content = app.descendants(matching: .any)
            .matching(identifier: "page.content").firstMatch
        XCTAssertTrue(content.waitForExistence(timeout: 5), "Activity lost its content region")

        let rows = app.descendants(matching: .any)
            .matching(identifier: "dashboard.eventRow").allElementsBoundByIndex
        if rows.isEmpty {
            XCTAssertTrue(
                app.staticTexts["暂无事件。"].exists,
                "Activity has neither rows nor its empty state"
            )
            return
        }

        let leadingEdges = rows.map { $0.frame.minX }
        let spread = leadingEdges.max()! - leadingEdges.min()!
        XCTAssertLessThanOrEqual(
            spread, 2,
            "Activity rows do not share a leading edge: \(rows.map(\.frame))"
        )

        // The old defect: a narrow column on the left of a wide pane. A row's
        // accessibility frame is the union of its three columns, which ends
        // where the summary text ends rather than at the table's right edge —
        // so this asks that a row occupies most of the table, not all of it.
        let table = min(
            content.frame.width - 2 * 20,
            980
        )
        let widest = rows.map(\.frame.width).max() ?? 0
        XCTAssertGreaterThan(
            widest, table * 0.6,
            "Activity rows use \(widest)pt of a \(content.frame.width)pt pane"
        )
    }
}
