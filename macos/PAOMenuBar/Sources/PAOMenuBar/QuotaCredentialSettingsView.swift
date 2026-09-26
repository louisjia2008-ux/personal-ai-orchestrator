import PAOControlKit
import SwiftUI

/// Owner-mediated bridge for quota telemetry when execution auth exists only in Pi.
///
/// This surface never reads Pi credentials. The owner explicitly pastes a provider
/// API credential, which PAO stores in its own macOS Keychain namespace. The daemon
/// reads that slot only while calling the documented read-only quota endpoint.
struct QuotaCredentialSettingsCard: View {
    @EnvironmentObject private var store: OrchestratorStore

    private let keychain = QuotaCredentialKeychain()
    @State private var secrets: [QuotaCredentialSlot: String] = [:]
    @State private var authorized: Set<QuotaCredentialSlot> = []
    @State private var notice: String?

    var body: some View {
        DashboardCard(title: "Quota API credentials", symbol: "key.fill") {
            Text(
                "Execution login and quota telemetry are separate. PAO never copies a Pi login. " +
                "Add a provider API credential here only if quota remains UNKNOWN while Pi can run the model."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            ForEach(QuotaCredentialSlot.allCases) { slot in
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(slot.displayName)
                                .font(.callout.weight(.medium))
                            Text(authorized.contains(slot) ? "Quota credential authorized" : "No PAO quota credential")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if authorized.contains(slot) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                                .accessibilityLabel("Authorized")
                        }
                    }

                    SecureField(
                        "Provider API credential",
                        text: secretBinding(for: slot)
                    )
                    .textFieldStyle(.roundedBorder)
                    .privacySensitive()

                    HStack {
                        Button("Save to Keychain") {
                            save(slot)
                        }
                        .disabled(secretValue(for: slot).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        Button("Revoke", role: .destructive) {
                            revoke(slot)
                        }
                        .disabled(!authorized.contains(slot))

                        Spacer()
                    }
                    .controlSize(.small)
                }
            }

            if let notice {
                Text(notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(
                "The credential is used only for read-only quota collection. " +
                "Adding or revoking it does not change Pi execution authentication."
            )
            .font(.caption)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
        }
        .onAppear {
            refreshPresence()
        }
    }

    private func secretBinding(for slot: QuotaCredentialSlot) -> Binding<String> {
        Binding(
            get: { secretValue(for: slot) },
            set: { secrets[slot] = $0 }
        )
    }

    private func secretValue(for slot: QuotaCredentialSlot) -> String {
        secrets[slot] ?? ""
    }

    private func refreshPresence() {
        authorized = Set(
            QuotaCredentialSlot.allCases.filter { keychain.isAuthorized($0) }
        )
    }

    private func save(_ slot: QuotaCredentialSlot) {
        do {
            try keychain.store(secretValue(for: slot), for: slot)
            secrets[slot] = ""
            refreshPresence()
            notice = "Quota credential saved to macOS Keychain."
            Task { await store.refreshQuota(providerId: slot.providerId) }
        } catch {
            notice = "Could not save the quota credential to macOS Keychain."
        }
    }

    private func revoke(_ slot: QuotaCredentialSlot) {
        do {
            try keychain.revoke(slot)
            secrets[slot] = ""
            refreshPresence()
            notice = "Quota credential revoked. Pi execution authentication was not changed."
            Task { await store.refreshQuota(providerId: slot.providerId) }
        } catch {
            notice = "Could not revoke the quota credential from macOS Keychain."
        }
    }
}
