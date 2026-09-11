import Foundation

/// opencode keeps its API credentials at `~/.local/share/opencode/auth.json`.
///
/// The owner has already authorized opencode to talk to each provider they
/// subscribe to, so the keys in this file are exactly what the daemon's
/// quota collectors need to make a real, observed quota collection against
/// the provider's plan endpoint. Reading the file here turns what would
/// be a missing-credential, fail-closed dispatch into a quota-aware one,
/// without ever copying credential material through the wire or the UI.
///
/// The projection is explicit: every supported entry below maps one opencode
/// provider_id to the single environment variable name the daemon looks
/// for. An entry that the daemon does not recognise is dropped, so this
/// file can carry API keys for other tools without leaking them into the
/// daemon's namespace.
public enum OpencodeAuthSource {
    /// The standard opencode auth location. Exposed for tests.
    public static let defaultPath: String = {
        let home = NSHomeDirectory()
        return (home as NSString).appendingPathComponent(
            ".local/share/opencode/auth.json"
        )
    }()

    public struct Entry: Equatable, Sendable {
        public let providerId: String
        /// The environment variable name the daemon expects.
        public let environmentName: String
        public let key: String
    }

    /// Reads opencode auth.json from the given path and projects it to
    /// daemon-readable environment entries. Malformed input (bad JSON,
    /// unexpected value shapes) yields an empty result rather than an
    /// error: a missing or unreadable file means opencode is not
    /// configured, which is a perfectly honest state to surface.
    public static func read(from path: String = defaultPath) -> [Entry] {
        let url = URL(fileURLWithPath: path)
        guard let data = try? Data(contentsOf: url),
              let raw = try? JSONSerialization.jsonObject(with: data),
              let dictionary = raw as? [String: Any]
        else { return [] }

        var entries: [Entry] = []
        for (providerId, value) in dictionary {
            guard let entry = mapping(for: providerId),
                  let object = value as? [String: Any],
                  let key = object["key"] as? String,
                  !key.isEmpty
            else { continue }
            entries.append(Entry(
                providerId: providerId,
                environmentName: entry,
                key: key
            ))
        }
        return entries
    }

    /// Provider-id → environment-variable mapping. Add to this table when
    /// the daemon recognises a new quota collector.
    private static func mapping(for providerId: String) -> String? {
        switch providerId {
        case "zai-coding-plan":
            return "ZAI_API_KEY"
        case "minimax-cn-coding-plan",
             "minimax-coding-plan":
            return "MINIMAX_API_KEY"
        default:
            return nil
        }
    }
}
