import SwiftUI

import PAOControlKit

/// Shared chrome for every primary Dashboard section.
///
/// Wraps a section's content in a uniform container:
/// - identical background material
/// - identical outer padding
/// - identical inter-card spacing
/// - optional leading section header
/// - optional trailing toolbar slot
///
/// All primary sections must render through this container so the
/// primary app sidebar and the content canvas feel like one shell
/// rather than a collection of separate apps.
struct DashboardPageContainer<Content: View>: View {
    let title: String
    let symbol: String?
    @ViewBuilder var content: Content
    @ViewBuilder var trailing: () -> AnyView

    init(
        title: String,
        symbol: String? = nil,
        @ViewBuilder trailing: @escaping () -> AnyView = { AnyView(EmptyView()) },
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.symbol = symbol
        self.content = content()
        self.trailing = trailing
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.section) {
                PageHeader(title: title, symbol: symbol, trailing: trailing)
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, Spacing.page)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

/// Title, optional symbol, and page-level actions for a primary section.
///
/// Extracted from `DashboardPageContainer` because the container was doing two
/// jobs — scrolling canvas and page header — which meant every section that
/// wanted one had to accept the other. Nine sections render this today, and B2
/// needs the header without the scroll container.
struct PageHeader: View {
    let title: String
    var symbol: String?
    var trailing: () -> AnyView = { AnyView(EmptyView()) }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.inner) {
            if let symbol {
                Image(systemName: symbol)
                    .foregroundStyle(.secondary)
            }
            Text(title)
                .font(.title2.weight(.semibold))
            Spacer()
            trailing()
        }
        .padding(.bottom, 2)
    }
}

/// Placeholder for a surface with nothing to show.
///
/// The action is a title plus a closure rather than a bare closure: every empty
/// state used to render the same "New Task" button regardless of what it was
/// empty of, so the quota page offered to create a task when what it needed was
/// a provider. A caller that supplies an action now has to say what it does.
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
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(.secondary)
            Text(title).font(.title3.weight(.semibold))
            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let action, let actionTitle {
                Button(actionTitle, action: action)
                    .controlSize(.regular)
                    .padding(.top, Spacing.tight)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(30)
    }
}

/// Chronological daemon events. Summaries are the daemon's own sentences and
/// are rendered verbatim.
struct EventList: View {
    let events: [ActivityEventView]

    var body: some View {
        if events.isEmpty {
            Label(L10n.noEvents, systemImage: "tray")
                .foregroundStyle(.secondary)
        } else {
            ForEach(events) { event in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(event.eventType).font(.system(.caption, design: .monospaced))
                        Spacer()
                        Text(Timestamps.friendly(event.createdAt))
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                    Text(event.summary).foregroundStyle(.secondary)
                }
                Divider()
            }
        }
    }
}
