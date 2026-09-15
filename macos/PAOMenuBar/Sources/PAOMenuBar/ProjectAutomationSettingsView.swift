import SwiftUI

import PAOControlKit

/// Reusable project-level supervised-auto configuration.
///
/// Every edit runs through the process-wide project mutation coordinator. The
/// coordinator serializes the full GET/resolve/PUT tuple so two Settings/Home
/// sheets cannot overwrite each other's sibling fields with stale values.
struct ProjectAutomationSettingsView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.dismiss) private var dismiss
    @State private var savingProjectIds: Set<String> = []
    @State private var notice: String?

    let showsDoneButton: Bool

    init(showsDoneButton: Bool = true) {
        self.showsDoneButton = showsDoneButton
    }

    private var projects: [ProjectView] {
        (store.projects?.projects ?? [])
            .sorted { $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(DailyDriverL10n.projectAutomationTitle)
                        .font(.title2.weight(.semibold))
                    Text(DailyDriverL10n.projectAutomationSubtitle)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 20)
                if showsDoneButton {
                    Button(DailyDriverL10n.done) { dismiss() }
                        .keyboardShortcut(.defaultAction)
                }
            }
            .padding(20)

            Divider()

            if projects.isEmpty {
                EmptyStateView(
                    title: DailyDriverL10n.noRegisteredProjects,
                    symbol: "folder.badge.questionmark",
                    message: DailyDriverL10n.registerProjectFirst
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(projects) { project in
                    ProjectAutomationRow(
                        project: project,
                        isSaving: savingProjectIds.contains(project.projectId),
                        onSupervisedChanged: { value in
                            save(projectId: project.projectId, supervisedAutoAllowed: value)
                        },
                        onUnattendedChanged: { value in
                            save(projectId: project.projectId, unattendedAllowed: value)
                        },
                        onGraceChanged: { value in
                            save(projectId: project.projectId, graceSeconds: value)
                        }
                    )
                    .padding(.vertical, 6)
                }
                .listStyle(.inset(alternatesRowBackgrounds: false))
            }

            if let notice {
                Divider()
                Label(notice, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
            }
        }
        .frame(minWidth: 620, idealWidth: 680, minHeight: 420, idealHeight: 540)
        .task { await store.refreshNow() }
    }

    private func save(
        projectId: String,
        supervisedAutoAllowed: Bool? = nil,
        unattendedAllowed: Bool? = nil,
        graceSeconds: Int? = nil
    ) {
        guard !savingProjectIds.contains(projectId) else { return }
        savingProjectIds.insert(projectId)
        notice = nil
        let socketPath = store.socketPath

        Task {
            do {
                let updated = try await ProjectAutomationMutationCoordinator.shared.update(
                    socketPath: socketPath,
                    projectId: projectId,
                    supervisedAutoAllowed: supervisedAutoAllowed,
                    unattendedAllowed: unattendedAllowed,
                    graceSeconds: graceSeconds
                )
                guard updated != nil else {
                    notice = DailyDriverL10n.projectGone
                    savingProjectIds.remove(projectId)
                    await store.refreshNow()
                    return
                }
                await store.refreshNow()
                notice = DailyDriverL10n.projectSaved
            } catch let error as PAOClientError {
                notice = DailyDriverL10n.projectUpdateFailed(error.displayDetail)
                await store.refreshNow()
            } catch {
                notice = DailyDriverL10n.projectMalformed
                await store.refreshNow()
            }
            savingProjectIds.remove(projectId)
        }
    }
}

private struct ProjectAutomationRow: View {
    let project: ProjectView
    let isSaving: Bool
    let onSupervisedChanged: (Bool) -> Void
    let onUnattendedChanged: (Bool) -> Void
    let onGraceChanged: (Int) -> Void

    @State private var confirmingDisable = false
    @State private var graceDraft: String

    init(
        project: ProjectView,
        isSaving: Bool,
        onSupervisedChanged: @escaping (Bool) -> Void,
        onUnattendedChanged: @escaping (Bool) -> Void,
        onGraceChanged: @escaping (Int) -> Void
    ) {
        self.project = project
        self.isSaving = isSaving
        self.onSupervisedChanged = onSupervisedChanged
        self.onUnattendedChanged = onUnattendedChanged
        self.onGraceChanged = onGraceChanged
        _graceDraft = State(initialValue: String(project.graceSeconds))
    }

    private var graceValue: Int? { Int(graceDraft) }
    private var graceIsValid: Bool {
        guard let graceValue else { return false }
        return (1...86_400).contains(graceValue)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(project.displayName)
                        .font(.headline)
                    Text(project.canonicalRepoRoot)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if isSaving {
                    ProgressView().controlSize(.small)
                } else if project.supervisedAutoAllowed {
                    Label(DailyDriverL10n.enabled, systemImage: "checkmark.circle.fill")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(StatusTone.positive.color)
                }
            }

            Toggle(
                DailyDriverL10n.allowSupervisedAuto,
                isOn: Binding(
                    get: { project.supervisedAutoAllowed },
                    set: { value in
                        if value {
                            onSupervisedChanged(true)
                        } else if project.supervisedAutoAllowed {
                            confirmingDisable = true
                        }
                    }
                )
            )
            .disabled(isSaving || !project.isOnline)

            Toggle(
                DailyDriverL10n.allowUnattended,
                isOn: Binding(
                    get: { project.unattendedAllowed },
                    set: onUnattendedChanged
                )
            )
            .disabled(isSaving || !project.isOnline || !project.supervisedAutoAllowed)

            HStack {
                Text(DailyDriverL10n.graceWindow)
                Spacer()
                TextField(DailyDriverL10n.seconds, text: $graceDraft)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 92)
                    .multilineTextAlignment(.trailing)
                    .disabled(isSaving || !project.supervisedAutoAllowed)
                    .onSubmit(applyGrace)
                Text(DailyDriverL10n.seconds)
                    .foregroundStyle(.secondary)
                Button(DailyDriverL10n.apply, action: applyGrace)
                    .controlSize(.small)
                    .disabled(
                        isSaving || !project.supervisedAutoAllowed || !graceIsValid
                            || graceValue == project.graceSeconds
                    )
            }

            if !graceIsValid {
                Text(DailyDriverL10n.graceRange)
                    .font(.caption)
                    .foregroundStyle(StatusTone.caution.color)
            } else {
                Text(DailyDriverL10n.graceHelp)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .onChange(of: project.graceSeconds) { newValue in
            if !isSaving { graceDraft = String(newValue) }
        }
        .confirmationDialog(
            DailyDriverL10n.disableProjectTitle(project.displayName),
            isPresented: $confirmingDisable,
            titleVisibility: .visible
        ) {
            Button(DailyDriverL10n.disable, role: .destructive) {
                onSupervisedChanged(false)
            }
            Button(DailyDriverL10n.cancel, role: .cancel) {}
        } message: {
            Text(DailyDriverL10n.disableProjectMessage)
        }
    }

    private func applyGrace() {
        guard graceIsValid, let graceValue, graceValue != project.graceSeconds else { return }
        onGraceChanged(graceValue)
    }
}
