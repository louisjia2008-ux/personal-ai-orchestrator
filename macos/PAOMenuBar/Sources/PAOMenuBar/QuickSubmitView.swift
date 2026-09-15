import SwiftUI

import PAOControlKit

/// Fast path for starting work from the menu bar.
///
/// The daily-driver surface shows intent + project first. Routing policy, tier
/// floor and manual target remain available under disclosure rather than taking
/// over the default flow. The daemon is still authoritative for every routing
/// and execution decision.
struct QuickSubmitView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var intent: String = ""
    @State private var submitting: Bool = false
    @State private var showsAdvancedRouting = false
    @State private var schedulingPolicy = "BALANCED"
    @State private var minTier = "T1"
    @State private var manualExecutionTargetId: String?
    /// Store notices outlive a menu presentation. Render them only after this
    /// presentation has issued its own submit request, otherwise yesterday's
    /// failure/success looks like feedback for an untouched form.
    @State private var submissionAttempted = false

    private var onlineProjects: [ProjectView] {
        store.projects?.projects.filter(\.isOnline) ?? []
    }

    private var selectedProject: ProjectView? {
        guard let selectedProjectId = store.selectedProjectId else { return nil }
        return onlineProjects.first { $0.projectId == selectedProjectId }
    }

    /// Manual targets are eligible only when the provider surface is routing-
    /// connected (Pi READY auth is represented by its runtime availability) and
    /// the exact target satisfies the same current host launch prerequisites used
    /// by the Daily Driver readiness/new-task surfaces.
    private var connectedTargets: [ExecutionTargetHealthView] {
        let connectedIds = Set(
            (store.providerConnections?.connected ?? []).map(\.providerId)
        )
        return (store.providers?.providers ?? [])
            .flatMap { provider in
                provider.executionTargets.filter { target in
                    let connectionEligible = connectedIds.contains(provider.providerId)
                        || (target.runtimeId == "pi" && target.runtimeAvailable == true)
                    return connectionEligible && target.isLaunchableOnHost
                }
            }
            .sorted {
                if $0.runtimeId != $1.runtimeId {
                    return $0.runtimeId < $1.runtimeId
                }
                return $0.modelSkuId.localizedStandardCompare($1.modelSkuId) == .orderedAscending
            }
    }

    private var selectablePolicies: [String] {
        let daemonPolicies = store.schedulingSettings?.selectablePolicies
            ?? SelectablePolicyFallback.policies
        return daemonPolicies.contains("MANUAL") ? daemonPolicies : daemonPolicies + ["MANUAL"]
    }

    private static let selectableTiers = ["T0", "T1", "T2", "T3"]

    private var selectedProjectNeedsSupervisedOptIn: Bool {
        store.schedulingSettings?.mode == "SUPERVISED_AUTO"
            && selectedProject?.supervisedAutoAllowed != true
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(L10n.quickSubmit)
                .font(.subheadline.weight(.semibold))

            Picker(L10n.newTaskProject, selection: $store.selectedProjectId) {
                Text(L10n.newTaskSelectProject).tag(String?.none)
                ForEach(onlineProjects) { project in
                    Text(project.displayName).tag(String?.some(project.projectId))
                }
            }
            .pickerStyle(.menu)

            HStack {
                TextField(L10n.submitPlaceholder, text: $intent)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submit)
                Button(submitting ? "…" : L10n.submit, action: submit)
                    .buttonStyle(.borderedProminent)
                    .disabled(submitDisabled)
            }

            if selectedProjectNeedsSupervisedOptIn {
                Label(
                    DailyDriverL10n.noEligibleProjectDetail,
                    systemImage: "folder.badge.questionmark"
                )
                .font(.caption2)
                .foregroundStyle(StatusTone.caution.color)
                .fixedSize(horizontal: false, vertical: true)
            }

            DisclosureGroup(isExpanded: $showsAdvancedRouting) {
                VStack(alignment: .leading, spacing: 6) {
                    Picker(L10n.newTaskSchedulingPolicy, selection: $schedulingPolicy) {
                        ForEach(selectablePolicies, id: \.self) { policy in
                            Text(L10n.schedulingPolicyName(policy)).tag(policy)
                        }
                    }
                    .pickerStyle(.menu)

                    Text(L10n.schedulingPolicyDetail(schedulingPolicy))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    Picker(L10n.newTaskMinTier, selection: $minTier) {
                        ForEach(Self.selectableTiers, id: \.self) { tier in
                            Text(L10n.tierLabel(tier)).tag(tier)
                        }
                    }
                    .pickerStyle(.menu)

                    if schedulingPolicy == "MANUAL" {
                        Picker(L10n.newTaskManualModel, selection: $manualExecutionTargetId) {
                            Text(L10n.newTaskChooseModel).tag(String?.none)
                            ForEach(connectedTargets) { target in
                                let runtime = target.runtimeId == "pi" ? "Pi" : "OpenCode"
                                Text("\(runtime) · \(target.modelSkuId)")
                                    .tag(String?.some(target.executionTargetId))
                            }
                        }
                        .pickerStyle(.menu)
                    }
                }
                .padding(.top, 4)
            } label: {
                Text(L10n.advancedDetails)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if submissionAttempted,
               let notice = store.submitNotice,
               case .submitted(let taskId, _) = notice {
                Text(L10n.authoritativeTaskId(taskId))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(StatusTone.positive.color)
            }
            if submissionAttempted,
               let notice = store.submitNotice {
                Text(L10n.submitNotice(notice))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear {
            submissionAttempted = false
            if store.selectedProjectId == nil {
                store.selectedProjectId = onlineProjects.first?.projectId
            }
            resetRoutingToDaemonDefault()
        }
        .onChange(of: store.schedulingSettings?.defaultSchedulingPolicy) { _ in
            guard !showsAdvancedRouting else { return }
            resetRoutingToDaemonDefault()
        }
        .onChange(of: showsAdvancedRouting) { expanded in
            if !expanded {
                resetRoutingToDaemonDefault()
            }
        }
        .onChange(of: connectedTargets.map(\.executionTargetId)) { ids in
            if let manualExecutionTargetId, !ids.contains(manualExecutionTargetId) {
                self.manualExecutionTargetId = nil
            }
        }
    }

    private var submitDisabled: Bool {
        submitting
            || store.selectedProjectId == nil
            || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || (schedulingPolicy == "MANUAL" && manualExecutionTargetId == nil)
    }

    private func resetRoutingToDaemonDefault() {
        schedulingPolicy = store.schedulingSettings?.defaultSchedulingPolicy ?? "BALANCED"
        minTier = "T1"
        manualExecutionTargetId = nil
    }

    private func submit() {
        guard !submitDisabled else { return }
        let value = intent
        let projectId = store.selectedProjectId

        // QuickSubmit's controls are local so a hidden MANUAL choice from some
        // other window cannot silently affect this submission. Copy the explicit
        // values into the store immediately before its existing submit contract.
        store.selectedSchedulingPolicy = schedulingPolicy
        store.selectedMinTier = minTier
        store.selectedManualExecutionTargetId = manualExecutionTargetId

        submissionAttempted = true
        submitting = true
        Task {
            await store.quickSubmit(projectId: projectId, intent: value)
            submitting = false

            // `lastSubmittedTaskId` can survive an earlier successful submit, so
            // it is not evidence this click succeeded. Clear input only when the
            // structured notice from THIS invocation is a submission success.
            if let notice = store.submitNotice,
               case .submitted = notice {
                intent = ""
                if !showsAdvancedRouting {
                    resetRoutingToDaemonDefault()
                }
            }
        }
    }
}
