import AppKit
import SwiftUI

import PAOControlKit

struct MenuBarContentView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()
            taskCountsSection
            recentTasksSection
            Divider()
            QuickSubmitView()
            Divider()
            providerSection
            Divider()
            activeStatusSection
            footer
        }
        .padding(12)
        .onAppear { store.menuVisible = true }
        .onDisappear { store.menuVisible = false }
    }

    private var header: some View {
        HStack {
            Image(systemName: store.statusSummary.systemImage)
            Text(L10n.statusTitle(store.statusSummary))
                .font(.headline)
            Spacer()
            if store.connection.isConnected {
                Text(L10n.connectedLabel)
                    .foregroundStyle(.secondary)
                    .font(.caption)
            } else {
                Text(disconnectionText)
                    .foregroundStyle(.red)
                    .font(.caption)
            }
        }
    }

    private var disconnectionText: String {
        if case .disconnected(let reason) = store.connection {
            return L10n.disconnectionReason(reason)
        }
        return ""
    }

    private var taskCountsSection: some View {
        let counts = store.taskCounts()
        return HStack(spacing: 16) {
            Label("\(counts.running)", systemImage: "gearshape.2")
            Label("\(counts.ready)", systemImage: "tray")
            Label("\(counts.blocked)", systemImage: "exclamationmark.octagon")
            Label("\(counts.verified)", systemImage: "checkmark.seal")
        }
        .font(.callout)
        .help(L10n.taskCountsHelp)
    }

    private var recentTasksSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L10n.recentTasks).font(.subheadline).foregroundStyle(.secondary)
            if let tasks = store.tasks?.tasks, !tasks.isEmpty {
                ForEach(tasks.prefix(10)) { task in
                    HStack {
                        Text(task.taskId)
                            .font(.system(.caption, design: .monospaced))
                            .lineLimit(1)
                        Spacer()
                        Text(task.state)
                            .font(.caption)
                            .foregroundStyle(StatusStyle.task(state: task.state).color)
                        Button(L10n.cancel) {
                            Task { await store.cancel(taskId: task.taskId) }
                        }
                        .controlSize(.mini)
                        .disabled(task.state == "CANCELLED" || task.state == "COMPLETED"
                                  || task.state == "FAILED")
                        .help(L10n.cancelHelp)
                    }
                }
            } else {
                Text(L10n.noTasks).font(.caption).foregroundStyle(.secondary)
            }
            if let notice = store.cancellationNotice {
                Text(L10n.cancelNotice(notice)).font(.caption2).foregroundStyle(.orange)
            }
        }
    }

    private var providerSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L10n.providersQuota).font(.subheadline).foregroundStyle(.secondary)
            if let providers = store.providers?.providers, !providers.isEmpty {
                ForEach(providers) { provider in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(provider.displayName) (\(L10n.accountsCount(provider.accountCount)))")
                            .font(.callout)
                        ForEach(provider.quotaPools) { pool in
                            Text(
                                "  \(pool.name): \(pool.state.lowercased())"
                                    + " · \(L10n.quotaConfidenceLabel) \(QuotaRendering.confidenceBadge(pool.confidence))"
                                    + " · \(L10n.quotaSourceLabel) \(pool.measurementSourceType)"
                            )
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            ForEach(pool.windows, id: \.windowId) { window in
                                let remaining = L10n.quotaRemaining(
                                    fraction: window.remainingFraction,
                                    confidence: window.confidence
                                )
                                Text("    \(window.windowId): \(remaining)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        ForEach(provider.executionTargets) { target in
                            let observed = target.observedAvailability.map { "\($0.state)" } ?? "NONE"
                            Text("  \(L10n.providerTargetLabel) \(target.executionTargetId): \(observed)")
                                .font(.caption2)
                                .foregroundStyle(
                                    (target.observedAvailability?.state == "EXHAUSTED_OBSERVED"
                                      || target.observedAvailability?.state == "COOLDOWN")
                                        ? StatusTone.caution.color : StatusTone.neutral.color
                                )
                        }
                    }
                }
            } else if store.connection.isConnected {
                Text(L10n.noProviders).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var activeStatusSection: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(L10n.productionActive).font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                Text(store.activeStatus?.productionActive ?? "UNKNOWN")
                    .font(.callout.bold())
                    .foregroundStyle((store.activeStatus?.authorized == true ? StatusTone.positive : StatusTone.neutral).color)
            }
            if let blockers = store.activeStatus?.blockingReasons {
                ForEach(blockers, id: \.self) { reason in
                    Text("· \(reason)").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Text(store.socketPath)
                .font(.system(.caption2, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
                .foregroundStyle(.tertiary)
            Spacer()
            Button(L10n.openDashboard) {
                NSApp.activate(ignoringOtherApps: true)
                openWindow(id: "dashboard")
            }
            .controlSize(.mini)
            Button(L10n.refresh) {
                Task { await store.refreshNow() }
            }
            .controlSize(.mini)
            Button(L10n.quit) {
                NSApplication.shared.terminate(nil)
            }
            .controlSize(.mini)
        }
    }
}
