import SwiftUI

import PAOControlKit

struct QuickSubmitView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var intent: String = ""
    @State private var submitting: Bool = false

    private var onlineProjects: [ProjectView] {
        store.projects?.projects.filter(\.isOnline) ?? []
    }

    /// Manual targets are owner-connected for connection-managed runtimes,
    /// while Pi is considered connected when its host-authenticated runtime
    /// reports the target as available.
    private var connectedTargets: [ExecutionTargetHealthView] {
        let connectedIds = Set(
            (store.providerConnections?.connected ?? []).map(\.providerId)
        )
        return (store.providers?.providers ?? [])
            .flatMap { provider in
                provider.executionTargets.filter { target in
                    connectedIds.contains(provider.providerId)
                        || (target.runtimeId == "pi" && target.runtimeAvailable == true)
                }
            }
            .sorted {
                if $0.runtimeId != $1.runtimeId {
                    return $0.runtimeId < $1.runtimeId
                }
                return $0.modelSkuId.localizedStandardCompare($1.modelSkuId) == .orderedAscending
            }
    }

    private static let selectablePolicies = SelectablePolicyFallback.policies + ["MANUAL"]

    /// M1 WP2: tier floor options.
    private static let selectableTiers = ["T0", "T1", "T2", "T3"]

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(L10n.quickSubmit).font(.subheadline).foregroundStyle(.secondary)
            Picker(L10n.newTaskProject, selection: $store.selectedProjectId) {
                Text(L10n.newTaskSelectProject).tag(String?.none)
                ForEach(onlineProjects) { project in
                    Text(project.displayName).tag(String?.some(project.projectId))
                }
            }
            .pickerStyle(.menu)
            Picker(L10n.newTaskSchedulingPolicy, selection: $store.selectedSchedulingPolicy) {
                ForEach(Self.selectablePolicies, id: \.self) { policy in
                    Text(L10n.schedulingPolicyName(policy)).tag(policy)
                }
            }
            .pickerStyle(.menu)
            // M1 WP2: tier floor picker; mirrors the dashboard sheet
            // but rendered inline because the quick-submit view is
            // dense.
            Picker(L10n.newTaskMinTier, selection: $store.selectedMinTier) {
                ForEach(Self.selectableTiers, id: \.self) { tier in
                    Text(L10n.tierLabel(tier)).tag(tier)
                }
            }
            .pickerStyle(.menu)
            if store.selectedSchedulingPolicy == "MANUAL" {
                Picker(L10n.newTaskManualModel, selection: $store.selectedManualExecutionTargetId) {
                    Text(L10n.newTaskChooseModel).tag(String?.none)
                    ForEach(connectedTargets) { target in
                        let runtime = target.runtimeId == "pi" ? "Pi" : "OpenCode"
                        Text("\(runtime) · \(target.modelSkuId)")
                            .tag(String?.some(target.executionTargetId))
                    }
                }
                .pickerStyle(.menu)
            }
            HStack {
                TextField(L10n.submitPlaceholder, text: $intent)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submit)
                Button(submitting ? "…" : L10n.submit, action: submit)
                    .disabled(
                        submitting
                            || store.selectedProjectId == nil
                            || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || (store.selectedSchedulingPolicy == "MANUAL"
                                && store.selectedManualExecutionTargetId == nil)
                    )
            }
            if let taskId = store.lastSubmittedTaskId {
                Text(L10n.authoritativeTaskId(taskId))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.green)
            }
            if let notice = store.submitNotice {
                Text(L10n.submitNotice(notice)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .onAppear {
            if store.selectedProjectId == nil {
                store.selectedProjectId = onlineProjects.first?.projectId
            }
        }
    }

    private func submit() {
        let value = intent
        let projectId = store.selectedProjectId
        submitting = true
        Task {
            await store.quickSubmit(projectId: projectId, intent: value)
            submitting = false
            if store.lastSubmittedTaskId != nil {
                intent = ""
            }
        }
    }
}
