import SwiftUI

import PAOControlKit

/// Native settings surface for owner-controlled automation authority and routing.
/// MANUAL/SUPERVISED_AUTO, owner-initiated execution, routing policy, and the
/// read-only Production ACTIVE gate are kept together without conflating them.
struct AutomationSettingsPane: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var isChangingMode = false
    @State private var modeNotice: String?

    private var onlineProjects: [ProjectView] {
        (store.projects?.projects ?? []).filter(\.isOnline)
    }

    private var supervisedProjectCount: Int {
        onlineProjects.filter(\.supervisedAutoAllowed).count
    }

    var body: some View {
        Form {
            Section {
                automationModeControl

                if store.schedulingSettings?.mode == "SUPERVISED_AUTO" {
                    LabeledContent(
                        DailyDriverL10n.projectAutomationTitle,
                        value: DailyDriverL10n.supervisedProjectCount(supervisedProjectCount)
                    )
                    if supervisedProjectCount == 0 {
                        Label(
                            DailyDriverL10n.noEligibleProjectDetail,
                            systemImage: "folder.badge.questionmark"
                        )
                        .font(.caption)
                        .foregroundStyle(StatusTone.caution.color)
                    }
                }

                if let modeNotice {
                    Label(modeNotice, systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text(DailyDriverL10n.scheduling)
            } footer: {
                Text(DailyDriverL10n.modeHelp)
            }

            Section {
                routingPolicyControl
            } header: {
                Text(L10n.settingsDefaultSchedulingPolicy)
            } footer: {
                Text(L10n.settingsDefaultSchedulingPolicyFooter)
            }

            Section {
                let enabled = store.ownerExecutionSettings?.ownerInitiatedExecutionEnabled ?? false
                Toggle(
                    L10n.ownerExecutionToggle,
                    isOn: Binding(
                        get: { enabled },
                        set: { newValue in
                            Task { await store.setOwnerExecution(enabled: newValue) }
                        }
                    )
                )
                .disabled(store.ownerExecutionSettings == nil)

                LabeledContent(L10n.ownerExecutionState, value: enabled ? "ON" : "OFF")
            } header: {
                Text(L10n.ownerExecutionSetting)
            } footer: {
                Text(L10n.ownerExecutionMeaning)
            }

            Section {
                if let active = store.activeStatus {
                    LabeledContent("ACTIVE", value: active.productionActive)
                    Label(
                        active.authorized ? L10n.activeAuthorized : L10n.activeNotAuthorized,
                        systemImage: active.authorized ? "lock.open" : "lock"
                    )
                    .foregroundStyle(.secondary)

                    if !active.blockingReasons.isEmpty {
                        DisclosureGroup(L10n.advancedDetails) {
                            VStack(alignment: .leading, spacing: 6) {
                                ForEach(active.blockingReasons, id: \.self) { reason in
                                    Label(reason, systemImage: "exclamationmark.triangle")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.top, 4)
                        }
                    }
                } else {
                    Label(DailyDriverL10n.fullAutomationLocked, systemImage: "lock")
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text(L10n.productionActive)
            } footer: {
                Text(L10n.activeReadOnlyNote)
            }
        }
        .formStyle(.grouped)
        .padding(16)
        .task {
            await store.loadSchedulingSettings()
            await store.refreshNow()
        }
    }

    @ViewBuilder
    private var automationModeControl: some View {
        if let settings = store.schedulingSettings {
            if settings.mode == "ACTIVE" {
                LabeledContent(DailyDriverL10n.scheduling) {
                    Label(DailyDriverL10n.fullAutomation, systemImage: "bolt.shield")
                }
                Label(DailyDriverL10n.fullAutomationLocked, systemImage: "lock")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Picker(
                    DailyDriverL10n.scheduling,
                    selection: Binding(
                        get: { settings.mode },
                        set: { setAutomationMode($0) }
                    )
                ) {
                    Text(DailyDriverL10n.manual).tag("MANUAL")
                    Text(DailyDriverL10n.supervisedAuto).tag("SUPERVISED_AUTO")
                }
                .pickerStyle(.segmented)
                .disabled(isChangingMode)

                if isChangingMode {
                    ProgressView().controlSize(.small)
                }
            }
        } else {
            LabeledContent(DailyDriverL10n.scheduling) {
                ProgressView().controlSize(.small)
            }
        }
    }

    @ViewBuilder
    private var routingPolicyControl: some View {
        if let settings = store.schedulingSettings {
            Picker(
                L10n.settingsDefaultSchedulingPolicy,
                selection: Binding(
                    get: { settings.defaultSchedulingPolicy },
                    set: { newValue in
                        Task { await store.setDefaultSchedulingPolicy(newValue) }
                    }
                )
            ) {
                ForEach(settings.selectablePolicies, id: \.self) { policy in
                    Text(L10n.schedulingPolicyName(policy)).tag(policy)
                }
            }
            .pickerStyle(.menu)

            Text(L10n.schedulingPolicyDetail(settings.defaultSchedulingPolicy))
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            ProgressView().controlSize(.small)
        }
    }

    private func setAutomationMode(_ mode: String) {
        guard !isChangingMode else { return }
        guard AutomationModeCatalog.selectable.contains(mode) else { return }
        isChangingMode = true
        modeNotice = nil
        let socketPath = store.socketPath

        Task {
            do {
                let updated = try await AutomationModeMutationCoordinator.shared.setMode(
                    socketPath: socketPath,
                    mode: mode
                )
                await store.loadSchedulingSettings()
                await store.refreshNow()
                modeNotice = updated.mode == mode
                    ? DailyDriverL10n.modeChanged(modeLabel(updated.mode))
                    : DailyDriverL10n.daemonReturnedMode(modeLabel(updated.mode))
            } catch let error as PAOClientError {
                modeNotice = DailyDriverL10n.modeChangeFailed(error.displayDetail)
            } catch {
                modeNotice = DailyDriverL10n.modeChangeMalformed
            }
            isChangingMode = false
        }
    }

    private func modeLabel(_ value: String) -> String {
        switch value {
        case "MANUAL": return DailyDriverL10n.manual
        case "SUPERVISED_AUTO": return DailyDriverL10n.supervisedAuto
        case "ACTIVE": return DailyDriverL10n.fullAutomation
        default: return value
        }
    }
}
