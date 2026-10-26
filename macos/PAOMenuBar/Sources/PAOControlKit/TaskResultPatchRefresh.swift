/// Reconcile immutable patch bytes with the latest host-owned result binding.
/// This policy never promotes a task or substitutes a worktree for accepted bytes.
public enum TaskResultPatchRefresh {
    public static func matches(
        _ patch: TaskPatchView, summary: CrossClientResultView,
        taskId: String, evidenceId: String = ""
    ) -> Bool {
        guard patch.taskId == taskId, summary.binding.taskId == taskId else { return false }
        if evidenceId.isEmpty {
            guard patch.evidenceId == nil else { return false }
        } else {
            guard patch.evidenceId == evidenceId,
                  summary.verification.evidenceId == evidenceId else { return false }
        }
        if patch.source == "VERIFIED_SNAPSHOT" {
            return summary.binding.patchSHA256 == patch.sha256
                && summary.binding.patchByteCount == patch.byteCount
        }
        return evidenceId.isEmpty && summary.binding.patchSHA256 == nil
            && summary.binding.patchByteCount == nil
    }

    public static func shouldRefresh(
        patch: TaskPatchView?, summary: CrossClientResultView,
        taskId: String, evidenceId: String = ""
    ) -> Bool {
        guard summary.binding.taskId == taskId,
              ["VERIFIED", "VERIFICATION_STALE", "HISTORICAL_VERIFIED"].contains(summary.verification.status),
              let digest = summary.binding.patchSHA256, digest.utf8.count == 64,
              digest.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              let count = summary.binding.patchByteCount, (0...1024 * 1024).contains(count),
              evidenceId.isEmpty || summary.verification.evidenceId == evidenceId else { return false }
        guard let patch else { return true }
        return patch.source != "VERIFIED_SNAPSHOT"
            || !matches(patch, summary: summary, taskId: taskId, evidenceId: evidenceId)
    }
}
