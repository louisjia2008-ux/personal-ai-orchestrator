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
}
