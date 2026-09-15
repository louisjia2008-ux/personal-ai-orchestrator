import SwiftUI

import PAOControlKit

/// Operational history and audit. Human-readable summaries stay primary; raw
/// event types remain secondary metadata inside EventList.
struct ActivityDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer {
            EventList(events: store.dashboard?.recentEvents ?? [], groupsByDay: true)
        }
    }
}
