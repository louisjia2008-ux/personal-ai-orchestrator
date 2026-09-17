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
/// known scheduling mode, explicit owner-execution permission, an online project,
/// project opt-in when supervised, and at least one routing-connected,
/// execution-verified/runtime-available target whose quota truth is compatible
/// with the selected automation mode.
public enum DailyDriverReadinessBlocker: Equatable, Sendable {
    case disconnected
    case schedulingModeUnknown
    case ownerExecutionDisabled
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
    /// Keep this set in lock-step with the daemon's current
    /// `_AUTO_OK_QUOTA_STATES`. SUPERVISED_AUTO planning is intentionally
    /// fail-closed: missing/UNKNOWN quota truth means the host tick will skip
    /// the task before any planning side effect.
    ///
    /// `RECOVERED_OBSERVED` is deliberately not listed here yet because the
    /// daemon currently omits it too; the recovery-semantic mismatch is tracked
    /// as a backend issue rather than papered over in presentation code.
    private static let supervisedAutoQuotaReadyStates: Set<String> = [
        "AVAILABLE_OBSERVED",
        "AVAILABLE_UNMETERED",
    ]

    /// MANUAL owner dispatch may collect quota at dispatch time, so absence of an
    /// observation is uncertainty rather than an automatic UI blocker. Explicit
    /// host evidence that the target is exhausted, cooling down, or uncertainty-
    /// locked must still prevent Home from claiming useful capacity.
    private static let manualQuotaBlockedStates: Set<String> = [
        "EXHAUSTED_OBSERVED",
        "COOLDOWN",
        "UNCERTAIN_LOCKED",
    ]

    public static func derive(
        connection: ConnectionState,
        schedulingMode: String?,
        ownerExecutionEnabled: Bool?,
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

        // Owner-Initiated Execution is a separate persisted safety gate and is
        // OFF on a fresh/corrupt install. Both manual dispatch and the current
        // supervised-auto implementation require it, so Home must not claim
        // "Ready to work" while the daemon would reject every dispatch. `nil`
        // means the setting has not been read yet and therefore fails closed.
        guard ownerExecutionEnabled == true else {
            return blocked(.ownerExecutionDisabled)
        }

        let onlineProjects = projects.filter(\.isOnline)
        guard !onlineProjects.isEmpty else {
            return blocked(.noOnlineProject)
        }

        if schedulingMode == "SUPERVISED_AUTO",
           !onlineProjects.contains(where: \.supervisedAutoAllowed) {
            return blocked(.supervisedAutoNeedsProject)
        }

        // Use the same owner-selection projection as New Task / Quick Submit /
        // Task Detail. `/v1/providers` carries connection state too, so passing
        // no separate connection projection here remains truthful during refresh
        // ordering while still excluding discovered-but-disconnected OpenCode.
        let launchableTargets = DailyDriverExecutionTargets.launchable(
            providers: providers,
            connections: nil
        )

        guard !launchableTargets.isEmpty else {
            return blocked(.noRunnableTarget)
        }

        if schedulingMode == "SUPERVISED_AUTO" {
            // Match the daemon's pre-side-effect AUTO quota gate. In particular,
            // nil/UNKNOWN/RECOVERY_PROBE_DUE/UNCERTAIN_LOCKED/COOLDOWN are not
            // enough for Home to advertise that unattended planning can proceed.
            let autoQuotaReady = launchableTargets.contains { target in
                guard let state = target.observedAvailability?.state else {
                    return false
                }
                return supervisedAutoQuotaReadyStates.contains(state)
            }
            guard autoQuotaReady else {
                return blocked(.noAvailableCapacity)
            }
        } else {
            // MANUAL remains usable when quota is merely not observed yet because
            // the dispatch boundary can perform its own read-only refresh. But an
            // explicit blocked availability state is authoritative immediately.
            let manuallyUsable = launchableTargets.contains { target in
                guard let state = target.observedAvailability?.state else {
                    return true
                }
                return !manualQuotaBlockedStates.contains(state)
            }
            guard manuallyUsable else {
                return blocked(.noAvailableCapacity)
            }
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
