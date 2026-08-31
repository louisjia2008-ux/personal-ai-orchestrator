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
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store: OrchestratorStore

    init() {
        let home = ProcessInfo.processInfo.environment["HOME"].flatMap { value in
            value.isEmpty ? nil : value
        } ?? NSHomeDirectory()
        let layout = AppSupportLayout.resolve(homeDirectory: home)
        let path = SocketDiscovery.resolve(defaultLayout: layout)
        _store = StateObject(
            wrappedValue: OrchestratorStore(
                socketPath: path,
                daemonConfiguration: DaemonLaunchConfiguration(layout: layout),
                widgetSnapshotBridge: .appOwned(layout: layout)
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
        .defaultSize(width: 1100, height: 720)

        MenuBarExtra {
            MenuBarContentView()
                .environmentObject(store)
                .frame(width: 340)
        } label: {
            Image(systemName: store.statusSummary.systemImage)
        }
        .menuBarExtraStyle(.window)

        Settings {
            ClientSettingsDashboard()
                .environmentObject(store)
                .frame(width: 520)
        }
    }
}
