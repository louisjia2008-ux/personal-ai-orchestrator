import AppKit
import SwiftUI
import PAOControlKit

struct ProjectVerificationSheet: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.dismiss) private var dismiss
    let project: ProjectView
    @State private var commands = ""
    @State private var preparationCommands = ""
    @State private var paths = ""
    @State private var timeout = 300
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.workflow("configure")).font(.title2.bold())
            Text(project.displayName).foregroundStyle(.secondary)
            Text(L10n.workflow("commandHelp")).font(.callout)
            Text(L10n.workflow("preparationCommands")).font(.headline)
            TextEditor(text: $preparationCommands)
                .font(.system(.body, design: .monospaced))
                .frame(height: 85).border(.secondary.opacity(0.3))
                .accessibilityIdentifier("project.verification.preparation")
            Text(L10n.workflow("commands")).font(.headline)
            TextEditor(text: $commands)
                .font(.system(.body, design: .monospaced))
                .frame(height: 115).border(.secondary.opacity(0.3))
                .accessibilityIdentifier("project.verification.commands")
            Text(L10n.workflow("paths")).font(.headline)
            TextField(L10n.workflow("pathsHelp"), text: $paths)
                .textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("project.verification.paths")
            Stepper("\(L10n.workflow("timeout")): \(timeout)s", value: $timeout, in: 1...1800)
            Text(L10n.workflow("trustHelp")).font(.caption).foregroundStyle(.secondary)
            if let error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                if !loaded { ProgressView().controlSize(.small) }
                Spacer()
                Button(L10n.cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L10n.workflow("save")) { save() }
                    .buttonStyle(.borderedProminent).disabled(!loaded || saving)
                    .accessibilityIdentifier("project.verification.save")
            }
        }
        .padding(24).frame(width: 650)
        .task {
            do {
                let result = try await PAOControlClient(socketPath: store.socketPath)
                    .projectVerification(project.projectId)
                commands = result.commandLines.joined(separator: "\n")
                preparationCommands = (result.preparationCommandLines ?? []).joined(separator: "\n")
                paths = result.allowedPaths.joined(separator: ", ")
                timeout = result.timeoutSeconds
                loaded = true
            } catch { self.error = workflowError(error) }
        }
    }

    private func save() {
        saving = true
        error = nil
        Task {
            defer { saving = false }
            do {
                _ = try await PAOControlClient(socketPath: store.socketPath).saveProjectVerification(
                    project.projectId,
                    commands: commands.split(separator: "\n").map(String.init)
                        .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty },
                    allowedPaths: paths.split(separator: ",").map {
                        $0.trimmingCharacters(in: .whitespacesAndNewlines)
                    }.filter { !$0.isEmpty }, timeout: timeout,
                    preparationCommands: preparationCommands.split(separator: "\n").map(String.init)
                        .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                )
                dismiss()
            } catch { self.error = workflowError(error) }
        }
    }
}

struct TaskResultSheet: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.dismiss) private var dismiss
    let taskId: String
    @State private var result: TaskPatchView?
    @State private var summary: CrossClientResultView?
    @State private var summaryError: String?
    @State private var mode = ResultMode.summary
    @State private var error: String?
    @State private var exported = false
    @State private var history: TaskVerificationHistoryView?
    @State private var selectedEvidenceId = ""
    private enum ResultMode: Hashable { case summary, patch }

    private var canExport: Bool {
        guard let result, let summary else { return false }
        return TaskResultPatchRefresh.matches(
            result, summary: summary, taskId: taskId, evidenceId: selectedEvidenceId
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L10n.workflow("result")).font(.title2.bold())
            if let history, !history.entries.isEmpty {
                Picker(L10n.workflow("history"), selection: $selectedEvidenceId) {
                    Text(L10n.workflow("currentResult")).tag("")
                    ForEach(history.entries) { entry in
                        Text("\(entry.status) · \(entry.verdict) · \(entry.evidenceId)")
                            .tag(entry.evidenceId).disabled(!entry.available)
                    }
                }.accessibilityIdentifier("task.result.history")
                if history.truncated {
                    Text(L10n.workflow("historyTruncated")).font(.caption).foregroundStyle(.secondary)
                }
            }
            if !selectedEvidenceId.isEmpty {
                Text(L10n.workflow("historyHelp")).foregroundStyle(.orange)
                    .accessibilityIdentifier("task.result.historical")
            }
            Picker(L10n.workflow("resultMode"), selection: $mode) {
                Text(L10n.workflow("summary")).tag(ResultMode.summary)
                Text(L10n.workflow("diff")).tag(ResultMode.patch)
            }
            .pickerStyle(.segmented).accessibilityIdentifier("task.result.mode")
            if mode == .summary {
                summaryContent
            } else {
                patchContent
            }
            if exported { Text(L10n.workflow("exported")).foregroundStyle(.green) }
            HStack {
                Button(L10n.workflow("export")) { export() }.disabled(!canExport)
                    .accessibilityIdentifier("task.result.export")
                Spacer()
                Button(L10n.workflow("close")) { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(24).frame(minWidth: 740, minHeight: 500)
        .task(id: taskId) {
            history = nil
            do {
                let value = try await PAOControlClient(socketPath: store.socketPath).taskVerificationHistory(taskId)
                guard !Task.isCancelled else { return }
                history = value
            } catch { /* Current results remain usable when history is unavailable. */ }
        }
        .task(id: taskId + ":" + selectedEvidenceId) {
            result = nil; summary = nil; error = nil; summaryError = nil; exported = false
            let client = PAOControlClient(socketPath: store.socketPath)
            let selection = selectedEvidenceId
            async let patchRequest = client.taskPatch(taskId, evidenceId: selection.isEmpty ? nil : selection)
            async let summaryRequest = loadSummary(client, evidenceId: selection)
            do {
                let value = try await patchRequest
                guard !Task.isCancelled else { return }
                result = value
            } catch {
                guard !Task.isCancelled else { return }
                self.error = workflowError(error)
            }
            do {
                let value = try await summaryRequest
                guard !Task.isCancelled else { return }
                summary = value
                if let patch = result, patch.source == "VERIFIED_SNAPSHOT",
                   (value.binding.patchSHA256 != patch.sha256 || value.binding.patchByteCount != patch.byteCount) {
                    result = nil
                    self.error = L10n.workflow("bindingMismatch")
                }
            } catch {
                guard !Task.isCancelled else { return }
                summary = nil
                summaryError = workflowError(error)
            }
            // A terminal task's directory can still be edited by its owner.
            // Keep the current verdict fresh while displaying immutable patch bytes.
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 3_000_000_000) }
                catch { return }
                do {
                    let value = try await loadSummary(client, evidenceId: selection)
                    guard !Task.isCancelled else { return }
                    summary = value
                    summaryError = nil
                    if TaskResultPatchRefresh.shouldRefresh(
                        patch: result, summary: value, taskId: taskId, evidenceId: selection
                    ) {
                        // A sheet opened during verification has no accepted patch yet.
                        // Fetch the new snapshot as soon as its host binding arrives.
                        result = nil; error = nil; exported = false
                        do {
                            let patch = try await client.taskPatch(taskId, evidenceId: selection.isEmpty ? nil : selection)
                            guard !Task.isCancelled else { return }
                            if TaskResultPatchRefresh.matches(patch, summary: value, taskId: taskId, evidenceId: selection) {
                                result = patch
                            } else {
                                self.error = L10n.workflow("bindingMismatch")
                            }
                        } catch {
                            guard !Task.isCancelled else { return }
                            self.error = workflowError(error)
                        }
                        do {
                            let refreshed = try await client.taskVerificationHistory(taskId)
                            guard !Task.isCancelled else { return }
                            history = refreshed
                        } catch { /* Accepted current bytes remain usable without history. */ }
                    } else if let patch = result, patch.source == "VERIFIED_SNAPSHOT",
                              !TaskResultPatchRefresh.matches(patch, summary: value, taskId: taskId, evidenceId: selection) {
                        result = nil
                        self.error = L10n.workflow("bindingMismatch")
                    }
                } catch {
                    guard !Task.isCancelled else { return }
                    summary = nil
                    summaryError = workflowError(error)
                }
            }
        }
    }

    private func loadSummary(_ client: PAOControlClient, evidenceId: String) async throws -> CrossClientResultView {
        if evidenceId.isEmpty { return try await client.taskCrossClientResult(taskId) }
        return try await client.taskHistoricalResult(taskId, evidenceId: evidenceId)
    }

    @ViewBuilder
    private var patchContent: some View {
        Text(L10n.workflow(!selectedEvidenceId.isEmpty ? "historyHelp" : result?.source == "VERIFIED_SNAPSHOT" ? "snapshotHelp" : "resultHelp"))
            .font(.callout).foregroundStyle(.secondary)
        if let result {
            if summary?.verification.status == "VERIFICATION_STALE"
                || (summary == nil && result.workspaceStatus == "STALE") {
                Text(L10n.workflow("stale")).foregroundStyle(.orange)
                    .accessibilityIdentifier("task.result.stale")
            }
            Text("\(result.byteCount) bytes · SHA256 \(result.sha256)")
                .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
            ScrollView([.horizontal, .vertical]) {
                Text(result.patch.isEmpty ? L10n.workflow("noChanges") : result.patch)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color(nsColor: .textBackgroundColor))
            .accessibilityIdentifier("task.result.patch")
        } else if error == nil { ProgressView() }
        if let error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
    }

    @ViewBuilder
    private var summaryContent: some View {
        Text(L10n.workflow("summaryHelp")).font(.callout).foregroundStyle(.secondary)
        if let summary {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("\(L10n.workflow("delivery")): \(summary.completeness) · \(summary.verification.status)")
                        .font(.headline).accessibilityIdentifier("task.result.completeness")
                    if summary.verification.status == "VERIFICATION_STALE" {
                        Text(L10n.workflow("stale")).foregroundStyle(.orange)
                            .accessibilityIdentifier("task.result.stale")
                    }
                    Text(summary.goal).accessibilityIdentifier("task.result.goal")
                    Text("\(L10n.workflow("redacted")): \(summary.goalRedacted ? L10n.workflow("yes") : L10n.workflow("no")) · \(L10n.workflow("truncated")): \(summary.hasOmissions ? L10n.workflow("yes") : L10n.workflow("no"))")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(L10n.workflow("changedFiles")).font(.headline)
                    ForEach(Array(summary.changedFiles.enumerated()), id: \.offset) { _, path in
                        Text(path).font(.system(.body, design: .monospaced))
                            .accessibilityIdentifier("task.result.changedFile")
                    }
                    Text(L10n.workflow("checks")).font(.headline)
                    ForEach(Array(summary.tests.enumerated()), id: \.offset) { _, check in
                        Text("\(check.name) · \(check.status)")
                    }
                    DisclosureGroup(L10n.workflow("binding")) {
                        Text(summary.resultId)
                        Text(summary.binding.taskId)
                        if let evidence = summary.verification.evidenceId { Text(evidence) }
                        if let sha = summary.binding.verificationEvidenceSHA256 { Text(sha) }
                        if let sha = summary.binding.patchSHA256 { Text("Patch SHA256: \(sha)") }
                        ForEach(summary.limitations, id: \.self) { Text($0) }
                    }
                    .font(.system(.caption, design: .monospaced))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
            }
            .accessibilityIdentifier("task.result.summary")
        } else if summaryError == nil { ProgressView() }
        if let summaryError { Text(summaryError).foregroundStyle(.red).textSelection(.enabled) }
    }

    private func export() {
        guard canExport, let result else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = selectedEvidenceId.isEmpty
            ? "\(taskId).patch" : "\(taskId)-\(selectedEvidenceId).patch"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try result.patchData.write(to: url, options: .atomic)
            guard try Data(contentsOf: url) == result.patchData else {
                throw CocoaError(.fileWriteUnknown)
            }
            exported = true
        } catch { self.error = workflowError(error) }
    }
}

private func workflowError(_ error: Error) -> String {
    (error as? PAOClientError)?.displayDetail ?? L10n.workflow("unavailable")
}
