import Foundation

public enum TelemetrOrigin: String, Codable, CaseIterable, Sendable {
    case telemetrAPI, telemetrWebsite
    public var label: String { self == .telemetrAPI ? "Telemetr API" : "Сайт Telemetr" }
}

public enum TelemetrRelevance: String, Codable, CaseIterable, Sendable {
    case unreviewed, confirmed, rejected
    public var label: String {
        switch self {
        case .unreviewed: return "Не проверено"
        case .confirmed: return "Относится к компании"
        case .rejected: return "Не относится"
        }
    }
}

public struct TelemetrQuotaBucket: Codable, Equatable, Sendable {
    public var limit: Int
    public var used: Int
    public var remaining: Int
    public var resetAt: Int?
    public init(limit: Int, used: Int, remaining: Int, resetAt: Int? = nil) {
        self.limit = limit; self.used = used; self.remaining = remaining; self.resetAt = resetAt
    }
    enum CodingKeys: String, CodingKey { case limit, used, remaining; case resetAt = "reset_at" }
}

public struct TelemetrLimits: Codable, Sendable {
    public var model: String
    public var platform: String?
    public var requests: TelemetrQuotaBucket?
    public var channels: TelemetrQuotaBucket?
    public var search: TelemetrQuotaBucket?
    public var posts: TelemetrQuotaBucket?
    public var sampledAt: Date
    public var rateLimitHeaders: [String: String]

    public init(model: String, platform: String?, requests: TelemetrQuotaBucket? = nil,
                channels: TelemetrQuotaBucket? = nil, search: TelemetrQuotaBucket? = nil,
                posts: TelemetrQuotaBucket? = nil, sampledAt: Date = Date(), rateLimitHeaders: [String: String] = [:]) {
        self.model = model; self.platform = platform; self.requests = requests; self.channels = channels
        self.search = search; self.posts = posts; self.sampledAt = sampledAt; self.rateLimitHeaders = rateLimitHeaders
    }

    enum CodingKeys: String, CodingKey { case model, platform, requests, channels, search, posts, sampledAt, rateLimitHeaders }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        model = try c.decode(String.self, forKey: .model)
        platform = try c.decodeIfPresent(String.self, forKey: .platform)
        requests = try c.decodeIfPresent(TelemetrQuotaBucket.self, forKey: .requests)
        channels = try c.decodeIfPresent(TelemetrQuotaBucket.self, forKey: .channels)
        search = try c.decodeIfPresent(TelemetrQuotaBucket.self, forKey: .search)
        posts = try c.decodeIfPresent(TelemetrQuotaBucket.self, forKey: .posts)
        sampledAt = try c.decodeIfPresent(Date.self, forKey: .sampledAt) ?? Date()
        rateLimitHeaders = try c.decodeIfPresent([String: String].self, forKey: .rateLimitHeaders) ?? [:]
    }

    /// A website subscription alone is not proof of API access. All decisions use /v1/limits.
    public var companySearchBlockReason: String? {
        guard platform == "telegram" else { return "Нужен ключ Telegram. Площадка этого ключа не подтверждена как Telegram." }
        guard model == "step" || model == "monthly" else {
            if model == "trial" { return "Пробный API ищет только в демо-канале Telemetr. Поиск школ недоступен." }
            return "Платный доступ API не подтверждён. Подписка сайта и пакет API проверяются отдельно."
        }
        guard let requests, requests.remaining >= 3 else { return "Для пробы трёх школ нужно не менее 3 запросов API." }
        // Legacy monthly grants have no post meter: the API documents all-zero values.
        let unmeteredMonthly = model == "monthly" && posts?.limit == 0 && posts?.used == 0 && posts?.remaining == 0
        guard unmeteredMonthly || (posts?.remaining ?? -1) >= 20 else { return "Для пробы нужен запас не менее 20 публикаций API." }
        return nil
    }
}

public struct TelemetrQuery: Codable, Hashable, Sendable {
    public var companyID: String
    public var companyName: String
    public var query: String
    public var limit: Int
    public init(companyID: String, companyName: String, query: String, limit: Int) {
        self.companyID = companyID; self.companyName = companyName; self.query = query; self.limit = limit
    }
    public static let pilot: [TelemetrQuery] = [
        .init(companyID: "brunoyam", companyName: "Бруноям", query: "Бруноям", limit: 7),
        .init(companyID: "karpov", companyName: "karpov.courses", query: "karpov.courses", limit: 7),
        .init(companyID: "setters", companyName: "SETTERS Education", query: "SETTERS", limit: 6)
    ]
}

public struct TelemetrPost: Identifiable, Codable, Sendable {
    public var id: String { "\(platform):\(channelID):\(postID)" }
    public var companyID: String
    public var companyName: String
    public var query: String
    public var platform: String
    public var channelID: String
    public var postID: String
    public var channelTitle: String?
    public var text: String
    public var sourceURL: String?
    public var sourcePageURL: String?
    public var sourceDeleted: Bool?
    public var textFromCard: Bool?
    public var signalSummary: String?
    public var outreachHypothesis: String?
    public var publishedAt: Date?
    public var sourceDateLabel: String?
    public var retrievedAt: Date
    public var origin: TelemetrOrigin
    public var relevance: TelemetrRelevance
    public init(companyID: String, companyName: String, query: String, platform: String = "telegram",
                channelID: String, postID: String, channelTitle: String? = nil, text: String,
                sourceURL: String? = nil, publishedAt: Date? = nil, sourceDateLabel: String? = nil, retrievedAt: Date = Date(),
                origin: TelemetrOrigin, relevance: TelemetrRelevance = .unreviewed,
                sourcePageURL: String? = nil, sourceDeleted: Bool? = nil, textFromCard: Bool? = nil,
                signalSummary: String? = nil, outreachHypothesis: String? = nil) {
        self.companyID = companyID; self.companyName = companyName; self.query = query; self.platform = platform
        self.channelID = channelID; self.postID = postID; self.channelTitle = channelTitle; self.text = text
        self.sourceURL = sourceURL; self.publishedAt = publishedAt; self.sourceDateLabel = sourceDateLabel; self.retrievedAt = retrievedAt
        self.origin = origin; self.relevance = relevance
        self.sourcePageURL = sourcePageURL; self.sourceDeleted = sourceDeleted; self.textFromCard = textFromCard
        self.signalSummary = signalSummary; self.outreachHypothesis = outreachHypothesis
    }
}

public struct TelemetrQueryResult: Codable, Sendable {
    public var query: TelemetrQuery
    public var posts: [TelemetrPost]
    public var totalCount: Int?
    public var rawResponseJSON: String?
    public var rateLimitHeaders: [String: String]
    public var errorMessage: String?
    public init(query: TelemetrQuery, posts: [TelemetrPost], totalCount: Int? = nil,
                rawResponseJSON: String? = nil, rateLimitHeaders: [String: String] = [:], errorMessage: String? = nil) {
        self.query = query; self.posts = posts; self.totalCount = totalCount; self.rawResponseJSON = rawResponseJSON
        self.rateLimitHeaders = rateLimitHeaders; self.errorMessage = errorMessage
    }
}

public enum TelemetrRunStatus: String, Codable, Sendable { case complete, partial }

public struct TelemetrCollectionRun: Identifiable, Codable, Sendable {
    public var version: Int
    public var id: String
    public var title: String?
    public var startedAt: Date
    public var completedAt: Date?
    public var origin: TelemetrOrigin
    public var queries: [TelemetrQueryResult]
    public var limitsBefore: TelemetrLimits?
    public var limitsAfter: TelemetrLimits?
    public var status: TelemetrRunStatus
    public var errorMessage: String?
    public var posts: [TelemetrPost] {
        var seen = Set<String>()
        return queries.flatMap(\.posts).filter { seen.insert($0.companyID + ":" + $0.id).inserted }
    }
    public init(version: Int = 1, id: String = UUID().uuidString, startedAt: Date = Date(), completedAt: Date? = nil,
                origin: TelemetrOrigin, queries: [TelemetrQueryResult] = [], limitsBefore: TelemetrLimits? = nil,
                limitsAfter: TelemetrLimits? = nil, status: TelemetrRunStatus = .complete, errorMessage: String? = nil, title: String? = nil) {
        self.version = version; self.id = id; self.startedAt = startedAt; self.completedAt = completedAt
        self.title = title
        self.origin = origin; self.queries = queries; self.limitsBefore = limitsBefore
        self.limitsAfter = limitsAfter; self.status = status; self.errorMessage = errorMessage
    }
}
