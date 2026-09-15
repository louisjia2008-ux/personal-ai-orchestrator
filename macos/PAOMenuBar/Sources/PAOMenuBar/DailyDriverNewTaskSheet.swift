import SwiftUI

import PAOControlKit

/// Daily-driver replacement for the legacy technical New Task form.
///
/// Intent and project are the primary workflow. Routing policy, tier floor and
/// manual target remain available under progressive disclosure. There is no
/// separate "execution target override" that submits a task and then dispatches
/// it behind the user's back; MANUAL is the one explicit target-pinning path.
struct DailyDriverNewTaskSheet: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.dismiss) private var dismiss

    @State private var intent = ""
    @State private var schedulingPolicy = "BALANCED"
    @State private var minTier = "T1"
    @State private var manualExecutionTargetId: String?
    @State private var showsAdvancedRouting = false
    @State private var submitting = false
    /// `OrchestratorStore.submitNotice` is process-level convenience state and
    /// intentionally survives a sheet dismissal. Do not render an earlier
    /// interaction's failure before this sheet has made its own submit attempt.
    @State private var submissionAttempted = false

    let onSubmitted: (String) -> Void

    private var onlineProjects: [ProjectView] {
        store.projects?.projects.filter(\.isOnline) ?? []
    }

    private var selectedProject: ProjectView? {
        guard let projectId = store.selectedProjectId else { return nil }
        return onlineProjects.first { $0.projectId == projectId }
    }

    private var connectedTargets: [ExecutionTargetHealthView] {
        DailyDriverExecutionTargets.launchable(
            providers: store.providers,
            connections: store.providerConnections
        )
    }

    private var policies: [String] {
        let daemonPolicies = store.schedulingSettings?.selectablePolicies
            ?? SelectablePolicyFallback.policies
        return daemonPolicies.contains("MANUAL") ? daemonPolicies : daemonPolicies + ["MANUAL"]
    }

    private var selectedProjectNeedsSupervisedOptIn: Bool {
        store.schedulingSettings?.mode == "SUPERVISED_AUTO"
            && selectedProject?.supervisedAutoAllowed != true
    }

    private var submitDisabled: Bool {
        submitting
            || store.selectedProjectId == nil
            || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || (schedulingPolicy == "MANUAL" && manualExecutionTargetId == nil)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.section) {
                    TextEditor(text: $intent)
                        .font(.body)
                        .frame(minHeight: 130)
                        .padding(8)
                        .background(
                            Color(nsColor: .textBackgroundColor),
                            in: RoundedRectangle(cornerRadius: Radius.panel)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: Radius.panel)
                                .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                        )

                    Picker(L10n.newTaskProject, selection: $store.selectedProjectId) {
                        Text(L10n.newTaskSelectProject).tag(String?.none)
                        ForEach(onlineProjects) { project in
                            Text(project.displayName).tag(String?.some(project.projectId))
                        }
                    }
                    .pickerStyle(.menu)

                    if selectedProjectNeedsSupervisedOptIn {
                        Label(
                            DailyDriverL10n.noEligibleProjectDetail,
                            systemImage: "folder.badge.questionmark"
                        )
                        .font(.caption)
                        .foregroundStyle(StatusTone.caution.color)
                        .fixedSize(horizontal: false, vertical: true)
                    }

                    routingSummary
                    advancedRouting

                    if submissionAttempted,
                       let notice = store.submitNotice,
                       !isSuccessfulSubmission(notice) {
                        Label(L10n.submitNotice(notice), systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(StatusTone.caution.color)
                    }
                }
                .padding(20)
            }

            Divider()
            actions
        }
        .frame(minWidth: 600, idealWidth: 640, minHeight: 430, idealHeight: 500)
        .onAppear {
            if store.selectedProjectId == nil {
                store.selectedProjectId = onlineProjects.first?.projectId
            }
            resetToDaemonDefaults()
        }
        .onChange(of: store.schedulingSettings?.defaultSchedulingPolicy) { _ in
            guard !showsAdvancedRouting else { return }
            resetToDaemonDefaults()
        }
        .onChange(of: connectedTargets.map(\.executionTargetId)) { ids in
            // Runtime/verification truth can change while this sheet is open.
            // Never keep a MANUAL selection that disappeared from the launchable set.
            if let manualExecutionTargetId, !ids.contains(manualExecutionTargetId) {
                self.manualExecutionTargetId = nil
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(L10n.newTaskTitle)
                .font(.title2.weight(.semibold))
            Text(L10n.newTaskSubtitle)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding(20)
    }

    private var routingSummary: some View {
        HStack(spacing: Spacing.inner) {
            Image(systemName: schedulingPolicy == "MANUAL" ? "hand.raised" : "arrow.triangle.branch")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(L10n.schedulingPolicyName(schedulingPolicy))
                    .font(.callout.weight(.medium))
                Text(L10n.schedulingPolicyDetail(schedulingPolicy))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            Text(L10n.tierLabel(minTier))
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .background(
            Color(nsColor: .controlBackgroundColor),
            in: RoundedRectangle(cornerRadius: Radius.panel)
        )
    }

    private var advancedRouting: some View {
        DisclosureGroup(isExpanded: $showsAdvancedRouting) {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                Picker(L10n.newTaskSchedulingPolicy, selection: $schedulingPolicy) {
                    ForEach(policies, id: \.self) { policy in
                        Text(L10n.schedulingPolicyName(policy)).tag(policy)
                    }
                }
                .pickerStyle(.menu)

                Picker(L10n.newTaskMinTier, selection: $minTier) {
                    ForEach(["T0", "T1", "T2", "T3"], id: \.self) { tier in
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
            .padding(.top, Spacing.inner)
        } label: {
            Text(L10n.advancedDetails)
                .font(.callout.weight(.medium))
        }
        .onChange(of: schedulingPolicy) { policy in
            if policy != "MANUAL" { manualExecutionTargetId = nil }
        }
    }

    private var actions: some View {
        HStack {
            Spacer()
            Button(L10n.cancel, role: .cancel) { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button(submitting ? L10n.submitting : L10n.submit, action: submit)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(submitDisabled)
        }
        .padding(16)
    }

    private func resetToDaemonDefaults() {
        schedulingPolicy = store.schedulingSettings?.defaultSchedulingPolicy ?? "BALANCED"
        minTier = "T1"
        manualExecutionTargetId = nil
    }

    private func isSuccessfulSubmission(_ notice: SubmitNotice) -> Bool {
        if case .submitted = notice { return true }
        return false
    }

    private func submit() {
        guard !submitDisabled else { return }
        let projectId = store.selectedProjectId
        let value = intent

        store.selectedSchedulingPolicy = schedulingPolicy
        store.selectedMinTier = minTier
        store.selectedManualExecutionTargetId = manualExecutionTargetId

        submissionAttempted = true
        submitting = true
        Task {
            await store.quickSubmit(projectId: projectId, intent: value)
            submitting = false

            // `lastSubmittedTaskId` intentionally persists as an app-level
            // convenience. It therefore cannot prove THIS invocation succeeded:
            // after any earlier success it may still be non-nil when this submit
            // is rejected. Only this invocation's structured notice is evidence.
            if let notice = store.submitNotice,
               case .submitted(let taskId, _) = notice {
                onSubmitted(taskId)
            }
        }
    }
}
