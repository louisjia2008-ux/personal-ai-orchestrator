import AppKit
import SwiftUI

import PAOControlKit

/// The same three owner-facing settings surfaces are used by the Dashboard
/// destination and the standard macOS Settings scene. This removes the previous
/// split where the two entry points exposed different automation controls.
enum SettingsTab: String, CaseIterable, Identifiable {
    case general
    case automation
    case projects

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return L10n.settingsTabGeneral
        case .automation: return DailyDriverL10n.scheduling
        case .projects: return L10n.sectionProjects
        }
    }
}

struct SettingsDashboard: View {
    @Binding var tab: SettingsTab
    let onNewTask: (String) -> Void

    var body: some View {
        Group {
            switch tab {
            case .general:
                GeneralSettingsPane()
            case .automation:
                AutomationSettingsPane()
            case .projects:
                ProjectsSettingsPane(onNewTask: onNewTask)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Picker(L10n.settingsPickerTitle, selection: $tab) {
                    ForEach(SettingsTab.allCases) { candidate in
                        Text(candidate.title).tag(candidate)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }
        }
    }
}

/// Launch/runtime preferences only. Scheduling, routing and Production ACTIVE
/// truth live together in AutomationSettingsPane instead of being duplicated
/// here and in the dedicated Automation tab.
struct GeneralSettingsPane: View {
    @AppStorage("pao.launchAtLogin") private var launchAtLogin = false
    @AppStorage("pao.autoStartDaemon") private var autoStartDaemon = true
    @EnvironmentObject private var store: OrchestratorStore
    private let layout = AppSupportLayout.resolve()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.section) {
                BuildInformationCard(
                    daemonBuild: store.daemonBuild,
                    compatibility: store.buildCompatibility
                )

                DashboardCard(title: L10n.daemonLifecycle, symbol: "gearshape") {
                    Toggle(L10n.launchAtLogin, isOn: $launchAtLogin)
                    Text(L10n.launchAtLoginFooter)
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Toggle(L10n.autoStartDaemon, isOn: $autoStartDaemon)
                    Text(L10n.autoStartDaemonFooter)
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    LabeledContent(L10n.socket, value: store.socketPath)
                    LabeledContent(L10n.runtimeConfig, value: layout.runtimeConfigPath)
                    DaemonLifecycleLabel(lifecycle: store.daemonLifecycle)
                }
            }
            .frame(maxWidth: DashboardLayoutMetrics.formMaximumWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, DashboardLayoutMetrics.pageHorizontalPadding)
            .padding(.vertical, DashboardLayoutMetrics.pageVerticalPadding)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

/// Project registry plus the project-level automation editor. The registry is
/// identical from Dashboard Settings and the standard Settings scene; only the
/// optional New Task action is Dashboard-specific.
struct ProjectsSettingsPane: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var selectedURL: URL?
    @State private var bookmarkData: Data?
    @State private var showsAutomationSettings = false

    var onNewTask: ((String) -> Void)? = nil

    private var projects: [ProjectView] {
        store.projects?.projects ?? []
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.section) {
                header

                if let preview = store.pendingProjectPreview {
                    DashboardCard(title: L10n.projectsDetectedRepository, symbol: "checkmark.seal") {
                        ProjectRegistryFacts(project: preview)
                        HStack {
                            Button {
                                selectedURL = nil
                                bookmarkData = nil
                            } label: {
                                Label(L10n.cancel, systemImage: "xmark")
                            }
                            Button {
                                guard let selectedURL else { return }
                                Task {
                                    await store.registerProject(
                                        path: selectedURL.path,
                                        displayName: preview.displayName,
                                        bookmarkData: bookmarkData
                                    )
                                }
                            } label: {
                                Label(L10n.projectsRegister, systemImage: "plus.circle")
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }
                }

                if projects.isEmpty {
                    EmptyStateView(
                        title: L10n.sectionProjects,
                        symbol: "folder.badge.gearshape",
                        message: L10n.projectsEmpty,
                        actionTitle: L10n.projectsAdd,
                        action: pickProjectFolder
                    )
                    .frame(maxWidth: .infinity)
                } else {
                    ForEach(projects) { project in
                        ProjectRegistryCard(project: project, onNewTask: onNewTask)
                    }
                }
            }
            .frame(maxWidth: DashboardLayoutMetrics.formMaximumWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, DashboardLayoutMetrics.pageHorizontalPadding)
            .padding(.vertical, DashboardLayoutMetrics.pageVerticalPadding)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .sheet(isPresented: $showsAutomationSettings) {
            ProjectAutomationSettingsView()
                .environmentObject(store)
        }
    }

    private var header: some View {
        HStack(spacing: Spacing.inner) {
            Text(L10n.sectionProjects)
                .font(.headline)
            Spacer(minLength: 12)
            Button {
                showsAutomationSettings = true
            } label: {
                Label(
                    DailyDriverL10n.projectAutomationTitle,
                    systemImage: "folder.badge.gearshape"
                )
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(projects.isEmpty)

            Button(action: pickProjectFolder) {
                Label(L10n.projectsAdd, systemImage: "folder.badge.plus")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
    }

    private func pickProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = L10n.projectsAdd
        let response = panel.runModal()
        guard response == .OK, let url = panel.url else { return }
        selectedURL = url
        bookmarkData = try? url.bookmarkData(
            options: [.withSecurityScope],
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        Task { await store.resolveProject(path: url.path) }
    }
}

private struct ProjectRegistryCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let project: ProjectView
    let onNewTask: ((String) -> Void)?

    var body: some View {
        DashboardCard(title: project.displayName, symbol: "folder") {
            HStack {
                ProjectRegistryStatusBadge(
                    text: project.storageAvailability,
                    tone: project.storageAvailability == "ONLINE" ? .positive : .critical
                )
                Text(project.canonicalRepoRoot)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            ProjectRegistryFacts(project: project)

            HStack(spacing: 8) {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: project.gitRoot)])
                    Task { await store.markProjectOpened(projectId: project.projectId) }
                } label: {
                    Label(L10n.projectsRevealInFinder, systemImage: "finder")
                }
                .controlSize(.small)

                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: project.canonicalRepoRoot))
                    Task { await store.markProjectOpened(projectId: project.projectId) }
                } label: {
                    Label(L10n.projectsOpen, systemImage: "arrow.up.forward.app")
                }
                .controlSize(.small)
                .disabled(project.storageAvailability != "ONLINE")

                if let onNewTask {
                    Button {
                        onNewTask(project.projectId)
                    } label: {
                        Label(L10n.newTask, systemImage: "plus")
                    }
                    .controlSize(.small)
                    .disabled(project.storageAvailability != "ONLINE")
                }

                Spacer(minLength: 0)

                Button(role: .destructive) {
                    Task { await store.removeProject(projectId: project.projectId) }
                } label: {
                    Label(L10n.projectsRemove, systemImage: "minus.circle")
                }
                .controlSize(.small)
            }
        }
    }
}

private struct ProjectRegistryFacts: View {
    let project: ProjectView

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent(L10n.projectsGitRoot, value: project.gitRoot)
            if let workingSubpath = project.workingSubpath {
                LabeledContent(L10n.projectsWorkingSubpath, value: workingSubpath)
            }
            LabeledContent(L10n.projectsBranch, value: project.currentBranch ?? project.defaultBranch)
            LabeledContent("HEAD", value: short(project.lastKnownHead))
            LabeledContent(L10n.projectsRecentTasks, value: "\(project.recentTaskCount)")
            LabeledContent(L10n.projectsLastUsed, value: project.lastOpenedAt ?? L10n.never)
        }
        .font(.caption)
    }

    private func short(_ sha: String) -> String {
        sha.count > 12 ? String(sha.prefix(12)) : sha
    }
}

private struct ProjectRegistryStatusBadge: View {
    let text: String
    let tone: StatusTone

    var body: some View {
        Text(text)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(tone.color.opacity(0.14), in: Capsule())
            .foregroundStyle(tone.color)
            .lineLimit(1)
    }
}

private struct DaemonLifecycleLabel: View {
    @ObservedObject var lifecycle: DaemonLifecycleController

    var body: some View {
        LabeledContent(L10n.daemonLifecycle, value: lifecycle.status.displayValue)
    }
}

/// Build identity stays in General because a stale app/daemon pair invalidates
/// every other setting surface. Raw commit identifiers remain behind Advanced.
private struct BuildInformationCard: View {
    let daemonBuild: BuildView?
    let compatibility: BuildCompatibility

    private var app: BuildIdentity { BuildIdentity.current }

    var body: some View {
        DashboardCard(title: L10n.buildTitle, symbol: "hammer") {
            if compatibility.isMismatch {
                mismatchBanner
            }
            LabeledContent(L10n.buildVersion, value: appVersion)
            LabeledContent(L10n.buildShort, value: display(app.shortSHA))
            LabeledContent(L10n.buildConfiguration, value: display(app.configuration))
            LabeledContent(L10n.buildTimestamp, value: display(app.builtAt))
            LabeledContent(L10n.buildDaemon, value: display(daemonBuild?.shortSHA))
            statusLabel
            DisclosureGroup(L10n.buildAdvanced) {
                VStack(alignment: .leading, spacing: 6) {
                    LabeledContent(L10n.buildCommit, value: display(app.commitSHA))
                    LabeledContent(
                        "\(L10n.buildDaemon) · \(L10n.buildCommit)",
                        value: display(daemonBuild?.commitSHA)
                    )
                }
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 4)
            }
            .font(.caption)
        }
    }

    private var mismatchBanner: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(L10n.buildMismatchTitle, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline.bold())
                .foregroundStyle(.orange)
            LabeledContent(L10n.buildMismatchApp, value: display(app.shortSHA))
            LabeledContent(L10n.buildMismatchDaemon, value: display(daemonBuild?.shortSHA))
            Text(L10n.buildMismatchFooter)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder
    private var statusLabel: some View {
        switch compatibility {
        case .matched:
            Label(L10n.buildMatched, systemImage: "checkmark.seal")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .indeterminate:
            Label(L10n.buildIndeterminate, systemImage: "questionmark.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .mismatched:
            EmptyView()
        }
    }

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }

    private func display(_ value: String?) -> String {
        guard let value,
              !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              value != BuildIdentity.unknownValue
        else {
            return L10n.buildUnknown
        }
        return value
    }
}
