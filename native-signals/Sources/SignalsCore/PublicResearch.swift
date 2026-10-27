import Foundation

/// Captured public pages, independent of browser sessions and paid search APIs.
public struct PublicResearchRecord: Codable, Sendable {
    public var version: Int
    public var companyID: String
    public var companyName: String
    public var searchedAt: String
    public var documents: [PublicResearchDocument]
    public var channels: [PublicResearchChannel]
    public var contacts: [PublicResearchContact]
    public var checks: [PublicResearchCheck]
    public var notes: [String]?
}

public struct PublicResearchDocument: Codable, Sendable {
    public var id: String
    public var companyID: String
    public var url: String
    public var title: String
    public var text: String
    public var kind: String
    public var provider: String
    public var retrievedAt: String
    public var publishedAt: String?
    public var quality: String
    public var official: Bool
}

public struct PublicResearchChannel: Codable, Sendable {
    public var url: String
    public var sourceURL: String
    public var context: String
    public var basis: String
    public var purpose: String
    public var title: String?
    public var bio: String?
    public var status: String?
    public var accountType: String?
    public var retrievedAt: String?
    public var profileSourceURL: String? = nil
    public var verified: Bool { ["officialWebsite", "domainInBio", "reviewedIdentity", "officialChannelPost"].contains(basis) }
}

public struct PublicResearchContact: Codable, Sendable {
    public var url: String
    public var label: String
    public var purpose: String
    public var context: String
    public var sourceURL: String
    public var basis: String
}

public struct PublicResearchCheck: Codable, Sendable {
    public var url: String
    public var status: String
}

public enum ContactPurpose: String, CaseIterable, Sendable {
    case cooperation, hiring, support, enrollment, general, channel, chat, bot, unknown
    public var label: String {
        switch self {
        case .cooperation: return "Сотрудничество и реклама"
        case .hiring: return "Найм и вакансии"
        case .support: return "Поддержка"
        case .enrollment: return "Запись на обучение"
        case .general: return "Общий контакт"
        case .channel: return "Каналы компании"
        case .chat: return "Чат компании"
        case .bot: return "Бот компании"
        case .unknown: return "Назначение не установлено"
        }
    }
}

public struct PublicResearchStore: Sendable {
    public let directory: URL
    public init(directory: URL? = nil) {
        let base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        self.directory = directory ?? base.appendingPathComponent("public-research", isDirectory: true)
    }
    public func load() -> (records: [String: PublicResearchRecord], errors: [String]) {
        guard FileManager.default.fileExists(atPath: directory.path) else { return ([:], []) }
        var records: [String: PublicResearchRecord] = [:], errors: [String] = []
        do {
            for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil).filter({ $0.pathExtension == "json" }).sorted(by: { $0.path < $1.path }) {
                do {
                    let record = try JSONDecoder().decode(PublicResearchRecord.self, from: Data(contentsOf: url))
                    guard record.version == 1, !record.companyID.isEmpty, record.documents.allSatisfy({ $0.companyID == record.companyID }) else { throw CocoaError(.coderReadCorrupt) }
                    records[record.companyID] = record
                } catch { errors.append("Не прочитан архив \(url.lastPathComponent): \(error.localizedDescription)") }
            }
        } catch { errors.append("Не прочитан каталог материалов: \(error.localizedDescription)") }
        return (records, errors)
    }
}
