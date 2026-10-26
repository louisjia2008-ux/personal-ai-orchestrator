import Foundation

/// Drains a child's pipe independently of the main actor, retaining only a tail.
final class BoundedProcessOutput: @unchecked Sendable {
    let pipe = Pipe()
    private let limit: Int
    private let lock = NSLock()
    private var tail = Data()

    init(limit: Int = 64 * 1024) {
        self.limit = max(1, limit)
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            self?.append(data)
        }
    }

    private func append(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        tail.append(data.suffix(limit))
        if tail.count > limit { tail.removeFirst(tail.count - limit) }
    }

    func snapshot() -> Data {
        lock.lock()
        defer { lock.unlock() }
        return tail
    }

    deinit {
        pipe.fileHandleForReading.readabilityHandler = nil
    }
}
