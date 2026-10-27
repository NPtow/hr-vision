import Foundation
import Security
import SignalsCore

/// TargetHunter uses a shared local file; other providers retain separate Keychain items.
final class SourceCredentialStore {
    private let targetHunter = TargetHunterCredentialStore()
    private func query(_ provider: SourceProvider) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "ru.hrvision.tgsignals.sources",
         kSecAttrAccount as String: provider.rawValue]
    }

    func load(_ provider: SourceProvider) throws -> SourceCredentials? {
        if provider == .targetHunter { return try targetHunter.load() }
        var attributes = query(provider)
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(attributes as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw CredentialError(status: status) }
        do { return try JSONDecoder().decode(SourceCredentials.self, from: data) }
        catch { throw CredentialError(status: errSecDecode) }
    }

    func save(_ credentials: SourceCredentials, for provider: SourceProvider) throws {
        if provider == .targetHunter { try targetHunter.save(credentials); return }
        let data = try JSONEncoder().encode(credentials)
        let status = SecItemUpdate(query(provider) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var attributes = query(provider)
            attributes[kSecValueData as String] = data
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(attributes as CFDictionary, nil)
            guard added == errSecSuccess else { throw CredentialError(status: added) }
        } else if status != errSecSuccess { throw CredentialError(status: status) }
    }

    func remove(_ provider: SourceProvider) throws {
        if provider == .targetHunter { try targetHunter.remove(); return }
        let status = SecItemDelete(query(provider) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw CredentialError(status: status) }
    }

    private struct CredentialError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "Связка ключей недоступна (код \(status)). Данные доступа не записаны в файлы приложения." }
    }
}
