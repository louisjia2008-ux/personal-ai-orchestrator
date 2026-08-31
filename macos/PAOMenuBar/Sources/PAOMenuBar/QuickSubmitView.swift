import SwiftUI

import PAOControlKit

struct QuickSubmitView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var intent: String = ""
    @State private var submitting: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Quick submit").font(.subheadline).foregroundStyle(.secondary)
            HStack {
                TextField("Task intent (stored as intent, never executed)", text: $intent)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submit)
                Button(submitting ? "…" : "Submit", action: submit)
                    .disabled(submitting || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if let taskId = store.lastSubmittedTaskId {
                Text("Authoritative task id: \(taskId)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.green)
            }
            if let notice = store.submitNotice {
                Text(notice).font(.caption2).foregroundStyle(.secondary)
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
