import SwiftUI

import PAOControlKit

// Shared chrome for the Daily Driver redesign. Pages keep native toolbar titles;
// this layer owns only the content geometry and quiet grouped surfaces below it.

struct DashboardPageScaffold<Content: View>: View {
    @ViewBuilder var content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(Color(nsColor: .windowBackgroundColor))
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("page.content")
    }
}

struct DashboardPageContainer<Content: View>: View {
    @ViewBuilder var content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        DashboardPageScaffold {
            ScrollView {
                VStack(alignment: .leading, spacing: DashboardLayoutMetrics.sectionSpacing) {
                    content
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DashboardLayoutMetrics.pageHorizontalPadding)
                .padding(.vertical, DashboardLayoutMetrics.pageVerticalPadding)
            }
        }
    }
}

/// Product-level section heading. It must be stronger than metadata inside a
/// surface; the previous secondary/subheadline treatment made every page read
/// as one undifferentiated block of diagnostics.
struct DashboardSectionHeader: View {
    let title: String
    var symbol: String?
    var detail: String?

    init(_ title: String, symbol: String? = nil, detail: String? = nil) {
        self.title = title
        self.symbol = symbol
        self.detail = detail
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.tight) {
            if let symbol {
                Image(systemName: symbol)
                    .imageScale(.medium)
                    .foregroundStyle(.secondary)
            }
            Text(title)
                .font(.headline)
                .foregroundStyle(.primary)
            if let detail {
                Spacer(minLength: Spacing.inner)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityAddTraits(.isHeader)
    }
}

/// A quiet grouped surface, not a decorative card. Borders are deliberately
/// subtle so hierarchy comes from content and spacing rather than a wall of
/// rectangles. Independent action/status modules may still use this component;
/// ordinary rows should prefer dividers inside one surface.
struct DashboardCard<Content: View>: View {
    let title: String?
    let symbol: String?
    var titleLineLimit: Int? = nil
    @ViewBuilder var content: Content

    init(
        title: String? = nil,
        symbol: String? = nil,
        titleLineLimit: Int? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.symbol = symbol
        self.titleLineLimit = titleLineLimit
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.element) {
            if let title {
                HStack(spacing: Spacing.tight) {
                    if let symbol {
                        Image(systemName: symbol)
                            .imageScale(.small)
                            .foregroundStyle(.secondary)
                    }
                    Text(title)
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(titleLineLimit)
                        .minimumScaleFactor(titleLineLimit == nil ? 1.0 : 0.8)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(DashboardLayoutMetrics.cardPadding)
        .background(
            Color(nsColor: .controlBackgroundColor).opacity(0.72),
            in: RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius, style: .continuous)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.55), lineWidth: 0.5)
        }
    }
}

struct EmptyStateView: View {
    let title: String
    let symbol: String
    let message: String
    var hint: String? = nil
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    init(
        title: String,
        symbol: String,
        message: String,
        hint: String? = nil,
        actionTitle: String? = nil,
        action: (() -> Void)? = nil
    ) {
        self.title = title
        self.symbol = symbol
        self.message = message
        self.hint = hint
        self.actionTitle = actionTitle
        self.action = action
    }

    var body: some View {
        VStack(spacing: Spacing.element) {
            Image(systemName: symbol)
                .font(.system(size: 38, weight: .light))
                .foregroundStyle(.tertiary)
            Text(title)
                .font(.title3.weight(.semibold))
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
            }
            if let action, let actionTitle {
                Button(actionTitle, action: action)
                    .controlSize(.regular)
                    .padding(.top, Spacing.tight)
            }
        }
        .frame(maxWidth: 380)
        .padding(Spacing.section)
    }
}

/// Human-readable chronological events. The machine event type remains visible
/// as secondary audit metadata instead of competing with the event summary.
struct EventList: View {
    let events: [ActivityEventView]
    var groupsByDay: Bool = false

    var body: some View {
        if events.isEmpty {
            Label(L10n.noEvents, systemImage: "tray")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else if groupsByDay {
            VStack(alignment: .leading, spacing: DashboardLayoutMetrics.sectionSpacing) {
                ForEach(Timestamps.groupedByDay(events, id: \.createdAt)) { group in
                    VStack(alignment: .leading, spacing: Spacing.inner) {
                        DashboardSectionHeader(group.title)
                        rows(group.items)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            rows(events)
        }
    }

    private func rows(_ rows: [ActivityEventView]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(rows) { event in
                eventRow(event)
                if event.id != rows.last?.id { Divider() }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func eventRow(_ event: ActivityEventView) -> some View {
        HStack(alignment: .top, spacing: Spacing.element) {
            Circle()
                .fill(Color.secondary.opacity(0.45))
                .frame(width: 6, height: 6)
                .padding(.top, 7)
            VStack(alignment: .leading, spacing: 3) {
                Text(event.summary)
                    .font(.callout)
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: Spacing.inner) {
                    Text(stamp(event))
                        .font(.caption.monospacedDigit())
                    Text(event.eventType)
                        .font(.system(.caption2, design: .monospaced))
                }
                .foregroundStyle(.tertiary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(stamp(event)), \(event.summary), \(event.eventType)")
        .accessibilityIdentifier("dashboard.eventRow")
    }

    private func stamp(_ event: ActivityEventView) -> String {
        groupsByDay ? Timestamps.timeOfDay(event.createdAt) : Timestamps.friendly(event.createdAt)
    }
}
