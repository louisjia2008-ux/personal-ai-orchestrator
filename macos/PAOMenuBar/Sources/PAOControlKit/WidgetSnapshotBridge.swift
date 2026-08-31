import Foundation

public struct WidgetSnapshot: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let generatedAt: String
    public let connectionState: String
    public let daemonLifecycle: String
    public let productionActive: String
    public let counts: WidgetTaskCounts
    public let providers: [WidgetProviderSnapshot]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case connectionState = "connection_state"
        case daemonLifecycle = "daemon_lifecycle"
        case productionActive = "production_active"
        case counts
        case providers
    }

    public init(
        generatedAt: Date,
        connection: ConnectionState,
        daemonLifecycle: DaemonLifecycleStatus,
        dashboard: DashboardSummaryView?
    ) {
        self.schemaVersion = 1
        self.generatedAt = ISO8601DateFormatter().string(from: generatedAt)
        self.connectionState = connection.isConnected ? "CONNECTED" : "DISCONNECTED"
        self.daemonLifecycle = daemonLifecycle.displayValue
        self.productionActive = dashboard?.activeStatus.productionActive ?? "UNKNOWN"
        self.counts = WidgetTaskCounts(dashboard?.counts)
        self.providers = (dashboard?.providers.providers ?? []).map(WidgetProviderSnapshot.init(provider:))
    }

    public func isStale(referenceDate: Date = Date(), maxAgeSeconds: TimeInterval = 300) -> Bool {
        guard let generated = ISO8601DateFormatter().date(from: generatedAt) else {
            return true
        }
        return referenceDate.timeIntervalSince(generated) > maxAgeSeconds
    }
}

public struct WidgetTaskCounts: Codable, Equatable, Sendable {
    public let running: Int
    public let ready: Int
    public let blocked: Int
    public let verified: Int
    public let completed: Int
    public let total: Int

    public init(_ counts: DashboardCountsView?) {
        self.running = counts?.running ?? 0
        self.ready = counts?.ready ?? 0
        self.blocked = counts?.blocked ?? 0
        self.verified = counts?.verified ?? 0
        self.completed = counts?.completed ?? 0
        self.total = counts?.total ?? 0
    }
}

public struct WidgetProviderSnapshot: Codable, Equatable, Sendable {
    public let providerId: String
    public let displayName: String
    public let quotaState: String
    public let quotaConfidence: String
    public let targetCount: Int

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case displayName = "display_name"
        case quotaState = "quota_state"
        case quotaConfidence = "quota_confidence"
        case targetCount = "target_count"
    }

    public init(provider: ProviderHealthView) {
        self.providerId = provider.providerId
        self.displayName = provider.displayName
        self.quotaState = provider.quotaPools.first?.state ?? "UNKNOWN"
        self.quotaConfidence = provider.quotaPools.first?.confidence ?? "UNKNOWN"
        self.targetCount = provider.executionTargets.count
    }
}

public struct WidgetSnapshotBridge: Sendable {
    public let snapshotURL: URL

    public init(snapshotURL: URL) {
        self.snapshotURL = snapshotURL
    }

    public static func appOwned(layout: AppSupportLayout = .resolve()) -> WidgetSnapshotBridge {
        WidgetSnapshotBridge(
            snapshotURL: URL(fileURLWithPath: layout.appSupportRoot)
                .appendingPathComponent("WidgetSnapshot", isDirectory: true)
                .appendingPathComponent("snapshot.json")
        )
    }

    public static func sharedContainer(appGroupIdentifier: String) -> WidgetSnapshotBridge? {
        guard let container = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: appGroupIdentifier
        ) else {
            return nil
        }
        return WidgetSnapshotBridge(
            snapshotURL: container
                .appendingPathComponent("WidgetSnapshot", isDirectory: true)
                .appendingPathComponent("snapshot.json")
        )
    }

    public func write(_ snapshot: WidgetSnapshot) throws {
        try FileManager.default.createDirectory(
            at: snapshotURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(snapshot)
        try data.write(to: snapshotURL, options: [.atomic])
    }

    public func load() throws -> WidgetSnapshot {
        let data = try Data(contentsOf: snapshotURL)
        return try JSONDecoder().decode(WidgetSnapshot.self, from: data)
    }
}
