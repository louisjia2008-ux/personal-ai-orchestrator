import Foundation

/// What a "resource" is, and how the three backend surfaces that describe one
/// are joined without being merged.
///
/// The daemon answers three different questions about the same provider, on
/// three different endpoints:
///
/// - `/v1/providers` — discovery. What was found in the catalog: accounts,
///   execution targets, quota pools.
/// - `/v1/providers/connections` — the registry. What the owner explicitly
///   connected, with auth and credential provenance.
/// - `/v1/quota` — the quota projection. What each connected surface currently
///   reports, plan-first.
///
/// A provider can appear in any subset of the three, and the differences are
/// meaningful: discovered-but-not-connected is a real state, and so is
/// connected-with-no-quota-evidence. Resources joins them on `provider_id` and
/// keeps every piece labelled with where it came from, so the owner reads one
/// resource without the client pretending the three agree.

// MARK: - Resource identity

/// What kind of thing this row is, from the owner's point of view.
///
/// Not a status: a resource is `connected` or `available` because of what the
/// owner did, and `discovered` when the catalog found something the connection
/// registry has never heard of. That last case is the one worth keeping — it is
/// how a provider that discovery sees but the registry does not stays visible
/// instead of silently vanishing between two endpoints.
public enum ResourceKind: String, Equatable, Sendable, CaseIterable {
    /// In the connection registry. The owner connected it.
    case connected
    /// Offered for connection. Present in the catalog, not connected.
    case available
    /// Discovery found it; the connection registry does not list it either way.
    case discovered

    public var title: String { L10n.resourceKind(rawValue) }
}

/// One quota binding: a single window of a single pool of a single provider.
///
/// The binding — not the provider — is the unit quota is actually measured in.
/// A provider can own several, they can be in different states, and they can
/// reset at different times. There is deliberately no aggregate percentage
/// across bindings: averaging a 5-hour window at 12% with a monthly window at
/// 90% produces a number that describes no window that exists, and summing them
/// produces one that cannot exist at all.
public struct QuotaBindingSnapshot: Equatable, Identifiable, Sendable {
    public let series: QuotaSeriesIdentity
    /// Owner-facing name of the pool, when the daemon supplied one.
    public let poolName: String?
    /// The daemon's own window classification (`5h`, `monthly`, ...), verbatim.
    public let windowKind: String
    /// AVAILABLE | LIMITED | EXHAUSTED | UNKNOWN, verbatim.
    public let state: String
    /// EXACT | ESTIMATED | UNKNOWN, verbatim.
    public let confidence: String
    /// Present only when the provider actually reported a figure.
    public let remainingFraction: Double?
    public let remainingUnits: Double?
    public let totalUnits: Double?
    public let unit: String?
    public let resetAt: String?
    /// When this binding was read, when the daemon dated the reading.
    public let observedAt: String?
    /// Whether the daemon named this the window that currently limits the plan.
    public let isLimiting: Bool

    public var id: String { series.key }

    /// A meter may only be filled from a figure the provider actually reported.
    public var isReadable: Bool {
        confidence != "UNKNOWN" && remainingFraction != nil
    }

    public var status: StatusPresentation { StatusStyle.quotaState(state) }

    public init(
        series: QuotaSeriesIdentity,
        poolName: String? = nil,
        windowKind: String,
        state: String,
        confidence: String,
        remainingFraction: Double? = nil,
        remainingUnits: Double? = nil,
        totalUnits: Double? = nil,
        unit: String? = nil,
        resetAt: String? = nil,
        observedAt: String? = nil,
        isLimiting: Bool = false
    ) {
        self.series = series
        self.poolName = poolName
        self.windowKind = windowKind
        self.state = state
        self.confidence = confidence
        self.remainingFraction = remainingFraction
        self.remainingUnits = remainingUnits
        self.totalUnits = totalUnits
        self.unit = unit
        self.resetAt = resetAt
        self.observedAt = observedAt
        self.isLimiting = isLimiting
    }
}

// MARK: - Resource snapshot

/// Everything the client knows about one provider, joined but not merged.
public struct ResourceSnapshot: Equatable, Identifiable, Sendable {
    public let providerId: String
    public let displayName: String
    public let kind: ResourceKind

    /// Discovery's view, when discovery found it.
    public let health: ProviderHealthView?
    /// The registry's view, when the owner connected it.
    public let connection: ProviderConnectionView?
    /// The catalog's offer, when it is not connected.
    public let available: AvailableProviderView?
    /// The quota projection, when a quota card exists for it.
    public let quota: QuotaProviderCardView?

    public var id: String { providerId }

    // MARK: Availability

    /// CONNECTED | NOT_CONNECTED | ERROR | ..., verbatim from whichever surface
    /// carries it. The registry outranks discovery: connection state is what the
    /// registry is authoritative for.
    public var connectionState: String {
        connection?.connectionState
            ?? quota?.connectionState
            ?? available?.connectionState
            ?? health?.connectionState
            ?? "UNKNOWN"
    }

    public var authState: String? {
        connection?.authState ?? quota?.authState ?? available?.authState ?? health?.authState
    }

    public var planSurface: String? {
        connection?.planSurface ?? quota?.planSurface ?? available?.planSurface
            ?? health?.planSurface
    }

    public var region: String? {
        connection?.region ?? quota?.region ?? available?.region ?? health?.region
    }

    public var connectionStatus: StatusPresentation {
        StatusStyle.connection(connectionState)
    }

    // MARK: Execution targets

    /// Execution targets come from discovery only. The registry lists model
    /// SKUs, which are a catalog fact, not a runnable target — they are not
    /// promoted into targets here.
    public var executionTargets: [ExecutionTargetHealthView] {
        health?.executionTargets ?? []
    }

    public var verifiedExecutionTargetCount: Int {
        executionTargets.filter { $0.enabled && $0.isExecutionVerified }.count
    }

    /// Catalog model identifiers this provider exposes. Membership, not
    /// capacity: a listed model is not a runnable target and carries no quota.
    public var modelSkus: [String] {
        connection?.modelSkus ?? available?.modelSkus ?? []
    }

    // MARK: Quota

    /// The plan-first projection, when one exists.
    public var plan: QuotaPlanView? { quota?.plan }

    /// OBSERVED | UNKNOWN, verbatim. A resource with no quota card at all is
    /// UNKNOWN rather than absent: not being asked is not the same as being
    /// answered, but neither is it healthy.
    public var quotaState: String { quota?.quotaState ?? "UNKNOWN" }

    public var isQuotaObserved: Bool { quota?.isObserved ?? false }

    /// Freshness of the quota reading, or of the attempt when no reading
    /// succeeded. Never invented: nil means the daemon dated nothing.
    public var quotaObservedAt: String? { quota?.observedAt }
    public var quotaLastRefreshAt: String? { quota?.lastRefreshAt }

    /// Every quota binding this resource owns, from the plan first and the
    /// health pools second.
    ///
    /// The two sources are kept apart rather than reconciled. The plan pool and
    /// the discovery pools can carry the same `pool_id`, or the discovery pool
    /// can be identified by the provider id because the collector reported none
    /// — the client cannot tell those apart, so it never merges one into the
    /// other and never counts a window twice.
    public var quotaBindings: [QuotaBindingSnapshot] {
        var bindings: [QuotaBindingSnapshot] = []
        var seen: Set<String> = []

        if let plan {
            let limitingId = plan.bindingWindow?.windowId
            for window in plan.windows {
                let series = QuotaSeriesIdentity(
                    providerId: plan.providerId,
                    quotaPoolId: plan.poolId,
                    windowId: window.windowId
                )
                guard seen.insert(series.key).inserted else { continue }
                bindings.append(
                    QuotaBindingSnapshot(
                        series: series,
                        poolName: plan.displayName,
                        windowKind: window.windowKind,
                        state: window.state,
                        confidence: window.confidence,
                        remainingFraction: window.remainingFraction,
                        remainingUnits: window.remainingUnits,
                        totalUnits: window.totalUnits,
                        unit: window.unit,
                        resetAt: window.resetAt,
                        observedAt: plan.observedAt,
                        isLimiting: window.windowId == limitingId
                    )
                )
            }
        }

        // Health pools from the quota card first, then from discovery. The card
        // is the fresher of the two when both are present.
        for pool in (quota?.quotaPools ?? []) + (health?.quotaPools ?? []) {
            for window in pool.windows {
                let series = QuotaSeriesIdentity(
                    providerId: providerId,
                    quotaPoolId: pool.quotaPoolId,
                    windowId: window.windowId
                )
                guard seen.insert(series.key).inserted else { continue }
                bindings.append(
                    QuotaBindingSnapshot(
                        series: series,
                        poolName: pool.name,
                        windowKind: window.windowKind,
                        state: window.state,
                        confidence: window.confidence,
                        remainingFraction: window.remainingFraction,
                        resetAt: window.resetAt,
                        observedAt: pool.observedAt,
                        isLimiting: false
                    )
                )
            }
        }
        return bindings
    }

    /// The single binding the collection row may summarise, or nil.
    ///
    /// Only the window the daemon itself named as limiting qualifies. When no
    /// window is declared limiting, one readable binding may still stand for the
    /// resource — but two may not, because choosing between them would be the
    /// client inventing a limiting-window policy the daemon owns.
    public var summaryBinding: QuotaBindingSnapshot? {
        let bindings = quotaBindings
        if let declared = bindings.first(where: { $0.isLimiting && $0.isReadable }) {
            return declared
        }
        let readable = bindings.filter(\.isReadable)
        return readable.count == 1 ? readable[0] : nil
    }

    /// How many bindings carry a real figure. Drives the "several bindings" row
    /// state, which is what the row shows instead of a fabricated average.
    public var readableBindingCount: Int {
        quotaBindings.filter(\.isReadable).count
    }

    public init(
        providerId: String,
        displayName: String,
        kind: ResourceKind,
        health: ProviderHealthView? = nil,
        connection: ProviderConnectionView? = nil,
        available: AvailableProviderView? = nil,
        quota: QuotaProviderCardView? = nil
    ) {
        self.providerId = providerId
        self.displayName = displayName
        self.kind = kind
        self.health = health
        self.connection = connection
        self.available = available
        self.quota = quota
    }
}

// MARK: - Collection state

/// What the resource collection column should render.
///
/// Loading, disconnected and genuinely-empty are three different facts leading
/// to three different next actions, exactly as in the task workspace.
public enum ResourceCollectionState: Equatable, Sendable {
    /// Connected, nothing has arrived yet.
    case loading
    /// Not connected. The collection is unknown, not empty.
    case disconnected(reason: ConnectionState.DisconnectionReason)
    /// The daemon knows of no provider at all.
    case empty
    /// Resources exist; the search matched none of them.
    case noSearchMatch
    case populated([ResourceSnapshot])

    public var resources: [ResourceSnapshot] {
        if case .populated(let resources) = self { return resources }
        return []
    }

    public var isPopulated: Bool {
        if case .populated = self { return true }
        return false
    }
}

public enum ResourceCollection {

    /// Joins the three provider surfaces into one ordered collection.
    ///
    /// The union of provider ids is taken, never the intersection: a provider
    /// that only discovery knows about, or only the quota card knows about,
    /// stays visible under its own identity. Dropping it would make the answer
    /// to "what do I have" depend on which endpoint happened to answer.
    public static func snapshots(
        providers: ProviderHealthListView?,
        connections: ProviderConnectionListView?,
        quota: QuotaOverviewView?
    ) -> [ResourceSnapshot] {
        let health = index(providers?.providers ?? [], by: \.providerId)
        let connected = index(connections?.connected ?? [], by: \.providerId)
        let available = index(connections?.availableToAdd ?? [], by: \.providerId)
        let cards = index(quota?.providers ?? [], by: \.providerId)

        var ids: [String] = []
        var seen: Set<String> = []
        // Connected first, then offered, then discovery-only: the order the
        // owner cares about, before the alphabetical sort within each kind.
        for id in connected.keys.sorted() where seen.insert(id).inserted { ids.append(id) }
        for id in available.keys.sorted() where seen.insert(id).inserted { ids.append(id) }
        for id in cards.keys.sorted() where seen.insert(id).inserted { ids.append(id) }
        for id in health.keys.sorted() where seen.insert(id).inserted { ids.append(id) }

        return ids.map { id in
            let connection = connected[id]
            let offer = available[id]
            let kind: ResourceKind =
                connection != nil ? .connected : (offer != nil ? .available : .discovered)
            // The registry names it as the owner connected it; discovery's name
            // is the fallback, and the id itself is the last resort. A resource
            // is never displayed nameless.
            let name =
                connection?.displayName
                ?? offer?.displayName
                ?? cards[id]?.displayName
                ?? health[id]?.displayName
                ?? id
            return ResourceSnapshot(
                providerId: id,
                displayName: name,
                kind: kind,
                health: health[id],
                connection: connection,
                available: offer,
                quota: cards[id]
            )
        }
    }

    /// Resolves store state into what the collection column shows.
    public static func resolve(
        providers: ProviderHealthListView?,
        connections: ProviderConnectionListView?,
        quota: QuotaOverviewView?,
        connection: ConnectionState,
        query: String = ""
    ) -> ResourceCollectionState {
        guard connection.isConnected else {
            if case .disconnected(let reason) = connection {
                return .disconnected(reason: reason)
            }
            return .disconnected(reason: .daemonNotRunning)
        }
        // Nothing has answered yet. An empty state here would claim the daemon
        // holds no providers before it has been asked.
        guard providers != nil || connections != nil || quota != nil else { return .loading }

        let all = snapshots(providers: providers, connections: connections, quota: quota)
        guard !all.isEmpty else { return .empty }

        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return .populated(all) }
        let matched = all.filter { matches($0, query: needle) }
        return matched.isEmpty ? .noSearchMatch : .populated(matched)
    }

    /// Search over identity and the machine values the owner might paste in.
    static func matches(_ resource: ResourceSnapshot, query: String) -> Bool {
        let haystacks =
            [
                resource.displayName, resource.providerId, resource.planSurface,
                resource.region, resource.connectionState,
            ] + resource.modelSkus.map(Optional.some)
        return haystacks.contains { field in
            guard let field, !field.isEmpty else { return false }
            return field.localizedCaseInsensitiveContains(query)
        }
    }

    private static func index<T>(
        _ items: [T], by key: KeyPath<T, String>
    ) -> [String: T] {
        Dictionary(items.map { ($0[keyPath: key], $0) }, uniquingKeysWith: { first, _ in first })
    }
}
