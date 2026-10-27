import Foundation
import CryptoKit

public enum SourceProvider: String, Codable, CaseIterable, Identifiable, Sendable {
    case serper, yandex, targetHunter, harvest
    public var id: String { rawValue }
    public var displayName: String {
        switch self {
        case .serper: return "Google · Serper"
        case .yandex: return "Яндекс Search API"
        case .targetHunter: return "VK · TargetHunter"
        case .harvest: return "LinkedIn · HarvestAPI"
        }
    }
}

public struct SourceSearchRequest: Codable, Equatable, Sendable {
    public var companyID: String
    public var companyName: String
    public var query: String
    public var limit: Int
    public init(companyID: String, companyName: String, query: String, limit: Int = 10) {
        self.companyID = companyID; self.companyName = companyName
        self.query = query; self.limit = limit
    }
}

public enum SourceContentType: String, Codable, Sendable { case searchSnippet, vkCommunityCandidate, linkedInCompany, linkedInPerson, linkedInPost }

public enum VKAffiliationStatus: String, Codable, Sendable { case unverified }

/// Public community metadata returned by VK. A name match does not establish affiliation.
public struct VKCommunityMetadata: Codable, Equatable, Sendable {
    public var id: Int64
    public var screenName: String?
    public var membersCount: Int?
    /// VK's values: 0 = open, 1 = closed, 2 = private. Nil means not supplied.
    public var isClosed: Int?
    public var groupType: String?
    public var affiliation: VKAffiliationStatus
    public init(id: Int64, screenName: String? = nil, membersCount: Int? = nil, isClosed: Int? = nil, groupType: String? = nil) {
        self.id = id; self.screenName = screenName; self.membersCount = membersCount
        self.isClosed = isClosed; self.groupType = groupType; affiliation = .unverified
    }
}

public enum TargetHunterCompleteness: String, Codable, Sendable {
    case complete, partial, unknown
    public static func assess(reportedCount: Int?, receivedCount: Int) -> Self {
        guard let reportedCount else { return .unknown }
        if receivedCount == reportedCount { return .complete }
        return receivedCount < reportedCount ? .partial : .unknown
    }
}

public struct TargetHunterTaskMetadata: Codable, Equatable, Sendable {
    public var taskID: String
    public var moduleID: String
    public var reportedCount: Int?
    /// Number of unique community objects actually retained, not the provider's task count.
    public var receivedCount: Int
    public var completeness: TargetHunterCompleteness
    public init(taskID: String, moduleID: String = "vk/2", reportedCount: Int?, receivedCount: Int) {
        self.taskID = taskID; self.moduleID = moduleID; self.reportedCount = reportedCount
        self.receivedCount = receivedCount
        completeness = .assess(reportedCount: reportedCount, receivedCount: receivedCount)
    }
}

public struct SourceSearchResult: Codable, Identifiable, Sendable {
    public var id: String
    public var provider: SourceProvider
    public var companyID: String
    public var companyName: String
    public var query: String
    public var url: String
    public var title: String
    public var snippet: String
    public var retrievedAt: Date
    public var contentType: SourceContentType
    public var vkCommunity: VKCommunityMetadata?
    public var linkedIn: LinkedInMetadata?

    public init(provider: SourceProvider, request: SourceSearchRequest, url: String, title: String, snippet: String, retrievedAt: Date = Date()) {
        let identity = [provider.rawValue, request.companyID, request.query, url, title, snippet].joined(separator: "\u{0}")
        id = "source:" + SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
        self.provider = provider; companyID = request.companyID; companyName = request.companyName
        query = request.query; self.url = url; self.title = title; self.snippet = snippet
        self.retrievedAt = retrievedAt; contentType = .searchSnippet
        vkCommunity = nil
        linkedIn = nil
    }

    public init(request: SourceSearchRequest, community: VKCommunityMetadata, title: String, retrievedAt: Date) {
        let url = "https://vk.com/club\(community.id)"
        let metadata = [String(community.id), community.screenName ?? "", community.membersCount.map(String.init) ?? "",
                        community.isClosed.map(String.init) ?? "", community.groupType ?? ""].joined(separator: "\u{0}")
        let identity = [SourceProvider.targetHunter.rawValue, request.companyID, request.query,
                        SourceContentType.vkCommunityCandidate.rawValue, url, title, metadata].joined(separator: "\u{0}")
        id = "source:" + SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
        provider = .targetHunter; companyID = request.companyID; companyName = request.companyName
        query = request.query; self.url = url; self.title = title
        snippet = "Сообщество найдено по запросу. Принадлежность компании не подтверждена."
        self.retrievedAt = retrievedAt; contentType = .vkCommunityCandidate; vkCommunity = community
        linkedIn = nil
    }

    public init(request: SourceSearchRequest, url: String, title: String, text: String, metadata: LinkedInMetadata, retrievedAt: Date) {
        self.init(provider: .harvest, request: request, url: url, title: title, snippet: text, retrievedAt: retrievedAt)
        contentType = metadata.operation.contentType; linkedIn = metadata
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let details = (try? encoder.encode(metadata)) ?? Data()
        id = "source:" + SHA256.hash(data: Data(id.utf8) + details).map { String(format: "%02x", $0) }.joined()
    }

    /// A search extract is evidence of what the index returned, not verified page contents.
    public func evidence(collectionID: String) -> Evidence {
        let date = ISO8601DateFormatter().string(from: retrievedAt)
        let kind: String
        switch contentType {
        case .searchSnippet: kind = "Поисковый сниппет"
        case .vkCommunityCandidate: kind = "Кандидат сообщества VK"
        case .linkedInCompany: kind = "Страница компании LinkedIn"
        case .linkedInPerson: kind = "Профиль LinkedIn · связь требует проверки"
        case .linkedInPost: kind = linkedIn?.isRepost == true ? "Репост LinkedIn" : "Публикация LinkedIn"
        }
        return Evidence(id: id, title: title, excerpt: snippet, url: url, dateLabel: linkedIn?.publishedAt ?? date,
                        kind: kind, isDemo: false,
                        provenance: .init(provider: provider.displayName, query: query, retrievedAt: date,
                                          channel: nil, collectionID: collectionID, contentType: contentType.rawValue))
    }
}

public struct SourceMCPTool: Codable, Identifiable, Sendable {
    public var id: String { name }
    public var name: String
    public var description: String
    public var inputSchemaJSON: String
    public init(name: String, description: String, inputSchemaJSON: String) {
        self.name = name; self.description = description; self.inputSchemaJSON = inputSchemaJSON
    }
}

public struct SourceCollectionRun: Codable, Identifiable, Sendable {
    public var version: Int
    public var id: String
    public var provider: SourceProvider
    public var request: SourceSearchRequest?
    public var results: [SourceSearchResult]
    public var tools: [SourceMCPTool]
    /// Sanitized successful provider response. No request headers, tokens, or MCP session IDs.
    public var rawResponse: String
    public var rawFormat: String
    public var startedAt: Date
    public var completedAt: Date
    public var hasMore: Bool
    /// Present only for version 2 community search exports. Legacy discovery runs have no task.
    public var targetHunterTask: TargetHunterTaskMetadata?
    public var harvest: HarvestRunMetadata?
    public init(provider: SourceProvider, request: SourceSearchRequest? = nil,
                results: [SourceSearchResult] = [], tools: [SourceMCPTool] = [],
                rawResponse: String, rawFormat: String = "json", startedAt: Date = Date(),
                completedAt: Date = Date(), hasMore: Bool = false) {
        version = 1; id = "source-" + UUID().uuidString.lowercased(); self.provider = provider
        self.request = request; self.results = results; self.tools = tools
        self.rawResponse = rawResponse; self.rawFormat = rawFormat
        self.startedAt = startedAt; self.completedAt = completedAt; self.hasMore = hasMore
        targetHunterTask = nil
        harvest = nil
    }
}

public enum SourceError: Error, LocalizedError, Equatable {
    case missingKey, missingFolderID, invalidQuery, unsupportedOperation
    case unauthorized, forbidden, rateLimited, invalidResponse, responseTooLarge, redirectRejected
    case network, cancelled, http(Int), remoteRPC, unsupportedProtocol
    public var errorDescription: String? {
        switch self {
        case .missingKey: return "Введите ключ сервиса без переводов строк."
        case .missingFolderID: return "Введите идентификатор каталога Яндекс Облака (folder ID)."
        case .invalidQuery: return "Выберите компанию и введите запрос до 400 символов (для Яндекса — до 40 слов). За один запуск доступно не более 10 результатов."
        case .unsupportedOperation: return "Для этого сервиса операция пока не подключена."
        case .unauthorized: return "Ключ не принят (401). Проверьте его в кабинете сервиса."
        case .forbidden: return "Сервис отказал в доступе (403). Проверьте права ключа и тариф."
        case .rateLimited: return "Лимит исчерпан или запросы слишком частые (429). Автоповтора нет."
        case .invalidResponse: return "Ответ сервиса не соответствует ожидаемому формату."
        case .responseTooLarge: return "Ответ сервиса превышает допустимый размер."
        case .redirectRejected: return "Сервис перенаправил запрос. Ключ не передан по новому адресу."
        case .network: return "Не удалось связаться с сервисом. Запрос автоматически не повторён."
        case .cancelled: return "Запрос остановлен. Автоповтора нет."
        case .http(let status): return "Сервис вернул HTTP \(status). Автоповтора нет."
        case .remoteRPC: return "TargetHunter вернул ошибку MCP. Текст сервера не сохраняется."
        case .unsupportedProtocol: return "Версия MCP, предложенная сервером, пока не поддерживается."
        }
    }
}
