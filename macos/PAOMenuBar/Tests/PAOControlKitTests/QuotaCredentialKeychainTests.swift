import Foundation
import XCTest

@testable import PAOControlKit

private final class InMemoryQuotaCredentialStore: QuotaCredentialSecureStore {
    private(set) var values: [String: Data] = [:]

    func contains(service: String, account: String) throws -> Bool {
        values[key(service: service, account: account)] != nil
    }

    func write(_ data: Data, service: String, account: String) throws {
        values[key(service: service, account: account)] = data
    }

    func remove(service: String, account: String) throws {
        values.removeValue(forKey: key(service: service, account: account))
    }

    private func key(service: String, account: String) -> String {
        "\(service)|\(account)"
    }
}

final class QuotaCredentialKeychainTests: XCTestCase {
    func testOwnerAuthorizationUsesStableQuotaOnlyNamespace() throws {
        let backend = InMemoryQuotaCredentialStore()
        let keychain = QuotaCredentialKeychain(secureStore: backend)

        try keychain.store("  fixture-secret  ", for: .zaiCodingPlan)

        XCTAssertTrue(keychain.isAuthorized(.zaiCodingPlan))
        XCTAssertFalse(keychain.isAuthorized(.miniMaxChinaCodingPlan))
        let storageKey = "\(QuotaCredentialKeychain.serviceName)|zai-coding-plan"
        XCTAssertEqual(
            String(data: try XCTUnwrap(backend.values[storageKey]), encoding: .utf8),
            "fixture-secret"
        )
    }

    func testRevocationOnlyRemovesSelectedQuotaCredential() throws {
        let backend = InMemoryQuotaCredentialStore()
        let keychain = QuotaCredentialKeychain(secureStore: backend)

        try keychain.store("zai-secret", for: .zaiCodingPlan)
        try keychain.store("minimax-secret", for: .miniMaxChinaCodingPlan)
        try keychain.revoke(.zaiCodingPlan)

        XCTAssertFalse(keychain.isAuthorized(.zaiCodingPlan))
        XCTAssertTrue(keychain.isAuthorized(.miniMaxChinaCodingPlan))
    }

    func testEmptyCredentialIsRejectedBeforeSecureStoreWrite() {
        let backend = InMemoryQuotaCredentialStore()
        let keychain = QuotaCredentialKeychain(secureStore: backend)

        XCTAssertThrowsError(try keychain.store("   \n", for: .zaiCodingPlan)) { error in
            XCTAssertEqual(error as? QuotaCredentialKeychainError, .emptySecret)
        }
        XCTAssertTrue(backend.values.isEmpty)
    }

    func testSupportedSlotsAreQuotaSurfacesNotExecutionIdentities() {
        XCTAssertEqual(
            Set(QuotaCredentialSlot.allCases.map(\.providerId)),
            Set([
                "zai-coding-plan",
                "minimax-cn-coding-plan",
                "minimax-coding-plan",
            ])
        )
    }
}
