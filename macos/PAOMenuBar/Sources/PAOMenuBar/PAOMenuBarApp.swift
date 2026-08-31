import SwiftUI

import PAOControlKit

@main
struct PAOMenuBarApp: App {
    @StateObject private var store: OrchestratorStore

    init() {
        let path = SocketDiscovery.resolve()
        _store = StateObject(wrappedValue: OrchestratorStore(socketPath: path))
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
