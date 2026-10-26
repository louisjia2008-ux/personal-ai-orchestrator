// Compiled into an isolated child executable by SocketSigpipeTests. Interpose
// only this child's syscalls to order real peer closure before connect/accept
// returns; no sleeps or process-wide SIGPIPE ignore can hide the regression.
import Darwin
import Foundation
@testable import PAOControlKit

private typealias Connect = @convention(c) (Int32, UnsafePointer<sockaddr>?, socklen_t) -> Int32
private typealias Accept = @convention(c) (Int32, UnsafeMutablePointer<sockaddr>?, UnsafeMutablePointer<socklen_t>?) -> Int32
private typealias Close = @convention(c) (Int32) -> Int32
private typealias SetOption = @convention(c) (Int32, Int32, Int32, UnsafeRawPointer?, socklen_t) -> Int32
private let nextImage = UnsafeMutableRawPointer(bitPattern: -1)
private let systemConnect = unsafeBitCast(dlsym(nextImage, "connect")!, to: Connect.self)
private let systemAccept = unsafeBitCast(dlsym(nextImage, "accept")!, to: Accept.self)
private let systemClose = unsafeBitCast(dlsym(nextImage, "close")!, to: Close.self)
private let systemSetOption = unsafeBitCast(dlsym(nextImage, "setsockopt")!, to: SetOption.self)

private final class ProbeState: @unchecked Sendable {
    let lock = NSLock()
    let peerClosed = DispatchSemaphore(value: 0)
    let acceptedReady = DispatchSemaphore(value: 0)
    let acceptedClosed = DispatchSemaphore(value: 0)
    var mode = ""
    var acceptedFD: Int32 = -1
    var connectCalls = 0
    var protectedBeforeConnect = 0
    var protectedAtAccept = 0
    var lateOptionFailures = 0
}
private let probe = ProbeState()

private func require(_ value: Bool, _ message: String) {
    if !value { fputs("SIGPIPE_PROBE_FAILURE: \(message)\n", stderr); exit(71) }
}

private func protectedSocket(_ fd: Int32) -> Bool {
    var value: Int32 = 0
    var length = socklen_t(MemoryLayout<Int32>.size)
    return getsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &value, &length) == 0 && value == 1
}

@_cdecl("connect")
func paoProbeConnect(_ fd: Int32, _ address: UnsafePointer<sockaddr>?, _ length: socklen_t) -> Int32 {
    probe.lock.lock()
    probe.connectCalls += 1
    if protectedSocket(fd) { probe.protectedBeforeConnect += 1 }
    let mode = probe.mode
    probe.lock.unlock()
    let result = systemConnect(fd, address, length)
    if mode == "client-close", result == 0 {
        require(probe.peerClosed.wait(timeout: .now() + 3) == .success, "client peer did not close")
    }
    return result
}

@_cdecl("accept")
func paoProbeAccept(_ fd: Int32, _ address: UnsafeMutablePointer<sockaddr>?, _ length: UnsafeMutablePointer<socklen_t>?) -> Int32 {
    let result = systemAccept(fd, address, length)
    if result >= 0, probe.mode == "server-close" {
        probe.lock.lock()
        probe.acceptedFD = result
        if protectedSocket(result) { probe.protectedAtAccept += 1 }
        probe.lock.unlock()
        probe.acceptedReady.signal()
        require(probe.peerClosed.wait(timeout: .now() + 3) == .success, "server peer did not close")
    }
    return result
}

@_cdecl("close")
func paoProbeClose(_ fd: Int32) -> Int32 {
    let result = systemClose(fd)
    probe.lock.lock()
    let accepted = fd == probe.acceptedFD && fd >= 0
    if accepted { probe.acceptedFD = -1 }
    probe.lock.unlock()
    if accepted { probe.acceptedClosed.signal() }
    return result
}

@_cdecl("setsockopt")
func paoProbeSetOption(_ fd: Int32, _ level: Int32, _ option: Int32, _ value: UnsafeRawPointer?, _ length: socklen_t) -> Int32 {
    if level == SOL_SOCKET, option == SO_NOSIGPIPE,
       probe.mode == "client-option-failure" || probe.mode == "server-option-failure" {
        errno = EACCES
        return -1
    }
    let result = systemSetOption(fd, level, option, value, length)
    if result != 0, level == SOL_SOCKET, option == SO_NOSIGPIPE {
        let code = errno
        probe.lock.lock(); probe.lateOptionFailures += 1; probe.lock.unlock()
        fputs("SIGPIPE_PROBE_OPTION_FAILURE errno=\(code)\n", stderr)
        errno = code
    }
    return result
}

private func socketAddress(_ path: String) -> sockaddr_un {
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8)
    require(bytes.count < MemoryLayout.size(ofValue: address.sun_path), "path too long")
    withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes) }
    return address
}

private func bindListener(_ path: String) -> Int32 {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    require(fd >= 0, "listener socket")
    var address = socketAddress(path)
    let bound = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    require(bound == 0 && listen(fd, 8) == 0, "bind/listen")
    return fd
}

private func clientClose(_ path: String) throws {
    let listener = bindListener(path)
    defer { _ = systemClose(listener); unlink(path) }
    let done = DispatchSemaphore(value: 0)
    DispatchQueue.global().async {
        let fd = systemAccept(listener, nil, nil)
        require(fd >= 0, "client scenario accept")
        _ = systemClose(fd)
        probe.peerClosed.signal()
        done.signal()
    }
    do {
        _ = try PAOSocket.sendSync(socketPath: path, request: Data("GET / HTTP/1.0\r\n\r\n".utf8), timeoutSeconds: 1)
        require(false, "closed client peer was accepted as success")
    } catch let error as PAOClientError {
        require(error == .transportFailure, "closed client peer error mapping")
    }
    require(done.wait(timeout: .now() + 3) == .success, "client scenario cleanup")
}

private func serverClose(_ path: String) throws {
    let daemon = TestDaemon()
    daemon.route("GET", "/", body: "{}")
    try daemon.start(socketPath: path)
    defer { daemon.stop(); unlink(path) }
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    require(fd >= 0, "server scenario client socket")
    var address = socketAddress(path)
    let connected = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            systemConnect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    require(connected == 0, "server scenario connect")
    let request = Array("GET / HTTP/1.0\r\n\r\n".utf8)
    require(request.withUnsafeBytes { Darwin.write(fd, $0.baseAddress!, $0.count) } == request.count, "server scenario request")
    require(probe.acceptedReady.wait(timeout: .now() + 3) == .success, "server accept was not observed")
    _ = systemClose(fd)
    probe.peerClosed.signal()
    require(probe.acceptedClosed.wait(timeout: .now() + 3) == .success, "server did not close rejected peer")
}

@main
private enum Main {
    static func main() throws {
        signal(SIGPIPE, SIG_DFL)
        require(CommandLine.arguments.count == 2, "scenario required")
        probe.mode = CommandLine.arguments[1]
        let path = "/private/tmp/pao-sigpipe-\(getpid()).sock"
        defer { unlink(path) }
        switch probe.mode {
        case "client-close":
            for _ in 0..<20 { try clientClose(path) }
            require(probe.protectedBeforeConnect == 20, "client protection must precede connect")
        case "server-close":
            for _ in 0..<20 { try serverClose(path) }
            require(probe.protectedAtAccept == 20, "accepted sockets must inherit listener protection")
            require(probe.lateOptionFailures == 20, "closed-peer option failure was not exercised")
        case "client-option-failure":
            do {
                _ = try PAOSocket.sendSync(socketPath: path, request: Data([1]), timeoutSeconds: 1)
                require(false, "option failure accepted")
            } catch let error as PAOClientError {
                require(error == .transportFailure, "option failure must be transportFailure")
            }
            require(probe.connectCalls == 0, "option failure must prevent connect")
        case "server-option-failure":
            do { try TestDaemon().start(socketPath: path); require(false, "listener option failure accepted") }
            catch { require((error as NSError).code == Int(EACCES), "listener must preserve option errno") }
            require(access(path, F_OK) != 0, "option failure must prevent bind")
        case "roundtrip":
            let daemon = TestDaemon()
            daemon.route("GET", "/v1/health", body: healthBody)
            try daemon.start(socketPath: path)
            defer { daemon.stop() }
            for _ in 0..<20 {
                let request = PAOHTTP.encodeRequest(method: "GET", path: "/v1/health", body: Data())
                let response = try PAOSocket.sendSync(socketPath: path, request: request, timeoutSeconds: 1)
                require(PAOHTTP.parseResponse(response)?.status == 200, "normal roundtrip failed")
            }
        default: require(false, "unknown scenario")
        }
        print("SIGPIPE_PROBE_PASSED \(probe.mode)")
    }
}
