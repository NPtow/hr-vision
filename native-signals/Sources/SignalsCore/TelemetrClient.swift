import Foundation

public enum TelemetrError: Error, LocalizedError {
    case missingToken, unauthorized, forbidden, rateLimited, invalidResponse, network, cancelled
    case http(Int), unavailable(String)
    public var errorDescription: String? {
        switch self {
        case .missingToken: return "Введите ключ Telemetr API."
        case .unauthorized: return "Ключ API не принят (401). Проверьте его в кабинете Telemetr."
        case .forbidden: return "Telemetr отказал в доступе (403). Проверьте пакет и ограничения ключа."
        case .rateLimited: return "Лимит Telemetr исчерпан или запросы слишком частые (429). Автоповтора нет."
        case .invalidResponse: return "Ответ Telemetr не соответствует ожидаемому формату."
        case .network: return "Не удалось связаться с Telemetr. Запрос автоматически не повторён."
        case .cancelled: return "Сбор остановлен. Уже полученные публикации сохранены в результатах."
        case .http(let status): return "Telemetr вернул HTTP \(status). Запрос автоматически не повторён."
        case .unavailable(let reason): return reason
        }
    }
}

public final class TelemetrClient {
    private let token: String
    private let session: URLSession
    public init(token: String, session: URLSession = .shared) {
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines); self.session = session
    }

    public func fetchLimits() async throws -> TelemetrLimits {
        let (data, headers) = try await get(path: "/v1/limits")
        do {
            var limits = try JSONDecoder().decode(TelemetrLimits.self, from: data)
            limits.sampledAt = Date(); limits.rateLimitHeaders = headers
            return limits
        } catch { throw TelemetrError.invalidResponse }
    }

    /// One fixed pilot: three paid searches, at most 20 requested posts, no pagination or retry.
    /// Partial successful responses remain available even when a subsequent request fails.
    public func collectPilot() async throws -> TelemetrCollectionRun {
        var run = TelemetrCollectionRun(origin: .telemetrAPI)
        let before = try await fetchLimits()
        if let reason = before.companySearchBlockReason { throw TelemetrError.unavailable(reason) }
        run.limitsBefore = before
        for query in TelemetrQuery.pilot {
            do {
                try Task.checkCancellation()
                let result = try await search(query)
                run.queries.append(result)
            } catch {
                let message = safeMessage(error)
                run.queries.append(.init(query: query, posts: [], rateLimitHeaders: (error as? RequestFailure)?.headers ?? [:], errorMessage: message))
                run.errorMessage = message; run.status = .partial
                break
            }
        }
        do { run.limitsAfter = try await fetchLimits() }
        catch {
            run.errorMessage = (run.errorMessage.map { $0 + " " } ?? "") + "Итоговый расход не подтверждён: " + safeMessage(error)
            run.status = .partial
        }
        run.completedAt = Date()
        return run
    }

    private func search(_ query: TelemetrQuery) async throws -> TelemetrQueryResult {
        let (data, headers) = try await get(path: "/channels/posts/search", items: [
            URLQueryItem(name: "query", value: query.query), URLQueryItem(name: "limit", value: String(query.limit)),
            URLQueryItem(name: "offset", value: "0"), URLQueryItem(name: "peerType", value: "channel")
        ])
        let raw = String(data: data, encoding: .utf8)?.replacingOccurrences(of: token, with: "[REDACTED]")
        let envelope: SearchEnvelope
        do { envelope = try JSONDecoder().decode(SearchEnvelope.self, from: Data((raw ?? "").utf8)) }
        catch { throw TelemetrError.invalidResponse }
        guard envelope.status == "ok", let response = envelope.response else { throw TelemetrError.invalidResponse }
        let now = Date()
        var seen = Set<String>()
        let posts = response.items.prefix(query.limit).map { post in
            TelemetrPost(companyID: query.companyID, companyName: query.companyName, query: query.query,
                         channelID: post.channelID, postID: post.id, channelTitle: post.channel?.title,
                         text: post.text ?? "", sourceURL: Self.normalizeSourceURL(post.link),
                         publishedAt: post.date.map { Date(timeIntervalSince1970: $0) }, retrievedAt: now, origin: .telemetrAPI)
        }.filter { seen.insert($0.id).inserted }
        // Only a successful response body is retained. Never persist request headers or server error text.
        return .init(query: query, posts: posts, totalCount: response.totalCount, rawResponseJSON: raw, rateLimitHeaders: headers)
    }

    private func get(path: String, items: [URLQueryItem] = []) async throws -> (Data, [String: String]) {
        guard !token.isEmpty, !token.contains("\n"), !token.contains("\r") else { throw TelemetrError.missingToken }
        var components = URLComponents(string: "https://api.telemetr.me" + path)!
        if !items.isEmpty { components.queryItems = items }
        var request = URLRequest(url: components.url!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "GET"; request.httpShouldHandleCookies = false
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch {
            if error is CancellationError || (error as? URLError)?.code == .cancelled { throw TelemetrError.cancelled }
            throw TelemetrError.network
        }
        guard let http = response as? HTTPURLResponse else { throw TelemetrError.invalidResponse }
        var headers: [String: String] = [:]
        for (key, value) in http.allHeaderFields {
            let name = String(describing: key).lowercased(), value = String(describing: value)
            // Preserve quota metadata only. These documented headers contain nonnegative integers.
            if (name.hasPrefix("x-ratelimit-") || name.hasPrefix("x-quota-")), Int64(value) != nil { headers[name] = value }
        }
        let failure: TelemetrError?
        switch http.statusCode {
        case 200..<300: failure = nil
        case 401: failure = .unauthorized
        case 403: failure = .forbidden
        case 429: failure = .rateLimited
        default: failure = .http(http.statusCode)
        }
        if let failure { throw RequestFailure(error: failure, headers: headers) }
        return (data, headers)
    }

    private func safeMessage(_ error: Error) -> String {
        if error is CancellationError { return TelemetrError.cancelled.localizedDescription }
        if let failure = error as? RequestFailure { return failure.localizedDescription }
        return (error as? TelemetrError)?.localizedDescription ?? TelemetrError.network.localizedDescription
    }

    private static func normalizeSourceURL(_ source: String?) -> String? {
        guard var source, !source.isEmpty else { return nil }
        if source.hasPrefix("t.me/") || source.hasPrefix("telegram.me/") { source = "https://" + source }
        guard let url = URL(string: source), ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil else { return nil }
        return source
    }
}

private struct RequestFailure: Error, LocalizedError {
    var error: TelemetrError
    var headers: [String: String]
    var errorDescription: String? { error.localizedDescription }
}

private struct SearchEnvelope: Decodable {
    var status: String
    var response: SearchResponse?
}
private struct SearchResponse: Decodable {
    var totalCount: Int?
    var items: [APIPost]
    enum CodingKeys: String, CodingKey { case totalCount = "total_count"; case items }
}
private struct APIPost: Decodable {
    var id: String
    var channelID: String
    var text: String?
    var link: String?
    var date: Double?
    var channel: Channel?
    struct Channel: Decodable { var title: String? }
    enum CodingKeys: String, CodingKey { case id, text, link, date, channel; case channelID = "channel_id" }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func identifier(_ key: CodingKeys) throws -> String {
            if let value = try? c.decode(String.self, forKey: key), !value.isEmpty { return value }
            return String(try c.decode(Int64.self, forKey: key))
        }
        id = try identifier(.id); channelID = try identifier(.channelID)
        text = try c.decodeIfPresent(String.self, forKey: .text)
        link = try c.decodeIfPresent(String.self, forKey: .link)
        channel = try c.decodeIfPresent(Channel.self, forKey: .channel)
        if let value = try? c.decode(Double.self, forKey: .date) { date = value }
        else if let value = try? c.decode(String.self, forKey: .date) { date = Double(value) }
        else { date = nil }
    }
}
