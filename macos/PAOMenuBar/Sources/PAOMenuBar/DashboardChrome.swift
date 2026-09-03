import SwiftUI

import PAOControlKit

// Shared chrome for every primary Dashboard section.
//
// The page title is the window's toolbar title — the native macOS page chrome —
// and no destination draws a second one inside its content. Earlier revisions
// drew a title band under the toolbar, which repeated the same word twice, 52pt
// apart, on all five destinations, and cost every page a band of vertical space
// before its first line of content.
//
// What remains shared is the geometry below the toolbar:
// - `DashboardPageScaffold` — the content region itself (background, insets
//   contract, measurable identity), used directly by the pages that manage
//   their own layout (Tasks, Resources)
// - `DashboardPageContainer` — the scaffold plus a scrolling document body with
//   the standard page insets and section rhythm (Overview, Activity, Settings)

/// The content region under the toolbar.
///
/// Identified as one accessibility container so the layout contract — every
/// destination filling the same region — is measurable rather than asserted.
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

/// The document-style container: a scrolling body with the standard page insets
/// and section rhythm.
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

/// A group heading inside a page.
///
/// One weight, one colour, one baseline, on every page that groups content —
/// so "Basic information" on Overview and "Today" on Activity are visibly the
/// same rank rather than two designers' idea of a heading.
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
                    .imageScale(.small)
                    .foregroundStyle(.secondary)
            }
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
            if let detail {
                Spacer(minLength: Spacing.inner)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityAddTraits(.isHeader)
    }
}

/// A panel: one bordered surface holding one group of related facts.
///
/// Quiet on purpose. The previous card drew a `.regularMaterial` slab, and a
/// page of them read as a board of grey rectangles rather than as a document.
/// This one is the window's own control surface plus a hairline, which is what
/// macOS itself uses to separate a panel from the page behind it.
struct DashboardCard<Content: View>: View {
    let title: String?
    let symbol: String?
    /// KPI tiles pin this to 1 so a longer title can never grow one card in a row.
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
        VStack(alignment: .leading, spacing: Spacing.inner) {
            if let title {
                Label {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.secondary)
                } icon: {
                    if let symbol {
                        Image(systemName: symbol)
                            .imageScale(.small)
                            .foregroundStyle(.secondary)
                    }
                }
                .lineLimit(titleLineLimit)
                .minimumScaleFactor(titleLineLimit == nil ? 1.0 : 0.7)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(DashboardLayoutMetrics.cardPadding)
        .background(
            Color(nsColor: .controlBackgroundColor),
            in: RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius)
        )
        .overlay(
            RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius)
                .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
        )
    }
}

/// Placeholder for a surface with nothing to show.
///
/// Draws its content only; callers decide whether it fills a pane (the
/// collection columns and the Resources detail center it in the whole pane) or
/// sits inside a larger composition (the Tasks no-selection state groups it
/// with the routing readiness card).
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
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.tertiary)
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
        .frame(maxWidth: 380)
        .padding(Spacing.element)
    }
}

/// Chronological daemon events as one aligned table, grouped by day.
///
/// Fixed columns — timestamp, event kind, summary — so every row shares a
/// leading edge and the columns line up however wide the pane is. The timestamp
/// column is wide enough for an absolute date, because a column sized for
/// "5 hours ago" wrapped every older row onto two lines and left the table with
/// no shared row height. Summaries are the daemon's own sentences, verbatim.
struct EventList: View {
    let events: [ActivityEventView]
    /// Day headings separate the groups on the Activity page. Overview's recent
    /// slice is short enough to read without them.
    var groupsByDay: Bool = false

    /// Wide enough for the longest form each column renders: a full date and
    /// time when the list is ungrouped, a clock time when it is not.
    private var timestampWidth: CGFloat { groupsByDay ? 72 : 132 }
    private static let kindWidth: CGFloat = 176

    var body: some View {
        if events.isEmpty {
            Label(L10n.noEvents, systemImage: "tray")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else if groupsByDay {
            VStack(alignment: .leading, spacing: DashboardLayoutMetrics.sectionSpacing) {
                ForEach(Timestamps.groupedByDay(events, id: \.createdAt)) { group in
                    VStack(alignment: .leading, spacing: 0) {
                        DashboardSectionHeader(group.title)
                            .padding(.bottom, Spacing.inner)
                        table(group.items)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            table(events)
        }
    }

    private func table(_ rows: [ActivityEventView]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(rows) { event in
                row(event)
                if event.id != rows.last?.id {
                    Divider()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func row(_ event: ActivityEventView) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.element) {
            Text(stamp(event))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(width: timestampWidth, alignment: .leading)
            Text(event.eventType)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(width: Self.kindWidth, alignment: .leading)
            Text(event.summary)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, Spacing.inner - 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(stamp(event)), \(event.eventType), \(event.summary)")
        .accessibilityIdentifier("dashboard.eventRow")
    }

    /// Under a day heading the date is already stated, so the row carries the
    /// clock time only.
    private func stamp(_ event: ActivityEventView) -> String {
        groupsByDay
            ? Timestamps.timeOfDay(event.createdAt)
            : Timestamps.friendly(event.createdAt)
    }
}
