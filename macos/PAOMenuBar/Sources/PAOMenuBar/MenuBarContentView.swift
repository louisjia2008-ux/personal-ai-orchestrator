import AppKit
import SwiftUI

import PAOControlKit

struct MenuBarContentView: View {
    @EnvironmentObject private var store: OrchestratorStore

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
            Text(store.statusSummary.title)
                .font(.headline)
            Spacer()
            if store.connection.isConnected {
                Text("connected")
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
            return reasonDisplay(reason)
        }
        return ""
    }

    private func reasonDisplay(_ reason: ConnectionState.DisconnectionReason) -> String {
        switch reason {
        case .daemonNotRunning: return "daemon not running"
        case .socketInvalid: return "socket invalid"
        case .socketPathTooLong(let length): return "socket path too long (\(length) bytes)"
        case .accessDenied: return "permission denied"
        case .apiVersionMismatch(let version): return "API version mismatch (\(version))"
        case .malformedResponse: return "malformed response"
        case .transportFailure: return "transport failure"
        }
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
        .help("Running / Ready+Submitted / Blocked / Verified+Completed")
    }

    private var recentTasksSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Recent tasks").font(.subheadline).foregroundStyle(.secondary)
            if let tasks = store.tasks?.tasks, !tasks.isEmpty {
                ForEach(tasks.prefix(10)) { task in
                    HStack {
                        Text(task.taskId)
                            .font(.system(.caption, design: .monospaced))
                            .lineLimit(1)
                        Spacer()
                        Text(task.state)
                            .font(.caption)
                            .foregroundStyle(task.state == "BLOCKED" ? Color.red : Color.secondary)
                        Button("Cancel") {
                            Task { await store.cancel(taskId: task.taskId) }
                        }
                        .controlSize(.mini)
                        .disabled(task.state == "CANCELLED" || task.state == "COMPLETED"
                                  || task.state == "FAILED")
                        .help("Cancel via POST /v1/tasks/{id}/cancel; RUNNING tasks fail closed (409).")
                    }
                }
            } else {
                Text("No tasks").font(.caption).foregroundStyle(.secondary)
            }
            if let notice = store.cancellationNotice {
                Text(notice).font(.caption2).foregroundStyle(.orange)
            }
        }
    }

    private var providerSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Providers / quota").font(.subheadline).foregroundStyle(.secondary)
            if let providers = store.providers?.providers, !providers.isEmpty {
                ForEach(providers) { provider in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(provider.displayName) (\(provider.accountCount) account\(provider.accountCount == 1 ? "" : "s"))")
                            .font(.callout)
                        ForEach(provider.quotaPools) { pool in
                            Text(
                                "  \(pool.name): \(pool.state.lowercased())"
                                    + " · confidence \(QuotaRendering.confidenceBadge(pool.confidence))"
                                    + " · source \(pool.measurementSourceType)"
                            )
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            ForEach(pool.windows, id: \.windowId) { window in
                                let remaining = QuotaRendering.remainingText(
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
                            Text("  target \(target.executionTargetId): \(observed)")
                                .font(.caption2)
                                .foregroundStyle(
                                    (target.observedAvailability?.state == "EXHAUSTED_OBSERVED"
                                     || target.observedAvailability?.state == "COOLDOWN")
                                        ? Color.orange : Color.secondary
                                )
                        }
                    }
                }
            } else if store.connection.isConnected {
                Text("No providers registered").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var activeStatusSection: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("Production ACTIVE").font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                Text(store.activeStatus?.productionActive ?? "UNKNOWN")
                    .font(.callout.bold())
                    .foregroundStyle(store.activeStatus?.authorized == true ? Color.green : Color.secondary)
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
            Button("Refresh") {
                Task { await store.refreshNow() }
            }
            .controlSize(.mini)
            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
            .controlSize(.mini)
        }
    }
}
