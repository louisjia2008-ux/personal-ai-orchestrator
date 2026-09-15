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
                Label(DailyDriverL10n.scheduling, systemImage: modeSymbol(schedulingMode))
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
                            Text(DailyDriverL10n.stoppingSupervisedAuto)
                        }
                        .frame(maxWidth: .infinity)
                    } else {
                        Label(DailyDriverL10n.stopSupervisedAuto, systemImage: "stop.fill")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(isStoppingSupervisedAuto)
                .help(DailyDriverL10n.stopSupervisedAutoHelp)
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
                Text(DailyDriverL10n.currentWork)
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
                Label(DailyDriverL10n.nothingRunning, systemImage: "checkmark.circle")
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
                    Text(DailyDriverL10n.aiCapacity)
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
            do {
                // The process-wide coordinator serializes this emergency fallback
                // with Home/Settings mode writes. It re-reads routing policy at
                // execution time before setting MANUAL.
                let updated = try await AutomationModeMutationCoordinator.shared.setMode(
                    socketPath: socketPath,
                    mode: "MANUAL"
                )
                await store.loadSchedulingSettings()
                await store.refreshNow()
                stopNotice = updated.mode == "MANUAL"
                    ? DailyDriverL10n.stoppedSupervisedAuto
                    : DailyDriverL10n.daemonReturnedMode(modeLabel(updated.mode))
            } catch let error as PAOClientError {
                stopNotice = DailyDriverL10n.emergencyStopFailed(error.displayDetail)
            } catch {
                stopNotice = DailyDriverL10n.emergencyStopMalformed
            }
            isStoppingSupervisedAuto = false
        }
    }

    private func quotaSummary(_ summary: QuotaSummaryView) -> String {
        if summary.quotaExhaustedCount > 0 {
            return DailyDriverL10n.menuQuotaExhausted(
                summary.quotaExhaustedCount,
                summary.quotaWarningCount
            )
        }
        if summary.quotaWarningCount > 0 {
            return DailyDriverL10n.menuQuotaWarnings(
                summary.quotaWarningCount,
                summary.quotaObservableProviderCount,
                summary.connectedProviderCount
            )
        }
        if summary.quotaUnknownProviderCount > 0 {
            return DailyDriverL10n.menuQuotaUnknown(
                summary.quotaUnknownProviderCount,
                summary.quotaObservableProviderCount,
                summary.connectedProviderCount
            )
        }
        return DailyDriverL10n.menuQuotaObserved(
            summary.quotaObservableProviderCount,
            summary.connectedProviderCount
        )
    }

    private func modeLabel(_ value: String?) -> String {
        switch value {
        case "MANUAL": return DailyDriverL10n.manual
        case "SUPERVISED_AUTO": return DailyDriverL10n.supervisedAuto
        case "ACTIVE": return DailyDriverL10n.fullAutomation
        case .none: return DailyDriverL10n.unknown
        default: return value?.replacingOccurrences(of: "_", with: " ").capitalized ?? DailyDriverL10n.unknown
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
        case "AUTO_PLANNED": return DailyDriverL10n.taskAwaitingApproval
        case "AUTO_GRACE": return DailyDriverL10n.taskGraceWindow
        case "RUNNING": return DailyDriverL10n.taskRunning
        case "VERIFYING": return DailyDriverL10n.taskVerifying
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
