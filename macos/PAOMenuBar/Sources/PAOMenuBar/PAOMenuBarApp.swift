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
        let arguments = ProcessInfo.processInfo.arguments
        let isolatedHome = arguments.firstIndex(of: "--pao-home").flatMap { index in
            arguments.indices.contains(index + 1) ? arguments[index + 1] : nil
        }
        let home = isolatedHome ?? ProcessInfo.processInfo.environment["HOME"].flatMap { value in
            value.isEmpty ? nil : value
        } ?? NSHomeDirectory()
        let layout = AppSupportLayout.resolve(homeDirectory: home)
        let path = isolatedHome == nil ? SocketDiscovery.resolve(defaultLayout: layout) : layout.socketPath
        let widgetBridge = (isolatedHome == nil ? WidgetSnapshotBridge.sharedContainer(
            appGroupIdentifier: Self.widgetAppGroup
        ) : nil) ?? .appOwned(layout: layout)
        let autoStartDaemon = isolatedHome != nil
            || (UserDefaults.standard.object(forKey: "pao.autoStartDaemon") as? Bool ?? true)
        _store = StateObject(
            wrappedValue: OrchestratorStore(
                socketPath: path,
                daemonConfiguration: DaemonLaunchConfiguration(
                    layout: layout, port: isolatedHome == nil ? 8765 : 0,
                    homeDirectory: isolatedHome
                ),
                widgetSnapshotBridge: widgetBridge,
                autoStartDaemon: autoStartDaemon,
                initialSchedulingPolicy: "MANUAL"
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
