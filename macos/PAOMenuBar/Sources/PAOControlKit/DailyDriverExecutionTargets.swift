import Foundation

/// Shared client-side projection for targets the owner may actively choose.
///
/// Provider discovery and provider connection are deliberately separate daemon
/// surfaces. A discovered target is not owner-dispatchable until its provider is
/// routing-connected. Pi is the one intentional exception to the connection
/// registry: its local runtime manager treats a Pi provider as routing-connected
/// when Pi auth is READY and the model is present; `runtimeAvailable == true`
/// is the sanitized client projection of those Pi runtime prerequisites.
public enum DailyDriverExecutionTargets {
    public static func launchable(
        providers: ProviderHealthListView?,
        connections: ProviderConnectionListView?
    ) -> [ExecutionTargetHealthView] {
        let explicitlyConnected = Set(
            (connections?.connected ?? []).map(\.providerId)
        )

        return (providers?.providers ?? [])
            .flatMap { provider in
                provider.executionTargets.filter { target in
                    let routingConnected = explicitlyConnected.contains(provider.providerId)
                        || (target.runtimeId == "pi" && target.runtimeAvailable == true)
                    return routingConnected && target.isLaunchableOnHost
                }
            }
            .sorted {
                if $0.runtimeId != $1.runtimeId {
                    return $0.runtimeId < $1.runtimeId
                }
                return $0.modelSkuId.localizedStandardCompare($1.modelSkuId) == .orderedAscending
            }
    }
}
