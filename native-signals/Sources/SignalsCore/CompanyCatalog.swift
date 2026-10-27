import Foundation
import CryptoKit

public enum DiscoverySourceAccess: String, Codable, Sendable {
    case pageRead = "page_read"
    case searchIndex = "search_index"

    public var label: String {
        switch self {
        case .pageRead: return "Прочитана страница официального сайта"
        case .searchIndex: return "Поисковый индекс официального сайта"
        }
    }
}

public struct CompanyDiscoverySource: Codable, Sendable {
    public var url: String
    public var title: String
    public var excerpt: String
    public var access: DiscoverySourceAccess
    public var checkedAt: String
}

public struct CompanyDiscoveryMetadata: Codable, Sendable {
    public var segment: String
    public var product: String
    public var audience: String
    public var sizeStatus: String
    public var groupNote: String
    public var discoveryQuery: String
    public var discoveryUrl: String
    public var sources: [CompanyDiscoverySource]

    public var lastCheckedAt: String? { sources.map(\.checkedAt).max() }
    public var hasReadPage: Bool { sources.contains { $0.access == .pageRead } }
    public var sizeLabel: String {
        sizeStatus == "unknown" ? "Размер команды не установлен" : "Масштаб: \(sizeStatus)"
    }
}

public struct DiscoveredCompany: Codable, Sendable {
    public var id: String
    public var name: String
    public var website: String
    public var segment: String
    public var product: String
    public var audience: String
    public var sizeStatus: String
    public var groupNote: String
    public var discoveryQuery: String
    public var discoveryUrl: String
    public var sources: [CompanyDiscoverySource]

    var dossier: CompanyDossier {
        let evidence = sources.map { source in
            let digest = SHA256.hash(data: Data(source.url.utf8)).map { String(format: "%02x", $0) }.joined().prefix(16)
            return Evidence(id: "\(id)-web-\(digest)", title: source.title, excerpt: source.excerpt,
                url: source.url, dateLabel: "Получено \(source.checkedAt) · дата публикации не установлена",
                kind: source.access.label, isDemo: false,
                provenance: EvidenceProvenance(provider: source.access.label, query: discoveryQuery,
                    retrievedAt: source.checkedAt, channel: nil, collectionID: "company-discovery",
                    contentType: "Пересказ исследователя; \(source.access == .pageRead ? "прочитана страница" : "прочитан только результат поиска")"))
        }
        var parameters = SeedCatalog.emptyParameters
        if let index = parameters.firstIndex(where: { $0.id == "product_type" }) {
            parameters[index].value = product
            parameters[index].state = .supported
            parameters[index].evidenceIDs = evidence.map(\.id)
        }
        return CompanyDossier(id: id, name: name, subtitle: segment, website: website,
            initials: String(name.prefix(1)).uppercased(), isDemo: false, evidence: evidence,
            parameters: parameters, hypotheses: SeedCatalog.emptyHypotheses,
            discovery: CompanyDiscoveryMetadata(segment: segment, product: product, audience: audience,
                sizeStatus: sizeStatus, groupNote: groupNote, discoveryQuery: discoveryQuery,
                discoveryUrl: discoveryUrl, sources: sources))
    }
}

public struct CompanyCatalogResult: Sendable {
    public var companies: [CompanyDossier]
    public var duplicateNotes: [String]
    public var addedCount: Int
}

public enum CompanyCatalogError: LocalizedError {
    case missingResource(String)
    case invalidRecord(String)
    case conflictingID(String)

    public var errorDescription: String? {
        switch self {
        case .missingResource(let path): return "Не найден файл каталога: \(path)"
        case .invalidRecord(let reason): return "Неверная запись каталога: \(reason)"
        case .conflictingID(let id): return "Один идентификатор относится к разным компаниям: \(id)"
        }
    }
}

public enum CompanyCatalog {
    public static let resourceBundleName = "TGSignals_SignalsCore.bundle"

    public static func loadBundled() throws -> CompanyCatalogResult {
        let resourceBundle: Bundle
        if Bundle.main.bundleURL.pathExtension == "app" {
            // SwiftPM's generated accessor may fall back to the developer's build folder.
            // Installed apps must use only the resource bundle inside their own package.
            guard let resourceURL = Bundle.main.resourceURL else {
                throw CompanyCatalogError.missingResource(resourceBundleName)
            }
            let url = resourceURL.appendingPathComponent(resourceBundleName)
            guard let bundle = Bundle(url: url) else {
                throw CompanyCatalogError.missingResource(url.path)
            }
            resourceBundle = bundle
        } else {
            resourceBundle = Bundle.module
        }
        guard let url = resourceBundle.url(forResource: "discovered-companies", withExtension: "json") else {
            throw CompanyCatalogError.missingResource("discovered-companies.json")
        }
        return try load(data: Data(contentsOf: url))
    }

    public static func load(data: Data, seeds: [CompanyDossier] = SeedCatalog.companies) throws -> CompanyCatalogResult {
        let records = try JSONDecoder().decode([DiscoveredCompany].self, from: data)
        var companies = seeds
        var byID = Dictionary(uniqueKeysWithValues: seeds.map { ($0.id, canonicalDomain($0.website ?? "") ?? "demo:\($0.id)") })
        var byDomain: [String: String] = [:]
        for seed in seeds {
            if let domain = canonicalDomain(seed.website ?? "") { byDomain[domain] = seed.id }
        }
        var notes: [String] = []
        var addedCount = 0
        for record in records {
            try validate(record)
            let domain = canonicalDomain(record.website)!
            if let knownDomain = byID[record.id] {
                guard knownDomain == domain else { throw CompanyCatalogError.conflictingID(record.id) }
                notes.append("\(record.name): повтор идентификатора \(record.id)")
                continue
            }
            if let existingID = byDomain[domain] {
                notes.append("\(record.name): домен \(domain) уже принадлежит \(existingID)")
                continue
            }
            byID[record.id] = domain
            byDomain[domain] = record.id
            companies.append(record.dossier)
            addedCount += 1
        }
        return CompanyCatalogResult(companies: companies, duplicateNotes: notes, addedCount: addedCount)
    }

    public static func canonicalDomain(_ value: String) -> String? {
        guard let url = URL(string: value), ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.user == nil, url.password == nil, var host = url.host?.lowercased(), host.contains(".") else { return nil }
        while host.hasSuffix(".") { host.removeLast() }
        if host.hasPrefix("www.") { host.removeFirst(4) }
        if host == "ru.hexlet.io" { return "hexlet.io" }
        return host
    }

    private static func validate(_ record: DiscoveredCompany) throws {
        func require(_ condition: Bool, _ reason: String) throws {
            if !condition { throw CompanyCatalogError.invalidRecord("\(record.id): \(reason)") }
        }
        try require(record.id.range(of: "^discovery-[a-z0-9]+(?:-[a-z0-9]+)*$", options: .regularExpression) != nil, "формат id")
        for (field, value) in [("name", record.name), ("segment", record.segment), ("product", record.product),
            ("audience", record.audience), ("sizeStatus", record.sizeStatus), ("groupNote", record.groupNote),
            ("discoveryQuery", record.discoveryQuery)] {
            try require(!value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, "пустое поле \(field)")
        }
        try require(canonicalDomain(record.website) != nil, "неверный адрес сайта")
        try require(canonicalDomain(record.discoveryUrl) != nil, "неверный адрес обнаружения")
        try require(!record.sources.isEmpty, "нет источников")
        try require(Set(record.sources.map(\.url)).count == record.sources.count, "повтор источника")
        let companyDomain = canonicalDomain(record.website)!
        try require(record.sources.contains { source in
            guard let host = canonicalDomain(source.url) else { return false }
            return host == companyDomain || host.hasSuffix("." + companyDomain) || companyDomain.hasSuffix("." + host)
        }, "нет источника на домене компании")
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        for source in record.sources {
            try require(canonicalDomain(source.url) != nil, "неверный адрес источника")
            try require(!source.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
                !source.excerpt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, "источник без содержания")
            try require(source.checkedAt.range(of: "^\\d{4}-\\d{2}-\\d{2}$", options: .regularExpression) != nil &&
                formatter.date(from: source.checkedAt) != nil, "неверная дата источника")
        }
    }
}

public extension CompanyDossier {
    var catalogSegment: String { discovery?.segment ?? (isDemo ? "Учебные сценарии" : "Исходный пилот") }

    func matchesCatalogSearch(_ query: String) -> Bool {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty || [name, subtitle, website ?? "", discovery?.product ?? "", discovery?.audience ?? ""]
            .contains { $0.localizedCaseInsensitiveContains(text) }
    }
}
