import Foundation

public struct DaemonLaunchConfiguration: Equatable, Sendable {
    public let pythonExecutable: String
    public let layout: AppSupportLayout
    public let host: String
    public let port: Int

    public init(
        pythonExecutable: String = "/usr/bin/env",
        layout: AppSupportLayout = .resolve(),
        host: String = "127.0.0.1",
        port: Int = 8765
    ) {
        self.pythonExecutable = pythonExecutable
        self.layout = layout
        self.host = host
        self.port = port
    }

    public var arguments: [String] {
        var values: [String] = []
        if pythonExecutable == "/usr/bin/env" {
            values.append("python")
        }
        values.append(contentsOf: [
            "-m", "personal_ai_orchestrator.daemon",
            "--config", layout.runtimeConfigPath,
            "--state-db", layout.stateDatabasePath,
            "--runtime-state-root", layout.runtimeStateRoot,
            "--control-socket", layout.socketPath,
            "--host", host,
            "--port", "\(port)",
        ])
        return values
    }

    public var socketValidation: SocketDiscovery.Validation {
        SocketDiscovery.validate(path: layout.socketPath)
    }
}
