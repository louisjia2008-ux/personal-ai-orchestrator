import Darwin
import Foundation

struct DaemonProcessIdentity: Equatable, Sendable {
    let pid: Int32
    let executablePath: String
}

enum DaemonProcessOwnershipError: Error, Equatable {
    case socketOwnerUnavailable
    case executableUnavailable(pid: Int32)
    case executableMismatch
    case signalFailed
}

enum DaemonProcessOwnership {
    static func normalizedPath(_ path: String) -> String {
        URL(fileURLWithPath: path)
            .resolvingSymlinksInPath()
            .standardizedFileURL
            .path
    }

    /// Resolve the exact process that owns the daemon's connected Unix socket.
    ///
    /// This uses the kernel's LOCAL_PEERPID identity rather than argv/process
    /// scanning, so a similarly named process can never be selected.
    static func socketPeerPID(at socketPath: String) -> Int32? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(socketPath.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: address.sun_path) else {
            return nil
        }
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.initializeMemory(as: UInt8.self, repeating: 0)
            raw.copyBytes(from: bytes)
        }
        let connected = withUnsafePointer(to: &address) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
                Darwin.connect(
                    fd,
                    socketAddress,
                    socklen_t(MemoryLayout<sockaddr_un>.size)
                )
            }
        }
        guard connected == 0 else { return nil }

        var peerPID = pid_t(0)
        var peerPIDLength = socklen_t(MemoryLayout<pid_t>.size)
        let result = withUnsafeMutablePointer(to: &peerPID) { pointer in
            getsockopt(
                fd,
                SOL_LOCAL,
                LOCAL_PEERPID,
                pointer,
                &peerPIDLength
            )
        }
        guard result == 0, peerPID > 0 else { return nil }
        return Int32(peerPID)
    }

    static func executablePath(pid: Int32) -> String? {
        var buffer = [CChar](repeating: 0, count: 4096)
        let count = buffer.withUnsafeMutableBytes { raw -> Int32 in
            guard let base = raw.baseAddress else { return 0 }
            return proc_pidpath(pid, base, UInt32(raw.count))
        }
        guard count > 0 else { return nil }
        return String(cString: buffer)
    }

    static func validate(
        pid: Int32,
        expectedHelperPath: String,
        actualExecutablePath: String?
    ) throws -> DaemonProcessIdentity {
        guard let actualExecutablePath else {
            throw DaemonProcessOwnershipError.executableUnavailable(pid: pid)
        }
        let expected = normalizedPath(expectedHelperPath)
        let actual = normalizedPath(actualExecutablePath)
        guard expected == actual else {
            throw DaemonProcessOwnershipError.executableMismatch
        }
        return DaemonProcessIdentity(pid: pid, executablePath: actual)
    }

    static func validatedSocketOwner(
        socketPath: String,
        expectedHelperPath: String
    ) throws -> DaemonProcessIdentity {
        guard let pid = socketPeerPID(at: socketPath) else {
            throw DaemonProcessOwnershipError.socketOwnerUnavailable
        }
        return try validate(
            pid: pid,
            expectedHelperPath: expectedHelperPath,
            actualExecutablePath: executablePath(pid: pid)
        )
    }

    static func signalTermination(pid: Int32) throws {
        if kill(pid, SIGTERM) == 0 || errno == ESRCH {
            return
        }
        throw DaemonProcessOwnershipError.signalFailed
    }

    static func isAlive(pid: Int32) -> Bool {
        if kill(pid, 0) == 0 {
            return true
        }
        return errno != ESRCH
    }
}
