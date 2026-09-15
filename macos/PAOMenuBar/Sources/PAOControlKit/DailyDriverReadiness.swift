import Foundation

/// Fail-closed launchability projection shared by Daily Driver surfaces.
///
/// The daemon's launch boundary requires the target to be enabled, currently
/// execution-verified, and backed by an available runtime. A demote-fallback
/// (`executionVerifiedStale == true`) is historical evidence only: the latest
/// execution observation was non-VERIFIED, so the client must not promote that
/// target into a runnable picker/section until fresh verification restores it.
public extension ExecutionTargetHealthView {
    var isLaunchableOnHost: Bool {
        enabled
            && isExecutionVerified
            && !isExecutionVerifiedStale
            && runtimeAvailable == true
    }
}

/// Whether the Daily Driver can accept and execute new work right now.
///
/// This deliberately does **not** use historical BLOCKED/FAILED tasks or the
/// dashboard's Production ACTIVE evidence blockers. Those belong in Needs
/// Attention / Settings, but neither makes MANUAL or SUPERVISED_AUTO unusable.
/// Readiness is derived only from prerequisites for new work: daemon connection,
/// known scheduling mode, an online project, project opt-in when supervised,
/// and at least one verified/runtime-available target that is not explicitly
/// exhausted or cooling down.
public enum DailyDriverReadinessBlocker: Equatable, Sendable {
    case disconnected
    case schedulingModeUnknown
    case noOnlineProject
    case supervisedAutoNeedsProject
    case noRunnableTarget
    case noAvailableCapacity
}

public struct DailyDriverReadinessSnapshot: Equatable, Sendable {
    public let isReady: Bool
    public let blocker: DailyDriverReadinessBlocker?

    public init(isReady: Bool, blocker: DailyDriverReadinessBlocker?) {
        self.isReady = isReady
        self.blocker = blocker
    }
}

public enum DailyDriverReadiness {
    public static func derive(
        connection: ConnectionState,
        schedulingMode: String?,
        projects: [ProjectView],
        providers: ProviderHealthListView?
    ) -> DailyDriverReadinessSnapshot {
        guard connection.isConnected else {
            return blocked(.disconnected)
        }

        guard let schedulingMode,
              ["MANUAL", "SUPERVISED_AUTO", "ACTIVE"].contains(schedulingMode)
        else {
            return blocked(.schedulingModeUnknown)
        }

        let onlineProjects = projects.filter(\.isOnline)
        guard !onlineProjects.isEmpty else {
            return blocked(.noOnlineProject)
        }

        if schedulingMode == "SUPERVISED_AUTO",
           !onlineProjects.contains(where: \.supervisedAutoAllowed) {
            return blocked(.supervisedAutoNeedsProject)
        }

        let launchableTargets = (providers?.providers ?? [])
            .flatMap(\.executionTargets)
            .filter(\.isLaunchableOnHost)

        guard !launchableTargets.isEmpty else {
            return blocked(.noRunnableTarget)
        }

        let notExplicitlyUnavailable = launchableTargets.filter { target in
            guard let state = target.observedAvailability?.state else {
                // Missing availability is uncertainty, not evidence of exhaustion.
                // Capacity surfaces still render that uncertainty explicitly.
                return true
            }
            return state != "EXHAUSTED_OBSERVED" && state != "COOLDOWN"
        }

        guard !notExplicitlyUnavailable.isEmpty else {
            return blocked(.noAvailableCapacity)
        }

        return DailyDriverReadinessSnapshot(isReady: true, blocker: nil)
    }

    private static func blocked(
        _ blocker: DailyDriverReadinessBlocker
    ) -> DailyDriverReadinessSnapshot {
        DailyDriverReadinessSnapshot(isReady: false, blocker: blocker)
    }
}

/// Dashboard risks that are relevant to day-to-day operation.
///
/// The daemon currently includes two Production ACTIVE evidence reminders in
/// the generic risk array. They must remain visible in Settings, but surfacing
/// them as Home blockers would make a deliberately pre-ACTIVE Daily Driver look
/// broken forever. Unknown future risks stay visible by default.
public enum DailyDriverRiskPresentation {
    public static func isProductionActivationOnly(_ risk: RiskItemView) -> Bool {
        guard let raw = risk.rawCode?.lowercased() else { return false }
        return raw.contains("owner approval") || raw.contains("shadow evidence")
    }

    public static func actionable(_ risks: [RiskItemView]) -> [RiskItemView] {
        risks.filter { !isProductionActivationOnly($0) }
    }
}
