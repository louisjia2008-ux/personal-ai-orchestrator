import Foundation

public struct DaemonLaunchConfiguration: Equatable, Sendable {
    public let layout: AppSupportLayout
    public let helperURL: URL
    public let host: String
    public let port: Int
    public let homeDirectory: String?

    public init(
        layout: AppSupportLayout = .resolve(),
        helperURL: URL = Bundle.main.bundleURL
            .appendingPathComponent("Contents")
            .appendingPathComponent("Helpers")
            .appendingPathComponent("pao-daemon"),
        host: String = "127.0.0.1",
        port: Int = 8765,
        homeDirectory: String? = nil
    ) {
        self.layout = layout
        self.helperURL = helperURL
        self.host = host
        self.port = port
        self.homeDirectory = homeDirectory
    }

    public var arguments: [String] {
        homeDirectory.map { ["--home", $0, "--host", host, "--port", String(port)] } ?? []
    }

    public var socketValidation: SocketDiscovery.Validation {
        SocketDiscovery.validate(path: layout.socketPath)
    }

    public var helperExists: Bool {
        FileManager.default.isExecutableFile(atPath: helperURL.path)
    }
}
