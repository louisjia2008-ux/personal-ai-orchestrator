import AppKit
import SwiftUI

import PAOControlKit

struct MenuBarContentView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.openWindow) private var openWindow
    @State private var isStoppingSupervisedAuto = false
    @State private var stopNotice: String?

    private var liveTasks: [TaskView] {
        (store.tasks?.tasks ?? [])
            .filter { ["AUTO_PLANNED", "AUTO_GRACE", "RUNNING", "VERIFYING"].contains($0.state) }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    private var schedulingMode: String? {
        store.schedulingSettings?.mode
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()
            schedulingSection
            currentWorkSection
            quotaSection
            Divider()
            QuickSubmitView()
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 380)
        .onAppear {
            store.menuVisible = true
            Task { await store.loadSchedulingSettings() }
        }
        .onDisappear { store.menuVisible = false }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: store.statusSummary.systemImage)
            VStack(alignment: .leading, spacing: 1) {
                Text(L10n.statusTitle(store.statusSummary))
                    .font(.headline)
                Text(store.connection.isConnected ? L10n.connectedLabel : disconnectionText)
                    .font(.caption)
                    .foregroundStyle(store.connection.isConnected ? .secondary : StatusTone.critical.color)
            }
            Spacer()
            Button {
                Task { await store.refreshNow() }
            } label: {
                if store.isRefreshing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .buttonStyle(.borderless)
            .disabled(store.isRefreshing)
            .help(L10n.refresh)
        }
    }

    private var disconnectionText: String {
        if case .disconnected(let reason) = store.connection {
            return L10n.disconnectionReason(reason)
        }
        return ""
    }

    private var schedulingSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("Scheduling", systemImage: modeSymbol(schedulingMode))
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text(modeLabel(schedulingMode))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            if MenuBarAutoStop.shouldOffer(currentMode: schedulingMode) {
                Button(role: .destructive) {
                    stopSupervisedAuto()
                } label: {
                    if isStoppingSupervisedAuto {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("Stopping Supervised Auto…")
                        }
                        .frame(maxWidth: .infinity)
                    } else {
                        Label("Stop Supervised Auto", systemImage: "stop.fill")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(isStoppingSupervisedAuto)
                .help("Switch the daemon back to Manual while preserving the current routing policy.")
            }

            if let stopNotice {
                Text(stopNotice)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var currentWorkSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Current Work")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if liveTasks.count > 1 {
                    Text("+\(liveTasks.count - 1)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }
            }

            if let task = liveTasks.first {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: stateSymbol(task.state))
                        .foregroundStyle(StatusStyle.task(state: task.state).color)
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.intent)
                            .font(.callout.weight(.medium))
                            .lineLimit(2)
                        Text(stateLabel(task.state))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
            } else {
                Label("Nothing is running", systemImage: "checkmark.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var quotaSection: some View {
        if let summary = store.quota?.summary {
            let hasWarning = summary.quotaWarningCount > 0 || summary.quotaExhaustedCount > 0
                || summary.quotaUnknownProviderCount > 0
            HStack(spacing: 8) {
                Image(systemName: hasWarning ? "gauge.with.dots.needle.33percent" : "gauge.with.dots.needle.67percent")
                    .foregroundStyle(hasWarning ? StatusTone.caution.color : StatusTone.neutral.color)
                VStack(alignment: .leading, spacing: 1) {
                    Text("AI Capacity")
                        .font(.subheadline.weight(.semibold))
                    Text(quotaSummary(summary))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Button(L10n.openDashboard) {
                NSApp.activate(ignoringOtherApps: true)
                openWindow(id: "dashboard")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)

            Spacer()

            Button(L10n.quit) {
                NSApplication.shared.terminate(nil)
            }
            .controlSize(.small)
        }
    }

    private func stopSupervisedAuto() {
        guard schedulingMode == "SUPERVISED_AUTO", !isStoppingSupervisedAuto else { return }
        isStoppingSupervisedAuto = true
        stopNotice = nil
        let socketPath = store.socketPath

        Task {
            let client = PAOControlClient(socketPath: socketPath)
            do {
                // Re-read immediately before the mutation so the emergency stop
                // cannot overwrite a routing-policy change made elsewhere.
                let current = try await client.schedulingSettings()
                let updated = try await client.setSchedulingMode(
                    "MANUAL",
                    defaultSchedulingPolicy: current.defaultSchedulingPolicy
                )
                await store.loadSchedulingSettings()
                await store.refreshNow()
                stopNotice = updated.mode == "MANUAL"
                    ? "Supervised Auto stopped. Manual mode is now authoritative."
                    : "Daemon returned \(modeLabel(updated.mode)); no local mode was assumed."
            } catch let error as PAOClientError {
                stopNotice = "Emergency stop failed · \(error.displayDetail)"
            } catch {
                stopNotice = "Emergency stop response could not be decoded."
            }
            isStoppingSupervisedAuto = false
        }
    }

    private func quotaSummary(_ summary: QuotaSummaryView) -> String {
        if summary.quotaExhaustedCount > 0 {
            return "\(summary.quotaExhaustedCount) exhausted · \(summary.quotaWarningCount) warning"
        }
        if summary.quotaWarningCount > 0 {
            return "\(summary.quotaWarningCount) warning · \(summary.quotaObservableProviderCount)/\(summary.connectedProviderCount) observed"
        }
        if summary.quotaUnknownProviderCount > 0 {
            return "\(summary.quotaUnknownProviderCount) unknown · \(summary.quotaObservableProviderCount)/\(summary.connectedProviderCount) observed"
        }
        return "\(summary.quotaObservableProviderCount)/\(summary.connectedProviderCount) connected plans observed"
    }

    private func modeLabel(_ value: String?) -> String {
        switch value {
        case "MANUAL": return "Manual"
        case "SUPERVISED_AUTO": return "Supervised Auto"
        case "ACTIVE": return "Full Automation"
        case .none: return "Unknown"
        default: return value?.replacingOccurrences(of: "_", with: " ").capitalized ?? "Unknown"
        }
    }

    private func modeSymbol(_ value: String?) -> String {
        switch value {
        case "MANUAL": return "hand.raised"
        case "SUPERVISED_AUTO": return "sparkles"
        case "ACTIVE": return "bolt.shield"
        default: return "questionmark.circle"
        }
    }

    private func stateLabel(_ state: String) -> String {
        switch state {
        case "AUTO_PLANNED": return "Awaiting approval"
        case "AUTO_GRACE": return "Supervised grace"
        case "RUNNING": return "Running"
        case "VERIFYING": return "Verifying"
        default: return state.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func stateSymbol(_ state: String) -> String {
        switch state {
        case "AUTO_PLANNED", "AUTO_GRACE": return "timer"
        case "RUNNING": return "bolt.circle.fill"
        case "VERIFYING": return "checkmark.seal"
        default: return "circle"
        }
    }
}
