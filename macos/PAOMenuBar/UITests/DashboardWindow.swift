import XCTest

/// Opens the dashboard window the tests drive.
///
/// The app is a menu-bar app with a `WindowGroup`: launched fresh with no saved
/// window state it comes up as a status item and an empty File menu entry, with
/// no window for a test to query. Every UI test therefore has to ask for the
/// window rather than assume the launch produced one.
enum DashboardWindow {
    static func open(in app: XCUIApplication) {
        if app.windows.firstMatch.waitForExistence(timeout: 3) { return }
        // The WindowGroup's own "New <title> Window" command. Matched by prefix
        // because the middle of the title is the localized scene name.
        let fileMenu = app.menuBars.menuBarItems["File"].firstMatch
        guard fileMenu.waitForExistence(timeout: 10) else { return }
        fileMenu.click()
        let newWindow = app.menuItems
            .matching(NSPredicate(format: "title BEGINSWITH %@", "New")).firstMatch
        if newWindow.waitForExistence(timeout: 5) {
            newWindow.click()
        }
        _ = app.windows.firstMatch.waitForExistence(timeout: 15)
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
    static func place(_ window: XCUIElement, atSize spec: String, test: XCTestCase) {
        let parts = spec.split(separator: "x").compactMap { Double($0) }
        guard parts.count == 2 else { return XCTFail("bad window size: \(spec)") }
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

    static func move(_ window: XCUIElement, toOrigin origin: CGPoint) {
        let frame = window.frame
        let delta = CGVector(dx: origin.x - frame.minX, dy: origin.y - frame.minY)
        guard abs(delta.dx) > 1 || abs(delta.dy) > 1 else { return }
        // Grab the title bar, not the toolbar controls in it.
        let grip = window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0))
            .withOffset(CGVector(dx: 0, dy: 12))
        grip.press(forDuration: 0.4, thenDragTo: grip.withOffset(delta))
        Thread.sleep(forTimeInterval: 0.6)
    }

    static func resize(_ window: XCUIElement, to target: CGSize) {
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
