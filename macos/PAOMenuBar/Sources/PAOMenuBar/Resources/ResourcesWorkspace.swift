import SwiftUI

import PAOControlKit

/// The Resources destination: what this machine can run work on, and how much of
/// it is left.
///
/// B2 reached the same content through a segmented control over Providers,
/// Execution targets and Quota — three backend modules the owner had to
/// understand before they could answer one question about one provider. The
/// three were never independent: a provider's quota, its targets and its
/// connection state are facts about the same thing, and splitting them across
/// tabs meant reading a resource required visiting three surfaces and joining
/// them by memory.
///
/// The shape is now selection → detail, matching the task workspace: the
/// collection lists every resource once, and the detail says everything about
/// the selected one. That also fixes what the tabs made impossible — comparing
/// two providers, which is now scanning one column.
///
/// The pieces the tabs owned are all still here. Discovery status and provider
/// import moved into the collection column's own surfaces rather than being
/// deleted, because both are about the collection rather than about any one
/// resource.
struct ResourcesWorkspace: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var query: String
    @Binding var kindFilter: ResourceKind?
    @Binding var selectedResourceId: String?
    /// Ticks so reset countdowns and observation ages advance rather than
    /// freezing at whatever they read when the view was built.
    let now: Date

    private var allResources: [ResourceSnapshot] {
        ResourceCollection.snapshots(
            providers: store.providers,
            connections: store.providerConnections,
            quota: store.quota
        )
    }

    private var collection: ResourceCollectionState {
        let resolved = ResourceCollection.resolve(
            providers: store.providers,
            connections: store.providerConnections,
            quota: store.quota,
            connection: store.connection,
            query: query
        )
        // The kind filter narrows a resolved collection rather than being part
        // of resolution: "your filter hides everything" and "the daemon has no
        // providers" are different facts, and only the first is fixed by
        // clearing the filter.
        guard let kindFilter, case .populated(let resources) = resolved else {
            return resolved
        }
        let narrowed = resources.filter { $0.kind == kindFilter }
        return narrowed.isEmpty ? .noSearchMatch : .populated(narrowed)
    }

    private var selectedResource: ResourceSnapshot? {
        guard let selectedResourceId else { return nil }
        return allResources.first { $0.providerId == selectedResourceId }
    }

    private var displayNames: [String: String] {
        Dictionary(
            allResources.map { ($0.providerId, $0.displayName) },
            uniquingKeysWith: { first, _ in first }
        )
    }

    var body: some View {
        DashboardPageScaffold {
            // A fixed collection beside a flexible detail, not a negotiated
            // split. `HSplitView` resolved its children from their content:
            // Tasks, whose rows carry long intents, pushed the whole split
            // wider than the window and the sidebar was clipped to absorb it,
            // while Resources — whose rows truncate — was left alone. Same
            // page grammar, two different geometries.
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    ResourceCollectionView(
                        state: collection,
                        totalCount: allResources.count,
                        query: $query,
                        kindFilter: $kindFilter,
                        selectedResourceId: $selectedResourceId,
                        now: now
                    )
                    ResourceCollectionFooter()
                }
                .frame(width: DashboardLayoutMetrics.collectionPreferredWidth)

                Divider()

                ResourceDetailSurface(
                    resource: selectedResource,
                    history: store.quota?.history,
                    displayNames: displayNames,
                    isRefreshingQuota: store.isRefreshingQuota,
                    onRefreshQuota: { providerId in
                        Task { await store.refreshQuota(providerId: providerId) }
                    },
                    onConnect: { providerId in
                        Task { await store.connectProvider(providerId: providerId) }
                    },
                    now: now
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        // Window-level chrome — the search field and the toolbar live in the
        // title bar — is attached outside the content region, not inside it.
        // Attached inside, the region reported itself as spanning the whole
        // window rather than the pane it occupies.
        .searchable(text: $query, prompt: L10n.resourcesSearchPrompt)
        .toolbar { toolbarContent }
        .onAppear(perform: selectFirstIfNeeded)
        .onChange(of: allResources.map(\.providerId)) { _ in selectFirstIfNeeded() }
    }

    /// Selects something on arrival so the detail pane is not empty by default,
    /// and re-selects when the current selection stops existing — a disconnected
    /// provider must not leave the surface pointing at nothing.
    private func selectFirstIfNeeded() {
        let ids = allResources.map(\.providerId)
        if let selectedResourceId, ids.contains(selectedResourceId) { return }
        selectedResourceId = ids.first
    }

    /// Both refresh operations, named for what they do.
    ///
    /// Kept separate deliberately: discovery re-runs the catalog and credential
    /// scan and reads no quota, while the quota refresh contacts each connected
    /// provider's documented read-only endpoint. Merging them into one
    /// "Refresh" would hide which of two different daemon operations ran, and
    /// they do not cost the same.
    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem {
            Button {
                Task { await store.refreshProviders() }
            } label: {
                if store.isRefreshingProviders {
                    ProgressView().controlSize(.small)
                } else {
                    Label(L10n.resourceRefreshProviders, systemImage: "arrow.triangle.2.circlepath")
                }
            }
            .disabled(store.isRefreshingProviders)
            .help(L10n.resourceRefreshProvidersHelp)
        }
        ToolbarItem {
            Button {
                Task { await store.refreshQuota() }
            } label: {
                if store.isRefreshingQuota {
                    ProgressView().controlSize(.small)
                } else {
                    Label(L10n.resourceRefreshQuota, systemImage: "arrow.clockwise")
                }
            }
            .disabled(store.isRefreshingQuota || (store.quota?.providers ?? []).isEmpty)
            .help(L10n.resourceRefreshQuotaHelp)
        }
    }
}

/// Collection-wide facts that belong to no single resource: whether the list
/// reflects a live discovery cycle, and what is offered for import.
struct ResourceCollectionFooter: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let candidates = store.providerConnections?.importCandidates,
                !candidates.isEmpty
            {
                Divider()
                ResourceImportCandidates(candidates: candidates)
            }
            if let status = store.providerDiscoveryStatus {
                Divider()
                ResourceDiscoveryStatus(status: status)
            }
        }
    }
}

/// Whether the collection reflects a live discovery cycle or the registry
/// persisted from a previous launch.
struct ResourceDiscoveryStatus: View {
    let status: ProviderDiscoveryStatusView

    private var presentation: StatusPresentation {
        switch status.discoveryState {
        case "DISCOVERED": return StatusPresentation(tone: .positive, symbol: "checkmark.circle")
        case "PENDING": return StatusPresentation(tone: .caution, symbol: "clock")
        case "EMPTY": return StatusPresentation(tone: .neutral, symbol: "tray")
        case "FAILED":
            return StatusPresentation(tone: .critical, symbol: "exclamationmark.triangle")
        // A state this build has not seen is unknown, never healthy.
        default: return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            ResourceStatusLabel(
                presentation: presentation, text: status.discoveryState, font: .caption
            )
            if let lastDiscoveredAt = status.lastDiscoveredAt {
                Text(L10n.providerLastDiscovered(Timestamps.friendly(lastDiscoveredAt)))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            if let errorCode = status.lastErrorCode {
                Text(L10n.discoveryError(errorCode))
                    .font(.caption2)
                    .foregroundStyle(StatusTone.critical.color)
            }
        }
        .padding(.horizontal, Spacing.element)
        .padding(.vertical, Spacing.inner)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Surfaces the owner plausibly already uses, offered for import.
///
/// Selection starts empty and is explicit: reconciliation proposes, the owner
/// decides. Unchanged in substance from B2 — only its home moved, from a
/// Providers tab to the collection it is about.
struct ResourceImportCandidates: View {
    @EnvironmentObject private var store: OrchestratorStore
    let candidates: [ProviderImportCandidateView]
    @State private var selected: Set<String> = []
    @State private var importing = false
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                ForEach(candidates) { candidate in
                    VStack(alignment: .leading, spacing: 2) {
                        Toggle(isOn: binding(for: candidate.providerId)) {
                            Text(candidate.displayName).font(.callout)
                        }
                        // Structured discovery evidence, rendered verbatim.
                        ForEach(candidate.evidence) { item in
                            Label(item.detail, systemImage: "checkmark.circle")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Text(L10n.providersImportFooter)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Button {
                    importing = true
                    let ids = Array(selected)
                    Task {
                        await store.importProviderConnections(providerIds: ids)
                        selected = []
                        importing = false
                    }
                } label: {
                    if importing {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(L10n.providersImportExisting, systemImage: "square.and.arrow.down")
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(selected.isEmpty || importing)
            }
            .padding(.top, Spacing.tight)
        } label: {
            Label(L10n.providersImportTitle, systemImage: "square.and.arrow.down")
                .font(.caption.weight(.medium))
        }
        .padding(.horizontal, Spacing.element)
        .padding(.vertical, Spacing.inner)
    }

    private func binding(for providerId: String) -> Binding<Bool> {
        Binding(
            get: { selected.contains(providerId) },
            set: { isOn in
                if isOn {
                    selected.insert(providerId)
                } else {
                    selected.remove(providerId)
                }
            }
        )
    }
}
