import Foundation

/// Non-blocking widget snapshot writer.
///
/// The App Group container write path can stall indefinitely when the local
/// container authorization layer is unhealthy. Because a frozen write must
/// never freeze the UI, every write runs on a utility task with a bounded
/// timeout. If the primary (App Group) bridge times out, the snapshot is
/// rewritten through the app-owned fallback bridge and the primary is
/// re-tried only after `retryInterval` seconds.
public final class WidgetSnapshotWriter: @unchecked Sendable {
    public enum Outcome: Equatable, Sendable {
        case ok
        case failed
        case primaryTimeoutFallbackActive
        case fallbackTimeout
    }

    public let primary: WidgetSnapshotBridge?
    public let fallback: WidgetSnapshotBridge
    public let timeoutSeconds: TimeInterval
    public let retryInterval: TimeInterval

    private let primaryWrite: (WidgetSnapshot) throws -> Void
    private let fallbackWrite: (WidgetSnapshot) throws -> Void
    private let lock = NSLock()
    private var attemptInFlight = false
    private var primaryBlockedUntil: Date?

    public init(
        primary: WidgetSnapshotBridge?,
        fallback: WidgetSnapshotBridge,
        timeoutSeconds: TimeInterval = 2.0,
        retryInterval: TimeInterval = 300.0
    ) {
        self.primary = primary
        self.fallback = fallback
        self.primaryWrite = primary.map { bridge in { try bridge.write($0) } } ?? { _ in }
        self.fallbackWrite = { try fallback.write($0) }
        self.timeoutSeconds = timeoutSeconds
        self.retryInterval = retryInterval
    }

    init(
        primaryWrite: ((WidgetSnapshot) throws -> Void)?,
        fallback: WidgetSnapshotBridge,
        timeoutSeconds: TimeInterval,
        retryInterval: TimeInterval
    ) {
        self.primary = nil
        self.fallback = fallback
        self.primaryWrite = primaryWrite ?? { _ in }
        self.fallbackWrite = { try fallback.write($0) }
        self.timeoutSeconds = timeoutSeconds
        self.retryInterval = retryInterval
    }

    public func write(_ snapshot: WidgetSnapshot) {
        let usePrimary: Bool
        lock.lock()
        if attemptInFlight {
            lock.unlock()
            return
        }
        attemptInFlight = true
        if let blockedUntil = primaryBlockedUntil, Date() < blockedUntil {
            usePrimary = false
        } else {
            usePrimary = true
            primaryBlockedUntil = nil
        }
        lock.unlock()

        let primaryAttempt = usePrimary ? primaryWrite : nil
        let fallbackAttempt = fallbackWrite
        let timeout = timeoutSeconds

        Task.detached(priority: .utility) { [weak self] in
            var outcome = Outcome.failed
            if let primaryAttempt {
                let primaryOutcome = await Self.timedWrite(
                    attempt: primaryAttempt, snapshot: snapshot, timeoutSeconds: timeout
                )
                switch primaryOutcome {
                case .ok:
                    outcome = .ok
                case .failed:
                    outcome = .failed
                case .timedOut:
                    let fallbackOutcome = await Self.timedWrite(
                        attempt: fallbackAttempt, snapshot: snapshot, timeoutSeconds: timeout
                    )
                    outcome = fallbackOutcome == .ok ? .primaryTimeoutFallbackActive : .fallbackTimeout
                }
            } else {
                let fallbackOutcome = await Self.timedWrite(
                    attempt: fallbackAttempt, snapshot: snapshot, timeoutSeconds: timeout
                )
                outcome = fallbackOutcome == .ok ? .ok : .fallbackTimeout
            }
            self?.finish(outcome: outcome, usedPrimary: primaryAttempt != nil)
        }
    }

    private func finish(outcome: Outcome, usedPrimary: Bool) {
        lock.lock()
        attemptInFlight = false
        if usedPrimary {
            switch outcome {
            case .primaryTimeoutFallbackActive, .fallbackTimeout:
                primaryBlockedUntil = Date().addingTimeInterval(retryInterval)
            case .ok, .failed:
                primaryBlockedUntil = nil
            }
        }
        lock.unlock()
        ClientLog.operation("widget_snapshot", outcome: logValue(outcome))
    }

    private func logValue(_ outcome: Outcome) -> String {
        switch outcome {
        case .ok: return "ok"
        case .failed: return "write_failed"
        case .primaryTimeoutFallbackActive: return "primary_timeout_fallback"
        case .fallbackTimeout: return "fallback_timeout"
        }
    }

    fileprivate enum AttemptOutcome: Sendable {
        case ok
        case failed
        case timedOut
    }

    private static func timedWrite(
        attempt: @escaping (WidgetSnapshot) throws -> Void,
        snapshot: WidgetSnapshot,
        timeoutSeconds: TimeInterval
    ) async -> AttemptOutcome {
        final class ResumeOnce: @unchecked Sendable {
            private let lock = NSLock()
            private var resumed = false

            func once(
                _ continuation: CheckedContinuation<AttemptOutcome, Never>,
                _ outcome: AttemptOutcome
            ) {
                lock.lock()
                defer { lock.unlock() }
                guard !resumed else { return }
                resumed = true
                continuation.resume(returning: outcome)
            }
        }
        return await withCheckedContinuation { continuation in
            let resume = ResumeOnce()
            Task.detached(priority: .utility) {
                do {
                    try attempt(snapshot)
                    resume.once(continuation, .ok)
                } catch {
                    resume.once(continuation, .failed)
                }
            }
            Task.detached(priority: .utility) {
                try? await Task.sleep(for: .seconds(timeoutSeconds))
                resume.once(continuation, .timedOut)
            }
        }
    }
}
