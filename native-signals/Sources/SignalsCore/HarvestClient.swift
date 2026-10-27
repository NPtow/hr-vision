import Foundation

/// One read-only first-page request. No cookies, retries, pagination, messages, or enrichment of emails.
public final class HarvestClient {
    private let apiKey: String
    private let session: URLSession
    private let redirectDelegate = SourceRedirectDelegate()

    public init(apiKey: String, configuration: URLSessionConfiguration? = nil) {
        self.apiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let config = (configuration?.copy() as? URLSessionConfiguration) ?? .ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.httpShouldSetCookies = false; config.httpAdditionalHeaders = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 90; config.timeoutIntervalForResource = 120
        session = URLSession(configuration: config)
    }
    deinit { session.invalidateAndCancel() }

    public func collect(_ query: SourceSearchRequest, operation: HarvestOperation,
                        targetURL: String = "", jobTitles: String = "") async throws -> SourceCollectionRun {
        guard !apiKey.isEmpty, apiKey.count <= 4096, apiKey.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }) else { throw SourceError.missingKey }
        guard !query.companyID.isEmpty, !query.companyName.isEmpty, !query.query.isEmpty,
              query.query.count <= 400, (1...100).contains(query.limit), jobTitles.count <= 400
        else { throw SourceError.invalidQuery }
        var parameters = [URLQueryItem(name: "page", value: "1")]
        var target: String?
        switch operation {
        case .companies:
            parameters.append(.init(name: "search", value: query.query))
        case .people:
            guard let url = Self.linkedInURL(targetURL, kind: .companies) else { throw HarvestError.invalidCompanyURL }
            target = url
            parameters.append(.init(name: "currentCompanies", value: url))
            if !jobTitles.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                parameters.append(.init(name: "currentJobTitles", value: jobTitles))
            }
        case .posts:
            guard let url = Self.linkedInURL(targetURL, kind: .people) else { throw HarvestError.invalidProfileURL }
            target = url
            parameters.append(.init(name: "profile", value: url))
        }
        if Task.isCancelled { throw SourceError.cancelled }
        var components = URLComponents(string: "https://api.harvestapi.io" + operation.path)!
        components.queryItems = parameters
        var request = URLRequest(url: components.url!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 90)
        request.httpMethod = "GET"; request.httpShouldHandleCookies = false
        request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let started = Date()
        do {
            let (bytes, response) = try await session.bytes(for: request, delegate: redirectDelegate)
            guard let http = response as? HTTPURLResponse else { throw SourceError.invalidResponse }
            switch http.statusCode {
            case 200..<300: break
            case 300..<400: throw SourceError.redirectRejected
            case 401: throw SourceError.unauthorized
            case 402: throw HarvestError.insufficientCredits
            case 403: throw SourceError.forbidden
            case 429: throw SourceError.rateLimited
            default: throw SourceError.http(http.statusCode)
            }
            var data = Data()
            for try await byte in bytes {
                try Task.checkCancellation()
                guard data.count < 4_000_000 else { throw SourceError.responseTooLarge }
                data.append(byte)
            }
            guard let original = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw SourceError.invalidResponse }
            // Error text can contain credentials; never surface or store it.
            if let error = original["error"], !(error is NSNull), String(describing: error) != "", String(describing: error) != "0" {
                throw SourceError.invalidResponse
            }
            guard let elements = original["elements"] as? [[String: Any]] else { throw SourceError.invalidResponse }
            let clean = sanitize(original) as? [String: Any] ?? [:]
            let cleanElements = clean["elements"] as? [[String: Any]] ?? []
            let completed = Date()
            var seen = Set<String>()
            let results = cleanElements.prefix(100).compactMap { element -> SourceSearchResult? in
                guard let result = Self.result(element, operation: operation, query: query, date: completed),
                      seen.insert(result.url).inserted else { return nil }
                return result
            }
            if !elements.isEmpty, results.isEmpty { throw SourceError.invalidResponse }
            let pagination = original["pagination"] as? [String: Any] ?? [:]
            let total = Self.nonnegativeInt(pagination["totalElements"])
            let pages = Self.nonnegativeInt(pagination["totalPages"])
            let raw = try JSONSerialization.data(withJSONObject: clean, options: [.sortedKeys, .withoutEscapingSlashes])
            var run = SourceCollectionRun(provider: .harvest, request: query, results: Array(results.prefix(query.limit)),
                rawResponse: String(decoding: raw, as: UTF8.self), startedAt: started, completedAt: completed,
                hasMore: (pages ?? 0) > 1 || (total ?? 0) > results.count || elements.count > query.limit ||
                         pagination["paginationToken"] as? String != nil)
            run.version = 3
            run.harvest = .init(operation: operation, targetURL: target, jobTitles: operation == .people ? jobTitles : nil,
                                receivedCount: elements.count, reportedCount: total, totalPages: pages)
            return run
        } catch let error as SourceError { throw error }
        catch let error as HarvestError { throw error }
        catch {
            if error is CancellationError || (error as? URLError)?.code == .cancelled { throw SourceError.cancelled }
            throw SourceError.network
        }
    }

    public static func linkedInURL(_ raw: String?, kind: HarvestOperation? = nil) -> String? {
        guard let raw, var parts = URLComponents(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["https", "http"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host?.lowercased(), host == "linkedin.com" || host.hasSuffix(".linkedin.com"),
              parts.user == nil, parts.password == nil, parts.port == nil else { return nil }
        let path = parts.path
        if let kind {
            let prefixes: [String] = kind == .companies ? ["/company/"] : kind == .people ? ["/in/"] : ["/posts/", "/feed/update/"]
            guard prefixes.contains(where: { path.hasPrefix($0) && path.count > $0.count }) else { return nil }
        }
        parts.scheme = "https"; parts.host = "www.linkedin.com"; parts.query = nil; parts.fragment = nil
        if parts.path.hasSuffix("/") { parts.path.removeLast() }
        return parts.url?.absoluteString
    }

    private static func nonnegativeInt(_ value: Any?) -> Int? {
        guard let n = value as? Int, n >= 0 else { return nil }; return n
    }

    private static func result(_ item: [String: Any], operation: HarvestOperation, query: SourceSearchRequest, date: Date) -> SourceSearchResult? {
        guard let url = linkedInURL(item["linkedinUrl"] as? String, kind: operation) else { return nil }
        var title = "", text = ""
        var metadata = LinkedInMetadata(operation: operation)
        switch operation {
        case .companies:
            title = item["name"] as? String ?? ""
            text = item["summary"] as? String ?? ""
        case .people:
            title = [item["firstName"], item["lastName"]].compactMap { $0 as? String }.joined(separator: " ")
            if title.isEmpty { title = item["name"] as? String ?? "" }
            metadata.positions = (item["currentPositions"] as? [[String: Any]] ?? []).prefix(30).map {
                LinkedInPosition(companyName: $0["companyName"] as? String ?? "", title: $0["title"] as? String ?? "",
                                 companyURL: linkedInURL($0["companyLinkedinUrl"] as? String, kind: .companies))
            }
            text = metadata.positions.map { [$0.title, $0.companyName].filter { !$0.isEmpty }.joined(separator: " · ") }.joined(separator: "\n")
        case .posts:
            let author = item["author"] as? [String: Any] ?? [:]
            metadata.authorName = author["name"] as? String
            metadata.authorURL = linkedInURL(author["linkedinUrl"] as? String)
            metadata.isRepost = item["repostId"] is String || item["repost"] is [String: Any] || item["repostedBy"] is [String: Any]
            let posted = item["postedAt"] as? [String: Any] ?? [:]
            metadata.publishedAt = posted["date"] as? String
            if metadata.publishedAt == nil, let timestamp = posted["timestamp"] as? Double, timestamp > 0 {
                metadata.publishedAt = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: timestamp > 10_000_000_000 ? timestamp / 1000 : timestamp))
            }
            text = item["content"] as? String ?? ""
            title = (metadata.isRepost ? "Репост · " : "Публикация · ") + (metadata.authorName ?? "Автор не указан")
        }
        guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, title.count <= 4096, text.count <= 100_000 else { return nil }
        return .init(request: query, url: url, title: title, text: text, metadata: metadata, retrievedAt: date)
    }

    private func sanitize(_ value: Any) -> Any {
        if let string = value as? String { return string.replacingOccurrences(of: apiKey, with: "[REDACTED]") }
        if let array = value as? [Any] { return array.map(sanitize) }
        if let object = value as? [String: Any] {
            let allowed: Set<String> = ["elements", "pagination", "status", "query", "search", "page",
                "id", "universalName", "name", "firstName", "lastName", "linkedinUrl", "summary",
                "industries", "location", "linkedinText", "followers", "headline",
                "currentPositions", "companyName", "title", "description", "companyId", "companyLinkedinUrl",
                "startedOn", "year", "month", "author", "content", "postedAt", "timestamp", "date",
                "repostId", "repost", "repostedBy", "publicIdentifier",
                "totalPages", "totalElements", "pageNumber", "previousElements", "pageSize", "paginationToken"]
            return object.reduce(into: [String: Any]()) { output, pair in
                if allowed.contains(pair.key) { output[pair.key] = sanitize(pair.value) }
            }
        }
        return value
    }
}

public enum HarvestError: Error, LocalizedError {
    case invalidCompanyURL, invalidProfileURL, insufficientCredits
    public var errorDescription: String? {
        switch self {
        case .invalidCompanyURL: return "Нужна ссылка на компанию: https://www.linkedin.com/company/…"
        case .invalidProfileURL: return "Нужна ссылка на человека: https://www.linkedin.com/in/…"
        case .insufficientCredits: return "HarvestAPI: недостаточно кредитов. Запрос не повторён."
        }
    }
}
