import SwiftUI

import PAOControlKit

struct QuickSubmitView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var intent: String = ""
    @State private var submitting: Bool = false

    private var onlineProjects: [ProjectView] {
        store.projects?.projects.filter(\.isOnline) ?? []
    }

    /// Manual targets come from owner-connected providers only.
    private var connectedTargets: [ExecutionTargetHealthView] {
        let connectedIds = Set(
            (store.providerConnections?.connected ?? []).map(\.providerId)
        )
        return (store.providers?.providers ?? [])
            .filter { connectedIds.contains($0.providerId) }
            .flatMap(\.executionTargets)
    }

    private static let selectablePolicies = [
        "BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "MANUAL",
    ]

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
            if store.selectedSchedulingPolicy == "MANUAL" {
                Picker(L10n.newTaskManualModel, selection: $store.selectedManualExecutionTargetId) {
                    Text(L10n.newTaskChooseModel).tag(String?.none)
                    ForEach(connectedTargets) { target in
                        Text(target.modelSkuId).tag(String?.some(target.executionTargetId))
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
