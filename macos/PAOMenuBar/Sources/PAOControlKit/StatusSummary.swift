import Foundation

/// Deterministic top-level status summary derived ONLY from daemon-reported API evidence.
///
/// Precedence (documented, stable): disconnected > blocked > quotaLimited > working > healthy.
/// The client never infers provider truth beyond what the sanitized API returns.
public enum StatusSummary: Equatable, Sendable {
    case disconnected(ConnectionState.DisconnectionReason)
    case blocked
    case quotaLimited
    case working
    case healthy

    public static func derive(
        connection: ConnectionState,
        tasks: [TaskView],
        providers: ProviderHealthListView?
    ) -> StatusSummary {
        guard connection.isConnected else {
            if case .disconnected(let reason) = connection {
                return .disconnected(reason)
            }
            return .disconnected(.transportFailure)
        }
        if tasks.contains(where: { $0.state == "BLOCKED" }) {
            return .blocked
        }
        if let providers, providers.containsExhaustedOrCooldown {
            return .quotaLimited
        }
        if tasks.contains(where: { $0.state == "RUNNING" }) {
            return .working
        }
        return .healthy
    }

    public var title: String {
        switch self {
        case .disconnected: return "Disconnected"
        case .blocked: return "Blocked"
        case .quotaLimited: return "Quota limited"
        case .working: return "Working"
        case .healthy: return "Healthy"
        }
    }

    public var systemImage: String {
        switch self {
        case .disconnected: return "antenna.radiowaves.left.and.right.slash"
        case .blocked: return "exclamationmark.octagon"
        case .quotaLimited: return "battery.25"
        case .working: return "gearshape.2"
        case .healthy: return "checkmark.circle"
        }
    }
}

extension ProviderHealthListView {
    /// Quota limitation is only claimed from explicit observed availability evidence.
    public var containsExhaustedOrCooldown: Bool {
        providers.contains { provider in
            provider.executionTargets.contains { target in
                guard let observed = target.observedAvailability else { return false }
                return observed.state == "EXHAUSTED_OBSERVED" || observed.state == "COOLDOWN"
            }
        }
    }
}

/// Rendering rules that preserve quota-confidence semantics.
public enum QuotaRendering {
    /// ESTIMATED/UNKNOWN never render as a precise percentage. Only EXACT may.
    public static func remainingText(fraction: Double?, confidence: String) -> String {
        guard let fraction else { return "unknown" }
        if confidence == "EXACT" {
            return String(format: "%.1f%%", fraction * 100)
        }
        return "unknown (confidence: \(confidence))"
    }

    public static func confidenceBadge(_ confidence: String) -> String {
        switch confidence {
        case "EXACT", "ESTIMATED", "UNKNOWN": return confidence
        default: return "UNKNOWN"
        }
    }
}
