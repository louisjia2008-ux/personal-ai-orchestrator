import SwiftUI

import PAOControlKit

@main
struct PAOMenuBarApp: App {
    @StateObject private var store: OrchestratorStore

    init() {
        let path = SocketDiscovery.resolve()
        _store = StateObject(wrappedValue: OrchestratorStore(socketPath: path))
        // Acceptance evidence: which localization the bundle machinery resolved
        // for this launch (follows macOS preferred languages automatically).
        ClientLog.operation("startup", outcome: "ui_language=\(L10n.resolvedLanguageCode)")
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarContentView()
                .environmentObject(store)
                .frame(width: 340)
        } label: {
            Image(systemName: store.statusSummary.systemImage)
        }
        .menuBarExtraStyle(.window)
    }
}
