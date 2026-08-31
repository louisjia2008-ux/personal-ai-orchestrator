import Foundation

public struct AppSupportLayout: Equatable, Sendable {
    public let appSupportRoot: String
    public let runtimeConfigPath: String
    public let stateDatabasePath: String
    public let runtimeStateRoot: String
    public let logsRoot: String
    public let socketPath: String

    public static func resolve(homeDirectory: String = NSHomeDirectory()) -> AppSupportLayout {
        let home = NSString(string: homeDirectory)
        let appRoot = home.appendingPathComponent(
            "Library/Application Support/Personal AI Orchestrator"
        )
        let cacheRoot = home.appendingPathComponent(
            "Library/Caches/Personal AI Orchestrator"
        )
        return AppSupportLayout(
            appSupportRoot: appRoot,
            runtimeConfigPath: NSString(string: appRoot).appendingPathComponent("runtime.json"),
            stateDatabasePath: NSString(string: appRoot).appendingPathComponent("state.sqlite3"),
            runtimeStateRoot: NSString(string: appRoot).appendingPathComponent("runtime-state"),
            logsRoot: NSString(string: appRoot).appendingPathComponent("logs"),
            socketPath: NSString(string: cacheRoot).appendingPathComponent("control.sock")
        )
    }
}
