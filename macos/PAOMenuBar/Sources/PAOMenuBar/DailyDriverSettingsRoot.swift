import SwiftUI

import PAOControlKit

/// Native macOS Settings entry point for the Daily Driver.
///
/// The Dashboard still owns its five product destinations; this scene makes the
/// same owner policy reachable through the standard Settings command without
/// duplicating any backend authority or maintaining a second settings store.
struct DailyDriverSettingsRoot: View {
    var body: some View {
        TabView {
            ClientSettingsDashboard()
                .tabItem {
                    Label(L10n.settingsTabGeneral, systemImage: "gearshape")
                }

            AutomationSettingsPane()
                .tabItem {
                    Label(DailyDriverL10n.scheduling, systemImage: "sparkles")
                }

            ProjectAutomationSettingsView(showsDoneButton: false)
                .tabItem {
                    Label(DailyDriverL10n.projectAutomationTitle, systemImage: "folder.badge.gearshape")
                }
        }
        .frame(minWidth: 680, idealWidth: 720, minHeight: 520, idealHeight: 600)
    }
}
