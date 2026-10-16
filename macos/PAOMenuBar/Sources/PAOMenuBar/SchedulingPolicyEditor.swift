import SwiftUI
import PAOControlKit

/// A draft is separate from the polled authoritative settings. A second client
/// can change the daemon without either overwriting this draft or being silently
/// overwritten by it. Reload is deliberate and never submits the draft.
struct SchedulingPolicyEditor: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var projectId = ""
    @State private var draftPolicy = "BALANCED"
    @State private var draftTarget = ""
    @State private var binding: PolicyRevisionBinding?
    @State private var loadedPolicy = ""
    @State private var loadedTarget = ""
    @State private var effectivePolicy: String?
    @State private var message: String?
    @State private var isLoading = false
    @State private var loadId: UUID?

    private var isProject: Bool { !projectId.isEmpty }
    private var changed: Bool { draftPolicy != loadedPolicy || draftTarget != loadedTarget }
    private var policies: [String] {
        let values = store.schedulingSettings?.selectablePolicies ?? SelectablePolicyFallback.policies
        return isProject ? ["INHERIT"] + values + ["MANUAL"] : values
    }
    private var targets: [ExecutionTargetHealthView] {
        (store.providers?.providers ?? []).flatMap(\.executionTargets)
    }

    var body: some View {
        DashboardCard(title: L10n.settingsDefaultSchedulingPolicy, symbol: "slider.horizontal.3") {
            Picker(L10n.controlText("policy.scope"), selection: $projectId) {
                Text(L10n.policyGlobalDefault).tag("")
                ForEach(store.projects?.projects ?? []) { project in
                    Text(project.displayName).tag(project.projectId)
                }
            }
            .disabled(store.isSavingSchedulingPolicy)
            Picker(L10n.policyTitle, selection: $draftPolicy) {
                ForEach(Array(Set(policies + [draftPolicy])).sorted(), id: \.self) { policy in
                    Text(policy == "INHERIT" ? L10n.controlText("policy.inherit") : L10n.schedulingPolicyName(policy))
                        .tag(policy)
                }
            }
            .disabled(isLoading || binding == nil || store.isSavingSchedulingPolicy)
            .accessibilityIdentifier("policy.draft")
            if isProject && draftPolicy == "MANUAL" {
                Picker(L10n.newTaskManualModel, selection: $draftTarget) {
                    Text(L10n.newTaskChooseModel).tag("")
                    ForEach(targets) { target in
                        Text(target.modelSkuId).tag(target.executionTargetId)
                    }
                    if !draftTarget.isEmpty && !targets.contains(where: { $0.executionTargetId == draftTarget }) {
                        Text(draftTarget).tag(draftTarget)
                    }
                }
                .disabled(isLoading || store.isSavingSchedulingPolicy)
            }
            if let effectivePolicy {
                LabeledContent(L10n.controlText("policy.current"), value: L10n.schedulingPolicyName(effectivePolicy))
                    .font(.caption)
            }
            if draftPolicy != "INHERIT" {
                Text(L10n.schedulingPolicyDetail(draftPolicy))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error = store.schedulingPolicyError {
                Label(L10n.controlText("policy.conflict") + " " + error, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if let message {
                Text(message).font(.caption).foregroundStyle(.secondary)
            }
            if binding == nil && !isLoading {
                Text(L10n.controlText("policy.unavailable"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button(L10n.controlText("policy.reload")) {
                    Task { await load(preserveDraft: true) }
                }
                .disabled(isLoading || store.isSavingSchedulingPolicy)
                Button(L10n.controlText("policy.save")) { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!changed || binding == nil || isLoading || store.isSavingSchedulingPolicy
                              || store.schedulingPolicyNeedsReview
                              || (draftPolicy == "MANUAL" && draftTarget.isEmpty))
                    .accessibilityIdentifier("policy.save")
                if isLoading || store.isSavingSchedulingPolicy { ProgressView().controlSize(.small) }
            }
            Text(L10n.settingsDefaultSchedulingPolicyFooter)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .task(id: projectId) { await load(preserveDraft: false) }
    }

    private func load(preserveDraft: Bool) async {
        let requestId = UUID()
        let scope = projectId
        loadId = requestId
        isLoading = true
        defer { if loadId == requestId { isLoading = false } }
        do {
            let policy: String
            let target: String
            let revision: PolicyRevisionBinding?
            let effective: String
            if scope.isEmpty {
                await store.loadSchedulingSettings()
                guard let settings = store.schedulingSettings else { throw PAOClientError.malformedResponse }
                policy = settings.defaultSchedulingPolicy
                target = ""
                revision = settings.revisionBinding
                effective = settings.defaultSchedulingPolicy
            } else {
                let settings = try await store.projectScheduling(scope)
                policy = settings.schedulingPolicy ?? "INHERIT"
                target = settings.manualExecutionTargetId ?? ""
                revision = settings.revisionBinding
                effective = settings.effectiveSchedulingPolicy
            }
            guard loadId == requestId, projectId == scope else { return }
            binding = revision
            loadedPolicy = policy
            loadedTarget = target
            effectivePolicy = effective
            if !preserveDraft { draftPolicy = policy; draftTarget = target }
            message = preserveDraft ? L10n.controlText("policy.review") : nil
            store.clearSchedulingPolicyError()
        } catch {
            guard loadId == requestId, projectId == scope else { return }
            binding = nil
            message = (error as? PAOClientError)?.displayDetail ?? L10n.controlText("policy.unavailable")
        }
    }

    private func save() {
        guard let binding else { return }
        let scope = projectId
        let policy = draftPolicy
        let target = draftTarget
        Task {
            let saved: Bool
            if scope.isEmpty {
                saved = await store.setDefaultSchedulingPolicy(policy, binding: binding)
            } else {
                saved = await store.setProjectSchedulingPolicy(
                    projectId: scope, policy: policy == "INHERIT" ? nil : policy,
                    manualExecutionTargetId: policy == "MANUAL" ? target : nil, binding: binding
                )
            }
            guard projectId == scope else { return }
            if saved {
                await load(preserveDraft: false)
                guard projectId == scope else { return }
                message = L10n.controlText("policy.saved")
            }
        }
    }
}
