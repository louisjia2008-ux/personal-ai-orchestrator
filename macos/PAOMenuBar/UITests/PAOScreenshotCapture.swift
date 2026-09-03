import XCTest

/// Real-build screenshot capture.
///
/// Not a regression test: it drives the packaged app at a canonical window size
/// and writes one PNG per destination, so the layout can be reviewed against
/// what actually renders rather than against what the code intended to render.
/// Skipped unless `PAO_SCREENSHOT_DIR` names a directory to write into, so the
/// ordinary test run is unaffected.
///
/// The window size is not set from here: XCUITest cannot resize a macOS window
/// and the accessibility route is denied to the runner. The caller sets the
/// window's autosaved frame before launching instead; this test records the
/// size it actually observed so a capture at the wrong size is visible rather
/// than silent.
final class PAOScreenshotCapture: XCTestCase {
    private static let destinations: [(raw: String, file: String)] = [
        ("overview", "01_overview"),
        ("tasks", "02_tasks"),
        ("resources", "03_resources"),
        ("activity", "04_activity"),
        ("settings", "05_settings"),
    ]

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testCaptureCanonicalWindowSizes() throws {
        let environment = ProcessInfo.processInfo.environment
        // Not a regression test: it is a capture tool that happens to live in
        // the UI test target, so an ordinary `xcodebuild test` skips it rather
        // than failing on a missing output directory.
        try XCTSkipIf(
            environment["PAO_SCREENSHOT_DIR"]?.isEmpty != false,
            "PAO_SCREENSHOT_DIR unset; capture runs from scripts/capture_dashboard_screenshots.sh"
        )

        // The runner is sandboxed and cannot write into an arbitrary directory,
        // so the PNGs land in its own temporary directory and the caller copies
        // them out from the paths printed below.
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pao-screenshots", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true
        )

        // The exact bundle under review, not whatever the test scheme built:
        // the Release app is the artifact the owner opens, and it is the one
        // that carries the daemon the dashboard reads from.
        let app: XCUIApplication
        if let bundlePath = environment["PAO_APP_PATH"], !bundlePath.isEmpty {
            app = XCUIApplication(url: URL(fileURLWithPath: bundlePath))
        } else {
            app = XCUIApplication()
        }
        app.launch()
        DashboardWindow.open(in: app)

        let window = app.windows.firstMatch
        XCTAssertTrue(window.waitForExistence(timeout: 20), "no dashboard window")
        // A system permission prompt anchored to the window covers the toolbar
        // in the capture. Dismissing it leaves the permission unanswered, which
        // is what an unattended capture should do.
        window.typeKey(XCUIKeyboardKey.escape, modifierFlags: [])

        if let spec = environment["PAO_SCREENSHOT_SIZE"], !spec.isEmpty {
            place(window, atSize: spec)
        }

        // Named for the size actually achieved, not the size requested: a
        // window the screen could not fit must not be filed as if it had.
        let size = window.frame.size
        let label = "\(Int(size.width))x\(Int(size.height))"
        print("PAO_WINDOW_FRAME \(label)")

        for (raw, file) in Self.destinations {
            let row = app.descendants(matching: .any)
                .matching(identifier: "sidebar.\(raw)").firstMatch
            XCTAssertTrue(row.waitForExistence(timeout: 10), "sidebar row missing: \(raw)")
            row.click()
            // Let the destination settle before the shutter.
            Thread.sleep(forTimeInterval: 1.5)

            let url = directory.appendingPathComponent("\(file)_\(label).png")
            try window.screenshot().pngRepresentation.write(to: url)
            print("PAO_SCREENSHOT_WROTE \(url.path)")

            if let full = environment["PAO_SCREENSHOT_FULLSCREEN"], !full.isEmpty {
                let screenURL = directory
                    .appendingPathComponent("screen_\(file)_\(label).png")
                try XCUIScreen.main.screenshot().pngRepresentation
                    .write(to: screenURL)
                print("PAO_SCREENSHOT_WROTE \(screenURL.path)")
            }
        }
    }

    /// Places the window at an exact size, and where the capture can see it.
    ///
    /// The autosaved frame decides the size of a *restored* window; the window
    /// this test opens is created after launch and does not restore, so the
    /// only way to state its size from outside the app is to move and resize it
    /// the way a person would. The order matters: the window is moved to the
    /// top of the screen first, because a window sitting on the bottom edge
    /// cannot be grown downwards, and is moved down afterwards when it is short
    /// enough — macOS floats permission prompts across the upper part of the
    /// screen, and they land in the capture.
    private func place(_ window: XCUIElement, atSize spec: String) {
        let parts = spec.split(separator: "x").compactMap { Double($0) }
        guard parts.count == 2 else { return XCTFail("bad PAO_SCREENSHOT_SIZE: \(spec)") }
        let target = CGSize(width: parts[0], height: parts[1])

        // Usually a no-op: the caller sets the window's saved frame before
        // launch, and this only has to correct a window macOS decided to place
        // somewhere else.
        guard abs(window.frame.width - target.width) > 1
            || abs(window.frame.height - target.height) > 1
        else { return }

        move(window, toOrigin: CGPoint(x: 40, y: 60))
        resize(window, to: target)
        // Clear of the prompt zone when the screen leaves room for it.
        let low = CGPoint(x: 40, y: 520)
        if low.y + window.frame.height <= 1400 {
            move(window, toOrigin: low)
        }
    }

    private func move(_ window: XCUIElement, toOrigin origin: CGPoint) {
        let frame = window.frame
        let delta = CGVector(dx: origin.x - frame.minX, dy: origin.y - frame.minY)
        guard abs(delta.dx) > 1 || abs(delta.dy) > 1 else { return }
        // Grab the title bar, not the toolbar controls in it.
        let grip = window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0))
            .withOffset(CGVector(dx: 0, dy: 12))
        grip.press(forDuration: 0.4, thenDragTo: grip.withOffset(delta))
        Thread.sleep(forTimeInterval: 0.6)
    }

    private func resize(_ window: XCUIElement, to target: CGSize) {
        let current = window.frame.size
        guard abs(current.width - target.width) > 1 || abs(current.height - target.height) > 1
        else { return }
        let corner = window.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 1))
        let destination = corner.withOffset(
            CGVector(dx: target.width - current.width, dy: target.height - current.height)
        )
        corner.press(forDuration: 0.4, thenDragTo: destination)
        Thread.sleep(forTimeInterval: 0.6)
    }
}
