import Foundation

/// Deterministic socket discovery without filesystem scanning.
public enum SocketDiscovery {
    public static let userDefaultsKey = "controlSocketPath"
    public static let environmentKey = "PAO_CONTROL_SOCKET"
    public static let defaultRelative = ".personal-ai-orchestrator/control.sock"

    public static let maximumPathBytes = 104

    /// Resolution order: explicit UserDefaults override, then environment, then default
    /// location under the user's home directory.
    public static func resolve(userDefaults: UserDefaults = .standard,
                               environment: [String: String] = ProcessInfo.processInfo.environment) -> String {
        if let configured = userDefaults.string(forKey: userDefaultsKey), !configured.isEmpty {
            return configured
        }
        if let fromEnvironment = environment[environmentKey], !fromEnvironment.isEmpty {
            return fromEnvironment
        }
        return NSString(string: NSHomeDirectory()).appendingPathComponent(defaultRelative)
    }

    public enum Validation: Equatable {
        case valid
        case tooLong(length: Int)
        case notAbsolute

        public func validate() -> Bool {
            switch self {
            case .valid: return true
            default: return false
            }
        }
    }

    public static func validate(path: String) -> Validation {
        guard path.hasPrefix("/") else { return .notAbsolute }
        let length = path.utf8.count
        guard length <= maximumPathBytes else { return .tooLong(length: length) }
        return .valid
    }
}
