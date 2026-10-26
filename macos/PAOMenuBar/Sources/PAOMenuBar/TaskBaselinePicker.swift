import SwiftUI
import PAOControlKit

struct TaskBaselinePicker: View {
    @EnvironmentObject private var store: OrchestratorStore
    let projectId: String?
    let refreshGeneration: Int
    @Binding var selection: TaskBaselineSelection?
    @State private var mode: TaskBaselineMode = .committedHead
    @State private var preview: ProjectBaselinePreview?
    @State private var error: String?
    @State private var refresh = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(L10n.workflow("baselineTitle")).font(.subheadline)
                Spacer()
                Button(L10n.workflow("baselineRefresh")) { refresh += 1 }
                    .disabled(projectId == nil)
                    .accessibilityIdentifier("task.baseline.refresh")
            }
            if let preview {
                Text("\(preview.sourceBranch.isEmpty ? "HEAD" : preview.sourceBranch) · \(preview.sourceHead.prefix(12)) · \(L10n.workflow("baselineChanges")): \(preview.changedFileCount) (\(L10n.workflow("baselineUntracked")): \(preview.untrackedFileCount))")
                    .font(.caption).textSelection(.enabled)
                Picker(L10n.workflow("baselineTitle"), selection: $mode) {
                    Text(TaskBaselineMode.committedHead.title).tag(TaskBaselineMode.committedHead)
                    Text(TaskBaselineMode.workingTreeSnapshot.title)
                        .tag(TaskBaselineMode.workingTreeSnapshot).disabled(!preview.snapshotAvailable)
                }
                .pickerStyle(.menu).accessibilityIdentifier("task.baseline.mode")
                Text(L10n.workflow(mode == .committedHead ? "baselineHeadHelp" : "baselineWorkingHelp"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !preview.snapshotAvailable {
                    Text("\(L10n.workflow("baselineUnavailable")): \(preview.snapshotError ?? "UNKNOWN")")
                        .font(.caption).foregroundStyle(.orange)
                }
            } else if let error {
                Text(error).font(.caption).foregroundStyle(.red)
            } else if projectId != nil {
                ProgressView().controlSize(.small)
            }
        }
        .task(id: "\(projectId ?? ""): \(refresh): \(refreshGeneration)") {
            selection = nil; preview = nil; error = nil
            guard let projectId else { return }
            do {
                let value = try await PAOControlClient(socketPath: store.socketPath, timeoutSeconds: 35)
                    .projectBaseline(projectId)
                guard !Task.isCancelled else { return }
                preview = value
                if !value.snapshotAvailable { mode = .committedHead }
                updateSelection()
            } catch {
                guard !Task.isCancelled else { return }
                self.error = L10n.workflow("baselineRefreshRequired")
            }
        }
        .onChange(of: mode) { _ in updateSelection() }
    }

    private func updateSelection() {
        guard let projectId, let preview,
              mode == .committedHead || preview.snapshotAvailable else {
            selection = nil
            return
        }
        selection = TaskBaselineSelection(projectId: projectId, mode: mode, fingerprint: preview.fingerprint)
    }
}
