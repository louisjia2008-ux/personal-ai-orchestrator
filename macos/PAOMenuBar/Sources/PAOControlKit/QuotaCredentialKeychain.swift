import Foundation
import Security

/// Explicit owner-managed credentials used only by PAO's read-only quota collectors.
///
/// Pi execution authentication is deliberately not inspected or copied. The owner
/// must opt in by placing a provider API credential in one of these PAO-owned
/// Keychain slots. The daemon resolves the same stable service/account identity
/// only when collecting quota.
public enum QuotaCredentialSlot: String, CaseIterable, Identifiable, Sendable {
    case zaiCodingPlan = "zai-coding-plan"
    case miniMaxChinaCodingPlan = "minimax-cn-coding-plan"
    case miniMaxGlobalCodingPlan = "minimax-coding-plan"

    public var id: String { rawValue }
    public var providerId: String { rawValue }

    public var displayName: String {
        switch self {
        case .zaiCodingPlan:
            return "GLM / Z.AI Coding Plan"
        case .miniMaxChinaCodingPlan:
            return "MiniMax China Coding Plan"
        case .miniMaxGlobalCodingPlan:
            return "MiniMax Global Coding Plan"
        }
    }
}

public enum QuotaCredentialKeychainError: Error, Equatable {
    case emptySecret
    case osStatus(OSStatus)
}

protocol QuotaCredentialSecureStore {
    func contains(service: String, account: String) throws -> Bool
    func write(_ data: Data, service: String, account: String) throws
    func remove(service: String, account: String) throws
}

struct SystemQuotaCredentialSecureStore: QuotaCredentialSecureStore {
    func contains(service: String, account: String) throws -> Bool {
        var query = baseQuery(service: service, account: account)
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnAttributes as String] = kCFBooleanTrue

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            return true
        case errSecItemNotFound:
            return false
        default:
            throw QuotaCredentialKeychainError.osStatus(status)
        }
    }

    func write(_ data: Data, service: String, account: String) throws {
        var query = baseQuery(service: service, account: account)
        SecItemDelete(query as CFDictionary)

        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        query[kSecAttrSynchronizable as String] = kCFBooleanFalse

        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw QuotaCredentialKeychainError.osStatus(status)
        }
    }

    func remove(service: String, account: String) throws {
        let status = SecItemDelete(baseQuery(service: service, account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw QuotaCredentialKeychainError.osStatus(status)
        }
    }

    private func baseQuery(service: String, account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse as Any,
        ]
    }
}

public struct QuotaCredentialKeychain {
    /// Must stay byte-for-byte aligned with
    /// quota_credentials.OWNER_QUOTA_KEYCHAIN_SERVICE.
    public static let serviceName = "personal-ai-orchestrator.quota"

    private let secureStore: any QuotaCredentialSecureStore

    public init() {
        self.secureStore = SystemQuotaCredentialSecureStore()
    }

    init(secureStore: any QuotaCredentialSecureStore) {
        self.secureStore = secureStore
    }

    public func isAuthorized(_ slot: QuotaCredentialSlot) -> Bool {
        (try? secureStore.contains(service: Self.serviceName, account: slot.rawValue)) ?? false
    }

    public func store(_ secret: String, for slot: QuotaCredentialSlot) throws {
        let normalized = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            throw QuotaCredentialKeychainError.emptySecret
        }
        try secureStore.write(
            Data(normalized.utf8),
            service: Self.serviceName,
            account: slot.rawValue
        )
    }

    public func revoke(_ slot: QuotaCredentialSlot) throws {
        try secureStore.remove(service: Self.serviceName, account: slot.rawValue)
    }
}
