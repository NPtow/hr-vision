import Foundation
#if canImport(FoundationXML)
import FoundationXML
#endif

/// Single explicit operations only. No provider balance probes, retries, pagination, or tools/call.
public final class SourceClient {
    private let provider: SourceProvider
    private let apiKey: String
    private let folderID: String
    private let session: URLSession
    private let redirectDelegate = SourceRedirectDelegate()
    private let maximumBytes = 4_000_000

    public init(provider: SourceProvider, apiKey: String, folderID: String = "", configuration: URLSessionConfiguration? = nil) {
        self.provider = provider; self.apiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        self.folderID = folderID.trimmingCharacters(in: .whitespacesAndNewlines)
        let config = (configuration?.copy() as? URLSessionConfiguration) ?? .ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.httpShouldSetCookies = false; config.httpAdditionalHeaders = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 45
        session = URLSession(configuration: config)
    }

    deinit { session.invalidateAndCancel() }

    public func search(_ query: SourceSearchRequest) async throws -> SourceCollectionRun {
        try validateCredentials()
        guard provider != .targetHunter, provider != .harvest else { throw SourceError.unsupportedOperation }
        guard !query.companyID.isEmpty, !query.companyName.isEmpty,
              !query.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              query.query.count <= 400, (1...10).contains(query.limit) else { throw SourceError.invalidQuery }
        if provider == .yandex, query.query.split(whereSeparator: { $0.isWhitespace }).count > 40 { throw SourceError.invalidQuery }
        let started = Date()
        let data: Data
        if provider == .serper {
            data = try await send(body: ["q": query.query, "gl": "ru", "hl": "ru", "num": query.limit]).data
            let object = try jsonObject(data)
            guard let organic = object["organic"] as? [[String: Any]] else { throw SourceError.invalidResponse }
            var seen = Set<String>()
            let results = organic.prefix(query.limit).compactMap { item -> SourceSearchResult? in
                guard let url = Self.safeURL(item["link"] as? String), seen.insert(url).inserted else { return nil }
                return SourceSearchResult(provider: provider, request: query, url: url,
                                          title: item["title"] as? String ?? url,
                                          snippet: item["snippet"] as? String ?? "")
            }
            return SourceCollectionRun(provider: provider, request: query, results: results,
                                       rawResponse: try jsonString(object), startedAt: started)
        }
        data = try await send(body: [
            "folderId": folderID,
            "query": ["searchType": "SEARCH_TYPE_RU", "queryText": query.query, "page": "0"],
            "responseFormat": "FORMAT_XML",
            "groupSpec": ["groupMode": "GROUP_MODE_FLAT", "groupsOnPage": String(query.limit), "docsInGroup": "1"]
        ]).data
        let object = try jsonObject(data)
        guard let raw = object["rawData"] as? String, let decoded = Data(base64Encoded: raw),
              decoded.count <= maximumBytes, let xml = String(data: decoded, encoding: .utf8) else { throw SourceError.invalidResponse }
        let safeXML = redact(xml)
        let parser = SourceYandexXML()
        let documents = try parser.documents(in: safeXML)
        var seen = Set<String>()
        let results = documents.prefix(query.limit).compactMap { item -> SourceSearchResult? in
            guard let url = Self.safeURL(item.url), seen.insert(url).inserted else { return nil }
            return SourceSearchResult(provider: provider, request: query, url: url,
                                      title: item.title.isEmpty ? url : item.title, snippet: item.snippet)
        }
        return SourceCollectionRun(provider: provider, request: query, results: results,
                                   rawResponse: safeXML, rawFormat: "xml", startedAt: started)
    }

    /// Only reads the server's tool catalogue. Tool names are not guessed or executed.
    public func discoverTools() async throws -> SourceCollectionRun {
        try validateCredentials()
        guard provider == .targetHunter else { throw SourceError.unsupportedOperation }
        let started = Date()
        let initialized = try await send(body: [
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": ["protocolVersion": "2025-06-18", "capabilities": [:],
                       "clientInfo": ["name": "tg-signals", "version": "0.4.0"]]
        ], rpcID: 1)
        let initialization = try rpcResult(initialized.data, id: 1)
        guard let protocolVersion = initialization["protocolVersion"] as? String,
              ["2025-03-26", "2025-06-18", "2025-11-25"].contains(protocolVersion) else { throw SourceError.unsupportedProtocol }
        guard (initialization["capabilities"] as? [String: Any])?["tools"] != nil else { throw SourceError.invalidResponse }
        let sessionID = initialized.http.value(forHTTPHeaderField: "Mcp-Session-Id")
        if let sessionID, !validHeader(sessionID) { throw SourceError.invalidResponse }
        _ = try await send(body: ["jsonrpc": "2.0", "method": "notifications/initialized"],
                           sessionID: sessionID, protocolVersion: protocolVersion, notification: true)
        let listed = try await send(body: ["jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": [:]],
                                    sessionID: sessionID, protocolVersion: protocolVersion, rpcID: 2)
        let result = try rpcResult(listed.data, id: 2)
        guard let entries = result["tools"] as? [[String: Any]] else { throw SourceError.invalidResponse }
        var tools: [SourceMCPTool] = []
        var seen = Set<String>()
        for entry in entries.prefix(100) {
            guard let name = entry["name"] as? String, !name.isEmpty, name.count <= 256,
                  let schema = entry["inputSchema"] as? [String: Any], seen.insert(name).inserted else { throw SourceError.invalidResponse }
            tools.append(.init(name: name, description: entry["description"] as? String ?? "", inputSchemaJSON: try jsonString(schema)))
        }
        // Initialization instructions and session identifiers are deliberately not stored.
        let retained: [String: Any] = ["protocolVersion": protocolVersion, "tools": Array(entries.prefix(100)),
                                       "hasMore": result["nextCursor"] != nil || entries.count > 100]
        return SourceCollectionRun(provider: provider, tools: tools, rawResponse: try jsonString(retained),
                                   startedAt: started, hasMore: result["nextCursor"] != nil || entries.count > 100)
    }

    static func safeURL(_ text: String?) -> String? {
        guard let text, let url = URL(string: text), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil else { return nil }
        return url.absoluteString
    }

    private func validateCredentials() throws {
        guard validHeader(apiKey), apiKey.count <= 4096 else { throw SourceError.missingKey }
        if provider == .yandex {
            let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
            guard !folderID.isEmpty, folderID.count <= 100, folderID.unicodeScalars.allSatisfy(allowed.contains) else { throw SourceError.missingFolderID }
        }
    }
    private func validHeader(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.allSatisfy { $0 >= 33 && $0 <= 126 }
    }

    private func send(body: [String: Any], sessionID: String? = nil, protocolVersion: String? = nil,
                      rpcID: Int? = nil, notification: Bool = false) async throws -> (data: Data, http: HTTPURLResponse) {
        if Task.isCancelled { throw SourceError.cancelled }
        let endpoint: String
        switch provider {
        case .serper: endpoint = "https://google.serper.dev/search"
        case .yandex: endpoint = "https://searchapi.api.cloud.yandex.net/v2/web/search"
        case .targetHunter: endpoint = "https://vk-mcp.targethunter.ru/mcp"
        case .harvest: throw SourceError.unsupportedOperation
        }
        var request = URLRequest(url: URL(string: endpoint)!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "POST"; request.httpShouldHandleCookies = false
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(provider == .targetHunter ? "application/json, text/event-stream" : "application/json", forHTTPHeaderField: "Accept")
        switch provider {
        case .serper: request.setValue(apiKey, forHTTPHeaderField: "X-API-KEY")
        case .yandex: request.setValue("Api-Key " + apiKey, forHTTPHeaderField: "Authorization")
        case .targetHunter: request.setValue("Bearer " + apiKey, forHTTPHeaderField: "Authorization")
        case .harvest: throw SourceError.unsupportedOperation
        }
        if let sessionID { request.setValue(sessionID, forHTTPHeaderField: "Mcp-Session-Id") }
        if let protocolVersion { request.setValue(protocolVersion, forHTTPHeaderField: "MCP-Protocol-Version") }
        do {
            let (bytes, response) = try await session.bytes(for: request, delegate: redirectDelegate)
            guard let http = response as? HTTPURLResponse else { throw SourceError.invalidResponse }
            switch http.statusCode {
            case 200..<300: break
            case 300..<400: throw SourceError.redirectRejected
            case 401: throw SourceError.unauthorized
            case 403: throw SourceError.forbidden
            case 429: throw SourceError.rateLimited
            default: throw SourceError.http(http.statusCode)
            }
            if notification { return (Data(), http) }
            var data = Data()
            if http.value(forHTTPHeaderField: "Content-Type")?.lowercased().contains("text/event-stream") == true {
                guard let rpcID else { throw SourceError.invalidResponse }
                var event = "", line = Data()
                var total = 0
                for try await byte in bytes {
                    try Task.checkCancellation()
                    total += 1
                    guard total <= maximumBytes else { throw SourceError.responseTooLarge }
                    if byte == 10 {
                        if let responseData = try appendSSELine(line, event: &event, id: rpcID) { return (responseData, http) }
                        line.removeAll(keepingCapacity: true)
                    } else {
                        line.append(byte)
                    }
                }
                if !line.isEmpty, let responseData = try appendSSELine(line, event: &event, id: rpcID) { return (responseData, http) }
                if let responseData = matchingRPCEvent(event, id: rpcID) { return (responseData, http) }
                throw SourceError.invalidResponse
            }
            for try await byte in bytes {
                try Task.checkCancellation()
                guard data.count < maximumBytes else { throw SourceError.responseTooLarge }
                data.append(byte)
            }
            return (data, http)
        } catch let error as SourceError { throw error }
        catch {
            if error is CancellationError || (error as? URLError)?.code == .cancelled { throw SourceError.cancelled }
            throw SourceError.network
        }
    }

    private func appendSSELine(_ data: Data, event: inout String, id: Int) throws -> Data? {
        guard var line = String(data: data, encoding: .utf8) else { throw SourceError.invalidResponse }
        if line.hasSuffix("\r") { line.removeLast() }
        if line.isEmpty {
            let result = matchingRPCEvent(event, id: id)
            event = ""
            return result
        }
        if line.hasPrefix("data:") {
            var value = String(line.dropFirst(5))
            if value.hasPrefix(" ") { value.removeFirst() }
            event += value + "\n"
        }
        return nil
    }

    private func matchingRPCEvent(_ event: String, id: Int) -> Data? {
        let data = Data(event.utf8)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["id"] as? Int == id else { return nil }
        return data
    }
    private func rpcResult(_ data: Data, id: Int) throws -> [String: Any] {
        let envelope = try jsonObject(data)
        guard envelope["jsonrpc"] as? String == "2.0", envelope["id"] as? Int == id else { throw SourceError.invalidResponse }
        if envelope["error"] != nil { throw SourceError.remoteRPC }
        guard let result = envelope["result"] as? [String: Any] else { throw SourceError.invalidResponse }
        return result
    }
    private func redact(_ string: String) -> String { string.replacingOccurrences(of: apiKey, with: "[REDACTED]") }
    private func sanitize(_ value: Any) -> Any {
        if let string = value as? String { return redact(string) }
        if let list = value as? [Any] { return list.map(sanitize) }
        if let object = value as? [String: Any] {
            let sensitive = Set(["authorization", "x-api-key", "api-key", "apikey", "api_key", "access_token", "refresh_token", "token", "mcp-session-id"])
            return object.reduce(into: [String: Any]()) { output, pair in
                // Preserve schema properties named `token`; only credential values are omitted.
                if !(sensitive.contains(pair.key.lowercased()) && pair.value is String) { output[redact(pair.key)] = sanitize(pair.value) }
            }
        }
        return value
    }
    private func jsonObject(_ data: Data) throws -> [String: Any] {
        guard let raw = try? JSONSerialization.jsonObject(with: data),
              let object = sanitize(raw) as? [String: Any] else { throw SourceError.invalidResponse }
        return object
    }
    private func jsonString(_ object: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: sanitize(object), options: [.sortedKeys, .withoutEscapingSlashes])
        guard let string = String(data: data, encoding: .utf8) else { throw SourceError.invalidResponse }
        return string
    }
}

final class SourceRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private final class SourceYandexXML: NSObject, XMLParserDelegate {
    struct Document { var url = ""; var title = ""; var snippet = "" }
    private var output: [Document] = []
    private var current: Document?
    private var field: String?
    private var content = ""
    private var validRoot = false
    private var validResponse = false
    private var hasError = false
    private var noResults = false

    func documents(in xml: String) throws -> [Document] {
        // Search results never need DTDs or external entity resolution.
        guard !xml.localizedCaseInsensitiveContains("<!DOCTYPE"), !xml.localizedCaseInsensitiveContains("<!ENTITY") else { throw SourceError.invalidResponse }
        let parser = XMLParser(data: Data(xml.utf8)); parser.shouldResolveExternalEntities = false
        parser.externalEntityResolvingPolicy = .never; parser.delegate = self
        guard parser.parse(), validRoot, validResponse, !hasError else { throw SourceError.invalidResponse }
        if noResults { return [] }
        return output
    }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes attributeDict: [String: String]) {
        if elementName == "yandexsearch" { validRoot = true }
        if elementName == "response" { validResponse = true }
        if elementName == "error" {
            // Yandex's documented code 15 means the query returned no matching documents.
            if attributeDict["code"] == "15" { noResults = true }
            else { hasError = true }
        }
        if elementName == "doc" { current = Document() }
        if current != nil, ["url", "title", "passage"].contains(elementName) { field = elementName; content = "" }
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) { if field != nil { content += string } }
    func parser(_ parser: XMLParser, foundCDATA CDATABlock: Data) { if field != nil { content += String(data: CDATABlock, encoding: .utf8) ?? "" } }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        if let field, field == elementName {
            let text = content.trimmingCharacters(in: .whitespacesAndNewlines)
            switch field {
            case "url": current?.url = text
            case "title": current?.title = text
            case "passage":
                let separator = (current?.snippet.isEmpty ?? true) ? "" : "\n"
                current?.snippet += separator + text
            default: break
            }
            self.field = nil; content = ""
        }
        if elementName == "doc", let current { output.append(current); self.current = nil }
    }
}
