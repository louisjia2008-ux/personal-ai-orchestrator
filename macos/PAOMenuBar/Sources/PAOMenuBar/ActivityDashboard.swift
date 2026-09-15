import SwiftUI

import PAOControlKit

/// Operational history and audit. Human-readable summaries stay primary; raw
/// event types remain secondary metadata inside EventList.
///
/// Category and search filtering are presentation-only. Unknown daemon event
/// types always remain visible in All and are never coerced into a made-up
/// semantic bucket.
struct ActivityDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var category: ActivityCategory = .all
    @State private var query = ""

    private var allEvents: [ActivityEventView] {
        store.dashboard?.recentEvents ?? []
    }

    private var filteredEvents: [ActivityEventView] {
        allEvents.filter { event in
            ActivityPresentation.matches(
                eventType: event.eventType,
                taskId: event.taskId,
                category: category
            )
            && ActivityPresentation.matchesQuery(
                summary: event.summary,
                eventType: event.eventType,
                taskId: event.taskId,
                query: query
            )
        }
    }

    var body: some View {
        DashboardPageContainer {
            controls

            if filteredEvents.isEmpty {
                EmptyStateView(
                    title: L10n.noEvents,
                    symbol: "clock.badge.questionmark",
                    message: L10n.noEvents
                )
                .frame(maxWidth: .infinity)
            } else {
                EventList(events: filteredEvents, groupsByDay: true)
            }
        }
        .searchable(text: $query, placement: .toolbar, prompt: L10n.search)
    }

    private var controls: some View {
        HStack(spacing: Spacing.inner) {
            Menu {
                ForEach(ActivityCategory.allCases) { candidate in
                    Button {
                        category = candidate
                    } label: {
                        if candidate == category {
                            Label(categoryTitle(candidate), systemImage: "checkmark")
                        } else {
                            Text(categoryTitle(candidate))
                        }
                    }
                }
            } label: {
                Label(categoryTitle(category), systemImage: categorySymbol(category))
            }
            .menuStyle(.borderlessButton)
            .fixedSize()

            Spacer(minLength: 0)

            Text("\(filteredEvents.count) / \(allEvents.count)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.tertiary)
        }
    }

    private func categoryTitle(_ category: ActivityCategory) -> String {
        switch category {
        case .all:
            return L10n.resourcesFilterAll
        case .task:
            return DashboardSection.tasks.title
        case .routing:
            return L10n.routingTitle
        case .quota:
            return L10n.quotaTitle
        case .safety:
            return DailyDriverL10n.needsAttention
        }
    }

    private func categorySymbol(_ category: ActivityCategory) -> String {
        switch category {
        case .all: return "line.3.horizontal.decrease.circle"
        case .task: return "checklist"
        case .routing: return "arrow.triangle.branch"
        case .quota: return "gauge.with.dots.needle.67percent"
        case .safety: return "shield.lefthalf.filled"
        }
    }
}
