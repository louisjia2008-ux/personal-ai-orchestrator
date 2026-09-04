import Darwin
import Foundation

public enum DaemonLifecycleStatus: Equatable, Sendable {
    case unknown
    case alreadyRunning
    case starting
    case startedByApp(pid: Int32)
    case healthyStartedByApp(pid: Int32)
    case healthyPreexisting
    case failed(reason: String)
    case exited(status: Int32)
    case versionMismatch(version: String)
    /// A running daemon speaks a compatible API but was built from a different
    /// commit than this app. Adopting it renders stale truth as live truth.
    case buildMismatch(app: String, daemon: String)

    public var displayValue: String {
        switch self {
        case .unknown: return "UNKNOWN"
        case .alreadyRunning: return "DAEMON_ALREADY_RUNNING"
        case .starting: return "DAEMON_STARTING"
        case .startedByApp: return "DAEMON_STARTED_BY_APP"
        case .healthyStartedByApp: return "DAEMON_HEALTHY_STARTED_BY_APP"
        case .healthyPreexisting: return "DAEMON_HEALTHY_PREEXISTING"
        case .failed: return "DAEMON_FAILED"
        case .exited: return "DAEMON_EXITED"
        case .versionMismatch: return "DAEMON_VERSION_MISMATCH"
        case .buildMismatch: return "DAEMON_BUILD_MISMATCH"
        }
    }

    public var logValue: String {
        switch self {
        case .failed(let reason): return "DAEMON_FAILED:\(reason)"
        case .exited(let status): return "DAEMON_EXITED:\(status)"
        case .versionMismatch(let version): return "DAEMON_VERSION_MISMATCH:\(version)"
        case .buildMismatch(let app, let daemon):
            return "DAEMON_BUILD_MISMATCH:app=\(app),daemon=\(daemon)"
        default: return displayValue
        }
    }
}

@MainActor
public final class DaemonLifecycleController: ObservableObject {
    @Published public private(set) var status: DaemonLifecycleStatus = .unknown {
        didSet {
            guard oldValue.logValue != status.logValue else { return }
            ClientLog.operation("daemon_lifecycle", outcome: status.logValue)
        }
    }

    private let configuration: DaemonLaunchConfiguration
    private let client: PAOControlClient
    private var ownedProcess: Process?
    private var startupTask: Task<Void, Never>?

    public init(configuration: DaemonLaunchConfiguration, client: PAOControlClient) {
        self.configuration = configuration
        self.client = client
    }

    public func ensureStarted() {
        guard startupTask == nil else { return }
        startupTask = Task {
            await startIfNeeded()
        }
    }

    public func ownsRunningDaemon() -> Bool {
        guard let ownedProcess else { return false }
        return ownedProcess.isRunning
    }

    private func startIfNeeded() async {
        if await healthIsCompatible() {
            status = .healthyPreexisting
            startupTask = nil
            return
        }
        guard case .valid = configuration.socketValidation else {
            status = .failed(reason: "socket_path_invalid")
            startupTask = nil
            return
        }

        let lockPath = URL(fileURLWithPath: configuration.layout.appSupportRoot)
            .appendingPathComponent("daemon-start.lock")
            .path
        do {
            try await withExclusiveLock(path: lockPath) {
                if await self.healthIsCompatible() {
                    await MainActor.run { self.status = .alreadyRunning }
                    return
                }
                if self.preexistingDaemonWasRefusedForBuild() {
                    await self.terminateStaleHelpers()
                }
                try await self.launchBundledDaemon()
            }
        } catch {
            status = .failed(reason: sanitizedReason(error))
        }
        startupTask = nil
    }

    /// A daemon is alive and speaking our API, but came from a different
    /// build: it was refused, so it is a leftover from an earlier app
    /// generation rather than a daemon the owner runs deliberately.
    private func preexistingDaemonWasRefusedForBuild() -> Bool {
        if case .buildMismatch = status { return true }
        return false
    }

    /// Terminate daemon helpers left behind by an earlier app generation.
    ///
    /// Without this, every app rebuild strands the old helper: it keeps
    /// serving from the unlinked socket inode while the new helper binds
    /// the path — two processes writing one state database. Only this
    /// bundle's own absolute helper path is ever signaled, so nothing
    /// outside this app's product can be matched.
    private func terminateStaleHelpers() async {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        process.arguments = ["-f", configuration.helperURL.path]
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            // Best effort: a failed cleanup must not block launching ours.
        }
        // Give the exited helpers a moment to release the socket and
        // sqlite handles before the new daemon binds the same path.
        try? await Task.sleep(for: .milliseconds(500))
    }

    private func healthIsCompatible() async -> Bool {
        do {
            let health = try await client.health()
            guard health.isCompatible else {
                status = .versionMismatch(version: health.apiVersion)
                return false
            }
            return await buildIsCompatible()
        } catch {
            return false
        }
    }

    /// Whether a reachable daemon came from this app's own commit.
    ///
    /// API-version compatibility is not enough. A daemon left over from an
    /// earlier checkout usually speaks the same API, so version alone lets a
    /// freshly built app quietly adopt a stale daemon and present its answers
    /// as current — the exact defect the deterministic launcher exists to close.
    ///
    /// Only a *known* mismatch is refused. When either side cannot state its
    /// commit, nothing can be concluded, and refusing on that basis would make
    /// an unstamped development build unable to start at all.
    private func buildIsCompatible() async -> Bool {
        let daemonCommit: String?
        do {
            daemonCommit = try await client.build().commitSHA
        } catch {
            // A daemon that cannot answer /v1/build predates the endpoint;
            // that is indeterminate, not a mismatch.
            return true
        }
        let compatibility = BuildCompatibility.compare(
            app: BuildIdentity.current,
            daemonCommitSHA: daemonCommit
        )
        guard case .mismatched(let app, let daemon) = compatibility else { return true }
        status = .buildMismatch(app: app, daemon: daemon)
        return false
    }

    private func launchBundledDaemon() async throws {
        guard configuration.helperExists else {
            throw LifecycleError.helperMissing
        }
        try FileManager.default.createDirectory(
            atPath: configuration.layout.appSupportRoot,
            withIntermediateDirectories: true
        )
        status = .starting
        let process = Process()
        process.executableURL = configuration.helperURL
        process.arguments = configuration.arguments
        process.currentDirectoryURL = URL(fileURLWithPath: configuration.layout.appSupportRoot)
        let stderr = Pipe()
        process.standardError = stderr
        process.terminationHandler = { [weak self] process in
            Task { @MainActor [weak self] in
                if self?.ownedProcess === process {
                    self?.status = .exited(status: process.terminationStatus)
                }
            }
        }
        try process.run()
        ownedProcess = process
        status = .startedByApp(pid: process.processIdentifier)

        let deadline = Date().addingTimeInterval(15.0)
        while Date() < deadline {
            if await healthIsCompatible() {
                status = .healthyStartedByApp(pid: process.processIdentifier)
                return
            }
            if !process.isRunning {
                throw LifecycleError.exitedEarly(process.terminationStatus)
            }
            try await Task.sleep(for: .milliseconds(150))
        }
        let stderrData = stderr.fileHandleForReading.availableData
        throw LifecycleError.startupTimeout(stderrData.isEmpty ? nil : stderrData)
    }

    private func withExclusiveLock<T>(
        path: String,
        operation: @escaping () async throws -> T
    ) async throws -> T {
        try FileManager.default.createDirectory(
            atPath: (path as NSString).deletingLastPathComponent,
            withIntermediateDirectories: true
        )
        let fd = open(path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw LifecycleError.lockUnavailable }
        defer { close(fd) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { throw LifecycleError.lockUnavailable }
        defer { flock(fd, LOCK_UN) }
        return try await operation()
    }

    private enum LifecycleError: Error {
        case helperMissing
        case lockUnavailable
        case exitedEarly(Int32)
        case startupTimeout(Data?)
    }

    private func sanitizedReason(_ error: Error) -> String {
        switch error {
        case LifecycleError.helperMissing:
            return "helper_missing"
        case LifecycleError.lockUnavailable:
            return "startup_lock_unavailable"
        case LifecycleError.exitedEarly(let status):
            return "helper_exited_\(status)"
        case LifecycleError.startupTimeout(let data):
            return sanitizedStderr(data)
        default:
            return "startup_failed"
        }
    }

    private func sanitizedStderr(_ data: Data?) -> String {
        guard let data, let text = String(data: data, encoding: .utf8) else {
            return "startup_timeout"
        }
        if text.contains("runtime_config_invalid") {
            return "runtime_config_invalid"
        }
        return "startup_timeout"
    }
}
