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
        DashboardCard(title: L10n.quotaCredentialsTitle, symbol: "key.fill") {
            Text(L10n.quotaCredentialsExplanation)
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
                            Text(
                                authorized.contains(slot)
                                    ? L10n.quotaCredentialAuthorized
                                    : L10n.quotaCredentialNone
                            )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if authorized.contains(slot) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                                .accessibilityLabel(L10n.quotaCredentialAccessibilityAuthorized)
                        }
                    }

                    SecureField(
                        L10n.quotaCredentialPlaceholder,
                        text: secretBinding(for: slot)
                    )
                    .textFieldStyle(.roundedBorder)
                    .privacySensitive()

                    HStack {
                        Button(L10n.quotaCredentialSave) {
                            save(slot)
                        }
                        .disabled(secretValue(for: slot).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        Button(L10n.quotaCredentialRevoke, role: .destructive) {
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

            Text(L10n.quotaCredentialsFooter)
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
            notice = L10n.quotaCredentialSaved
            Task { await store.refreshQuota(providerId: slot.providerId) }
        } catch {
            notice = L10n.quotaCredentialSaveFailed
        }
    }

    private func revoke(_ slot: QuotaCredentialSlot) {
        do {
            try keychain.revoke(slot)
            secrets[slot] = ""
            refreshPresence()
            notice = L10n.quotaCredentialRevoked
            Task { await store.refreshQuota(providerId: slot.providerId) }
        } catch {
            notice = L10n.quotaCredentialRevokeFailed
        }
    }
}
