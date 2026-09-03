import AppKit
import SwiftUI

/// One semantic vocabulary for every status the dashboard renders.
///
/// Before this existed the same semantics were mapped independently in five
/// places — badge kinds, risk rows, the state-distribution chart, lifecycle
/// phases, and the menu bar — so BLOCKED could be red in one view and secondary
/// in another. Status meaning now resolves in exactly one place.
///
/// Two rules the type enforces rather than documents:
///
/// - Colour is never the only channel. Every presentation carries a symbol, so
///   the status survives greyscale, colour-blindness and Increase Contrast.
/// - UNKNOWN is not healthy. It resolves to its own tone with a questioning
///   symbol, never to `positive` and never to the same treatment as "fine".
///
/// Raw protocol values (`RUNNING`, `EXACT`, `EXHAUSTED_OBSERVED`, ...) are not
/// translated here. This layer decides tone and symbol; the machine value keeps
/// being displayed verbatim, as it is everywhere else in the client.
public enum StatusTone: Equatable, Sendable, CaseIterable {
    /// Working as intended, or finished successfully.
    case positive
    /// Needs attention but is not a failure.
    case caution
    /// Failed, blocked, or exhausted.
    case critical
    /// In progress, or simply informational.
    case neutral
    /// Truth not established. Deliberately distinct from `neutral`, because
    /// "we do not know" must not look like "nothing to report".
    case unknown

    /// System colours rather than literals: they adapt to light/dark and respond
    /// to Increase Contrast, which fixed hex values cannot.
    public var color: Color {
        switch self {
        case .positive: return Color(nsColor: .systemGreen)
        case .caution: return Color(nsColor: .systemOrange)
        case .critical: return Color(nsColor: .systemRed)
        case .neutral: return Color(nsColor: .secondaryLabelColor)
        case .unknown: return Color(nsColor: .tertiaryLabelColor)
        }
    }

    /// Fill for meters and chart marks, where a full-strength status colour
    /// would overpower the surrounding type.
    public var fillColor: Color {
        color.opacity(self == .neutral || self == .unknown ? 0.55 : 0.75)
    }
}

/// Tone plus the symbol that carries the same meaning without colour.
public struct StatusPresentation: Equatable, Sendable {
    public let tone: StatusTone
    public let symbol: String

    public init(tone: StatusTone, symbol: String) {
        self.tone = tone
        self.symbol = symbol
    }

    public var color: Color { tone.color }
}

public enum StatusStyle {

    // MARK: - Task state

    /// Task lifecycle states as reported by the daemon.
    ///
    /// An unrecognized state resolves to `.unknown`, never to `.positive`: a
    /// state this build has not seen must not be presented as success.
    public static func task(state: String) -> StatusPresentation {
        switch state {
        case "RUNNING", "VERIFYING":
            return StatusPresentation(tone: .neutral, symbol: "gearshape.2")
        case "SUBMITTED", "READY", "PREPARING":
            return StatusPresentation(tone: .caution, symbol: "tray")
        case "VERIFIED", "COMPLETED", "ACCEPTED", "INTEGRATED":
            return StatusPresentation(tone: .positive, symbol: "checkmark.circle")
        case "BLOCKED":
            return StatusPresentation(tone: .critical, symbol: "exclamationmark.octagon")
        case "FAILED":
            return StatusPresentation(tone: .critical, symbol: "xmark.octagon")
        case "CANCELLED":
            return StatusPresentation(tone: .neutral, symbol: "slash.circle")
        default:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    // MARK: - Risk severity

    public static func severity(_ severity: String) -> StatusPresentation {
        switch severity {
        case "BLOCKED":
            return StatusPresentation(tone: .critical, symbol: "xmark.octagon.fill")
        case "WARNING":
            return StatusPresentation(tone: .caution, symbol: "exclamationmark.triangle.fill")
        case "UNKNOWN":
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle.fill")
        default:
            return StatusPresentation(tone: .neutral, symbol: "info.circle.fill")
        }
    }

    // MARK: - Quota

    /// Measurement confidence. UNKNOWN never renders as a healthy reading — the
    /// quota surface depends on that distinction being visible.
    public static func quotaConfidence(_ confidence: String) -> StatusPresentation {
        switch confidence {
        case "EXACT":
            return StatusPresentation(tone: .positive, symbol: "checkmark.seal")
        case "ESTIMATED":
            return StatusPresentation(tone: .caution, symbol: "chart.line.uptrend.xyaxis")
        default:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    /// Observed availability of a quota pool or execution target.
    public static func quotaState(_ state: String) -> StatusPresentation {
        switch state {
        case "AVAILABLE", "RECOVERED":
            return StatusPresentation(tone: .positive, symbol: "checkmark.circle")
        case "LIMITED":
            return StatusPresentation(tone: .caution, symbol: "exclamationmark.triangle")
        case "EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN":
            return StatusPresentation(tone: .critical, symbol: "battery.0percent")
        default:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    // MARK: - Provider connection

    public static func connection(_ state: String) -> StatusPresentation {
        switch state {
        case "CONNECTED":
            return StatusPresentation(tone: .positive, symbol: "link")
        case "DISCONNECTED", "NOT_CONNECTED":
            return StatusPresentation(tone: .neutral, symbol: "link.badge.plus")
        case "ERROR", "AUTH_FAILED":
            return StatusPresentation(tone: .critical, symbol: "exclamationmark.triangle")
        default:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    // MARK: - Routing roles

    /// A routing role's lifecycle status, before any outcome is known.
    public static func routingStatus(_ status: RoutingRoleStatus) -> StatusPresentation {
        switch status {
        case .unassigned:
            return StatusPresentation(tone: .neutral, symbol: "circle.dashed")
        case .assigned:
            return StatusPresentation(tone: .neutral, symbol: "circle")
        case .running:
            return StatusPresentation(tone: .neutral, symbol: "gearshape.2")
        case .completed:
            return StatusPresentation(tone: .positive, symbol: "checkmark.circle")
        case .cancelled:
            return StatusPresentation(tone: .neutral, symbol: "slash.circle")
        case .unknown:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    /// A role's presentation once status and outcome are both considered.
    ///
    /// The pair is resolved here so no view has to remember that COMPLETED+FAIL
    /// is a verdict rather than a breakage: a review that rejected the work is
    /// `caution`, while a review that could not run at all is `critical`.
    public static func routingRole(
        status: RoutingRoleStatus, outcome: RoutingOutcome
    ) -> StatusPresentation {
        guard status == .completed else {
            return routingStatus(status)
        }
        switch outcome {
        case .pass:
            return StatusPresentation(tone: .positive, symbol: "checkmark.circle")
        case .fail:
            return StatusPresentation(tone: .caution, symbol: "hand.thumbsdown")
        case .error:
            return StatusPresentation(tone: .critical, symbol: "exclamationmark.triangle")
        case .none:
            return StatusPresentation(tone: .neutral, symbol: "checkmark.circle")
        case .unknown:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    // MARK: - Verification

    public static func verification(_ status: String) -> StatusPresentation {
        switch status {
        case "VERIFIED", "PASSED":
            return StatusPresentation(tone: .positive, symbol: "checkmark.seal")
        case "FAILED":
            return StatusPresentation(tone: .critical, symbol: "xmark.seal")
        case "NOT_VERIFIED":
            return StatusPresentation(tone: .neutral, symbol: "seal")
        default:
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
    }

    /// One verifier stage. Exit code is the whole truth here.
    public static func verificationStage(passed: Bool) -> StatusPresentation {
        passed
            ? StatusPresentation(tone: .positive, symbol: "checkmark.circle")
            : StatusPresentation(tone: .critical, symbol: "xmark.circle")
    }

    // MARK: - Lifecycle phases

    /// A step in the task lifecycle. `current` outranks `complete` so the eye
    /// lands on where the work actually is.
    public static func lifecyclePhase(
        isComplete: Bool, isCurrent: Bool
    ) -> StatusPresentation {
        if isCurrent {
            return StatusPresentation(tone: .neutral, symbol: "circle.dotted")
        }
        return isComplete
            ? StatusPresentation(tone: .positive, symbol: "checkmark.circle.fill")
            : StatusPresentation(tone: .unknown, symbol: "circle")
    }
}
