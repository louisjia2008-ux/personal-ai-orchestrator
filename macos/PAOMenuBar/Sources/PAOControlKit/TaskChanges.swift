import Foundation

/// What a task changed on disk.
///
/// The actual paths are already in the payload — `verification.result` carries
/// `changed_paths` and `unexpected_paths` — yet the client rendered only the
/// count. This projects the list the daemon already sends, and states plainly
/// when there is no list rather than printing a bare number.
///
/// What the payload does *not* carry is per-file change kind or diff statistics.
/// `changed_paths` is the union of `git diff --name-only <base>` and untracked
/// files, so "added" and "modified" are indistinguishable, and no insertion or
/// deletion counts exist anywhere in the record. Both are BACKEND_WORK_REQUIRED
/// (Phase A) and neither is invented here.

/// One changed path, plus whether the verifier considered it in scope.
public struct TaskChangedFile: Equatable, Identifiable, Sendable {
    public let path: String
    /// True when the verifier profile's allowed paths did not cover this file.
    /// A policy fact from the verifier, not a judgement made by the client.
    public let isUnexpected: Bool

    public var id: String { path }

    public init(path: String, isUnexpected: Bool) {
        self.path = path
        self.isUnexpected = isUnexpected
    }

    /// Last path component, for a list that shows the name and the folder apart.
    public var fileName: String {
        String(path.split(separator: "/").last ?? Substring(path))
    }

    /// Everything before the file name, or nil at the repository root.
    public var directory: String? {
        let components = path.split(separator: "/")
        guard components.count > 1 else { return nil }
        return components.dropLast().joined(separator: "/")
    }
}

/// Why no changed-file list is available.
public enum TaskChangesUnavailableReason: String, Equatable, Sendable {
    /// The verifier has not produced a result yet, so nothing has been measured.
    case verificationNotRun
    /// The task never got a worktree, so there is nothing to have changed.
    case noWorkspace
}

/// The changed-file projection for one task.
public enum TaskChangeSet: Equatable, Sendable {
    case unavailable(TaskChangesUnavailableReason)
    /// A measured list. May be empty: "the verifier found no changes" is a
    /// result, and it is not the same as "we never looked".
    case files([TaskChangedFile])

    public var files: [TaskChangedFile] {
        if case .files(let files) = self { return files }
        return []
    }

    public var unexpectedFiles: [TaskChangedFile] {
        files.filter(\.isUnexpected)
    }

    public var isMeasured: Bool {
        if case .files = self { return true }
        return false
    }

    public static func derive(from detail: TaskDetailView) -> TaskChangeSet {
        guard let result = detail.verification.result else {
            return detail.workspace == nil
                ? .unavailable(.noWorkspace)
                : .unavailable(.verificationNotRun)
        }
        let unexpected = Set(result.unexpectedPaths)
        // Unexpected paths are reported separately by the verifier and are not
        // guaranteed to appear in `changed_paths`; the union keeps an
        // out-of-scope write visible either way.
        let all = result.changedPaths + result.unexpectedPaths.filter {
            !result.changedPaths.contains($0)
        }
        return .files(
            all.map { TaskChangedFile(path: $0, isUnexpected: unexpected.contains($0)) }
        )
    }
}
