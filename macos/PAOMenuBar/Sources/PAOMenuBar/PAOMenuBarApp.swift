import SwiftUI

import PAOControlKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct PAOMenuBarApp: App {
    static let widgetAppGroup = "group.com.personal-ai-orchestrator.dashboard"

    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store: OrchestratorStore

    init() {
        let home = ProcessInfo.processInfo.environment["HOME"].flatMap { value in
            value.isEmpty ? nil : value
        } ?? NSHomeDirectory()
        let layout = AppSupportLayout.resolve(homeDirectory: home)
        let path = SocketDiscovery.resolve(defaultLayout: layout)
        let widgetBridge = WidgetSnapshotBridge.sharedContainer(
            appGroupIdentifier: Self.widgetAppGroup
        ) ?? .appOwned(layout: layout)
        let autoStartDaemon = UserDefaults.standard.object(forKey: "pao.autoStartDaemon") as? Bool ?? true
        _store = StateObject(
            wrappedValue: OrchestratorStore(
                socketPath: path,
                daemonConfiguration: DaemonLaunchConfiguration(layout: layout),
                widgetSnapshotBridge: widgetBridge,
                autoStartDaemon: autoStartDaemon
            )
        )
        // Acceptance evidence: which localization the bundle machinery resolved
        // for this launch (follows macOS preferred languages automatically).
        ClientLog.operation("startup", outcome: "ui_language=\(L10n.resolvedLanguageCode)")
    }

    var body: some Scene {
        WindowGroup(L10n.dashboardTitle, id: "dashboard") {
            DashboardView()
                .environmentObject(store)
        }
        .defaultSize(width: 1180, height: 740)

        MenuBarExtra {
            // MenuBarContentView owns the canonical popover width. A second,
            // smaller frame here previously forced the 380pt daily-driver
            // content into a 340pt parent and could clip labels/actions.
            MenuBarContentView()
                .environmentObject(store)
        } label: {
            Image(systemName: store.statusSummary.systemImage)
        }
        .menuBarExtraStyle(.window)

        Settings {
            DailyDriverSettingsRoot()
                .environmentObject(store)
        }
    }
}
