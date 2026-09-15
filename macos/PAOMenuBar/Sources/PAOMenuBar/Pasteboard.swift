import AppKit

import PAOControlKit

/// Sanitized clipboard helper. Plain NSPasteboard writes do not require TCC
/// accessibility or screen-recording permissions. Task intent or credentials
/// never pass through this helper; current callers copy explicit identifiers.
enum Pasteboard {
    static func copy(_ value: String) {
        guard !value.isEmpty else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(value, forType: .string)
        ClientLog.operation("clipboard", outcome: "copy")
    }
}
