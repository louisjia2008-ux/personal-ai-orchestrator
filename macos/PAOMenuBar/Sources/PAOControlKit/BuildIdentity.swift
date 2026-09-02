import Foundation

/// Which commit produced the running app.
///
/// The values are injected into the bundle's `Info.plist` at build time from
/// `git rev-parse HEAD` (see `scripts/build_app_bundle.sh`). Nothing here is
/// hard-coded: a build that cannot resolve Git identity reports `.unknown`
/// rather than a stale or invented commit, so a mismatch is always visible
/// instead of silently plausible.
public struct BuildIdentity: Equatable, Sendable {
    /// Info.plist keys carrying build identity into the shipped bundle.
    public enum InfoKey {
        public static let commit = "PAOBuildCommit"
        public static let configuration = "PAOBuildConfiguration"
        public static let timestamp = "PAOBuildTimestamp"
    }

    public static let unknownValue = "unknown"

    private static let fullSHALength = 40
    private static let shortSHALength = 7

    public let commitSHA: String
    public let configuration: String
    public let builtAt: String

    public init(commitSHA: String, configuration: String, builtAt: String) {
        self.commitSHA = commitSHA
        self.configuration = configuration
        self.builtAt = builtAt
    }

    /// Nothing could be resolved. Rendered verbatim so the owner sees the gap.
    public static let unknown = BuildIdentity(
        commitSHA: unknownValue,
        configuration: unknownValue,
        builtAt: unknownValue
    )

    /// A commit identifier is a 40-character lowercase hex string.
    public static func isSHA(_ value: String) -> Bool {
        guard value.count == fullSHALength else { return false }
        return value.allSatisfy { $0.isHexDigit && !$0.isUppercase }
    }

    public var isKnown: Bool { Self.isSHA(commitSHA) }

    /// Owner-facing abbreviation; an unresolved commit stays `unknown`.
    public var shortSHA: String {
        guard isKnown else { return Self.unknownValue }
        return String(commitSHA.prefix(Self.shortSHALength))
    }

    /// A build-setting placeholder survives verbatim when the variable was
    /// never defined; treat that as unresolved rather than as a commit.
    private static func sanitize(_ raw: Any?) -> String? {
        guard let text = raw as? String else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.hasPrefix("$("), trimmed != unknownValue else {
            return nil
        }
        return trimmed
    }

    /// Decode identity from a bundle's info dictionary.
    public static func from(infoDictionary: [String: Any]?) -> BuildIdentity {
        guard let infoDictionary else { return .unknown }
        guard let commit = sanitize(infoDictionary[InfoKey.commit])?.lowercased(),
              isSHA(commit)
        else {
            return .unknown
        }
        return BuildIdentity(
            commitSHA: commit,
            configuration: sanitize(infoDictionary[InfoKey.configuration]) ?? unknownValue,
            builtAt: sanitize(infoDictionary[InfoKey.timestamp]) ?? unknownValue
        )
    }

    /// Identity of the currently running executable's bundle.
    public static let current = BuildIdentity.from(infoDictionary: Bundle.main.infoDictionary)
}

/// Whether the dashboard and the daemon came from the same source revision.
///
/// A current UI reading a stale daemon renders stale truth as if it were live,
/// so the comparison is surfaced rather than assumed.
public enum BuildCompatibility: Equatable, Sendable {
    /// Both sides resolved to the same commit.
    case matched
    /// Both sides resolved, and they disagree.
    case mismatched(app: String, daemon: String)
    /// At least one side could not state its commit; nothing can be concluded.
    case indeterminate

    public var isMismatch: Bool {
        if case .mismatched = self { return true }
        return false
    }

    /// Compare an app identity against a daemon-reported commit.
    public static func compare(app: BuildIdentity, daemonCommitSHA: String?) -> BuildCompatibility {
        guard let daemonCommitSHA else { return .indeterminate }
        let daemon = daemonCommitSHA.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard app.isKnown, BuildIdentity.isSHA(daemon) else { return .indeterminate }
        return app.commitSHA == daemon
            ? .matched
            : .mismatched(app: app.commitSHA, daemon: daemon)
    }
}
