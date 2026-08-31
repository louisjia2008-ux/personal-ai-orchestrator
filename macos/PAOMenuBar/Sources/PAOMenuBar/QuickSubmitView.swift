import SwiftUI

import PAOControlKit

struct QuickSubmitView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var intent: String = ""
    @State private var submitting: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(L10n.quickSubmit).font(.subheadline).foregroundStyle(.secondary)
            HStack {
                TextField(L10n.submitPlaceholder, text: $intent)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submit)
                Button(submitting ? "…" : L10n.submit, action: submit)
                    .disabled(submitting || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
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
    }

    private func submit() {
        let value = intent
        submitting = true
        Task {
            await store.quickSubmit(intent: value)
            submitting = false
            if store.lastSubmittedTaskId != nil {
                intent = ""
            }
        }
    }
}
