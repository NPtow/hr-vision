import Foundation

public enum HarvestOperation: String, Codable, CaseIterable, Identifiable, Sendable {
    case companies, people, posts
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .companies: return "Найти компании"
        case .people: return "Найти людей"
        case .posts: return "Получить публикации"
        }
    }
    public var path: String {
        switch self {
        case .companies: return "/linkedin/company-search"
        case .people: return "/linkedin/lead-search"
        case .posts: return "/linkedin/profile-posts"
        }
    }
    public var contentType: SourceContentType {
        switch self {
        case .companies: return .linkedInCompany
        case .people: return .linkedInPerson
        case .posts: return .linkedInPost
        }
    }
    /// Free / Starter price observed in the account on 2026-10-11, not a billing receipt.
    public var estimatedCostUSD: Double { self == .people ? 0.10 : 0.004 }
}

public struct LinkedInPosition: Codable, Equatable, Sendable {
    public var companyName: String
    public var title: String
    public var companyURL: String?
}

public struct LinkedInMetadata: Codable, Equatable, Sendable {
    public var operation: HarvestOperation
    public var authorName: String?
    public var authorURL: String?
    public var publishedAt: String?
    public var isRepost: Bool
    public var positions: [LinkedInPosition]
    public var affiliation: String
    public init(operation: HarvestOperation, authorName: String? = nil, authorURL: String? = nil,
                publishedAt: String? = nil, isRepost: Bool = false, positions: [LinkedInPosition] = []) {
        self.operation = operation; self.authorName = authorName; self.authorURL = authorURL
        self.publishedAt = publishedAt; self.isRepost = isRepost; self.positions = positions
        affiliation = "unverified"
    }
}

public struct HarvestRunMetadata: Codable, Equatable, Sendable {
    public var operation: HarvestOperation
    public var targetURL: String?
    public var jobTitles: String?
    public var receivedCount: Int
    public var reportedCount: Int?
    public var totalPages: Int?
    public var page: Int
    public var estimatedCostUSD: Double
    public init(operation: HarvestOperation, targetURL: String?, jobTitles: String?, receivedCount: Int,
                reportedCount: Int?, totalPages: Int?) {
        self.operation = operation; self.targetURL = targetURL; self.jobTitles = jobTitles
        self.receivedCount = receivedCount; self.reportedCount = reportedCount; self.totalPages = totalPages
        page = 1; estimatedCostUSD = operation.estimatedCostUSD
    }
}
