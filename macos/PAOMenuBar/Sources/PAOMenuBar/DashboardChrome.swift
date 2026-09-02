import SwiftUI

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
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
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
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

/// Internal browser / list panel used inside a page container when a
/// section needs a master/detail layout. Visually subordinate to the
/// primary navigation sidebar: rounded content material, subtle
/// separator, no navigation chrome.
struct DashboardBrowserPanel<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title)
                .font(.headline)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 12)
                .padding(.top, 10)
                .padding(.bottom, 6)
            Divider()
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color(nsColor: .separatorColor).opacity(0.6), lineWidth: 1)
        )
    }
}

/// Detail slot inside a DashboardPageContainer. Flexible width; pairs
/// with `DashboardBrowserPanel` to form a master/detail layout that
/// never visually competes with the primary navigation sidebar.
struct DashboardDetailPanel<Content: View>: View {
    @ViewBuilder var content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            content
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(.horizontal, 4)
    }
}