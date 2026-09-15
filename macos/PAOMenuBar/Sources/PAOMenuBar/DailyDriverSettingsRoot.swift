import SwiftUI

import PAOControlKit

/// Standard macOS Settings entry point. These are the same three panes used by
/// the Dashboard Settings destination, so policy never diverges by entry point.
struct DailyDriverSettingsRoot: View {
    var body: some View {
        TabView {
            GeneralSettingsPane()
                .tabItem {
                    Label(L10n.settingsTabGeneral, systemImage: "gearshape")
                }

            AutomationSettingsPane()
                .tabItem {
                    Label(DailyDriverL10n.scheduling, systemImage: "sparkles")
                }

            ProjectsSettingsPane()
                .tabItem {
                    Label(L10n.sectionProjects, systemImage: "folder.badge.gearshape")
                }
        }
        .frame(minWidth: 680, idealWidth: 720, minHeight: 520, idealHeight: 600)
    }
}
