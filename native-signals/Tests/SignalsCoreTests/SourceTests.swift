import Foundation
import XCTest
@testable import SignalsCore

final class SourceTests: XCTestCase {
    private let key = "test-secret-source-only-98765"
    private let query = SourceSearchRequest(companyID: "setters", companyName: "SETTERS Education", query: "SETTERS Education")

    func testSerperUsesOneBoundedRequestAndPreservesSnippetProvenance() async throws {
        let recorder = SourceRecorder { _ in
            (200, [:], self.json(["organic": [
                ["title": "Школа", "link": "https://example.com/course", "snippet": "Фрагмент " + self.key],
                ["title": "Дубль", "link": "https://example.com/course"],
                ["title": "Unsafe", "link": "javascript:alert(1)"],
                ["title": "SecretURL", "link": "https://user:password@example.com"]
            ], "X-API-KEY": self.key]))
        }
        let run = try await client(.serper, recorder).search(query)
        XCTAssertEqual(recorder.requests.count, 1)
        let request = try XCTUnwrap(recorder.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://google.serper.dev/search")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-KEY"), key)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        let body = try XCTUnwrap(recorder.bodies.first)
        XCTAssertEqual(body["q"] as? String, query.query)
        XCTAssertEqual(body["gl"] as? String, "ru")
        XCTAssertEqual(body["hl"] as? String, "ru")
        XCTAssertEqual(body["num"] as? Int, 10)
        XCTAssertEqual(run.results.count, 1)
        XCTAssertEqual(run.results.first?.snippet, "Фрагмент [REDACTED]")
        let evidence = try XCTUnwrap(run.results.first).evidence(collectionID: run.id)
        XCTAssertEqual(evidence.provenance?.contentType, "searchSnippet")
        XCTAssertEqual(evidence.provenance?.query, query.query)
        XCTAssertEqual(evidence.provenance?.collectionID, run.id)
        XCTAssertFalse(evidence.isDemo)
        XCTAssertFalse(String(data: try SourceCollectionStore.encode(run), encoding: .utf8)!.contains(key))
    }

    func testYandexDecodesXMLHighlightEntitiesAndMultiplePassages() async throws {
        let xml = """
        <?xml version="1.0" encoding="utf-8"?><yandexsearch><response><results><grouping><group>
        <doc><url>https://example.ru/a?x=1&amp;y=2</url><title>Школа <hlword>образования</hlword></title>
        <passages><passage>Первый &amp; второй</passage><passage>Ещё <hlword>текст</hlword></passage></passages></doc>
        </group></grouping></results></response></yandexsearch>
        """
        let recorder = SourceRecorder { _ in (200, [:], self.json(["rawData": Data(xml.utf8).base64EncodedString()])) }
        let run = try await client(.yandex, recorder, folderID: "folder-test").search(query)
        let request = try XCTUnwrap(recorder.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://searchapi.api.cloud.yandex.net/v2/web/search")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Api-Key " + key)
        let body = try XCTUnwrap(recorder.bodies.first)
        XCTAssertEqual(body["folderId"] as? String, "folder-test")
        XCTAssertEqual(body["responseFormat"] as? String, "FORMAT_XML")
        XCTAssertEqual((body["groupSpec"] as? [String: String])?["groupsOnPage"], "10")
        XCTAssertEqual((body["groupSpec"] as? [String: String])?["docsInGroup"], "1")
        XCTAssertEqual(run.rawFormat, "xml")
        XCTAssertEqual(run.results.first?.title, "Школа образования")
        XCTAssertEqual(run.results.first?.snippet, "Первый & второй\nЕщё текст")
        XCTAssertEqual(run.results.first?.url, "https://example.ru/a?x=1&y=2")
        XCTAssertEqual(recorder.requests.count, 1)
    }

    func testYandexRejectsExternalEntitiesAndErrorEnvelopes() async throws {
        for xml in [
            "<!DOCTYPE x [<!ENTITY y SYSTEM 'file:///etc/passwd'>]><yandexsearch><response>&y;</response></yandexsearch>",
            "<yandexsearch><response><error code='42'>" + key + "</error></response></yandexsearch>",
            "<html><body>not a search response</body></html>"
        ] {
            let recorder = SourceRecorder { _ in (200, [:], self.json(["rawData": Data(xml.utf8).base64EncodedString()])) }
            do { _ = try await client(.yandex, recorder, folderID: "folder-test").search(query); XCTFail("Must reject XML") }
            catch { XCTAssertEqual(error as? SourceError, .invalidResponse); XCTAssertFalse(error.localizedDescription.contains(key)) }
            XCTAssertEqual(recorder.requests.count, 1)
        }
    }

    func testYandexNoResultsIsSuccessfulAndCancellationDoesNotSend() async throws {
        let xml = "<yandexsearch><response><error code='15'>No results found</error></response></yandexsearch>"
        let recorder = SourceRecorder { _ in (200, [:], self.json(["rawData": Data(xml.utf8).base64EncodedString()])) }
        let source = client(.yandex, recorder, folderID: "folder-test")
        let run = try await source.search(query)
        XCTAssertTrue(run.results.isEmpty)
        XCTAssertEqual(recorder.requests.count, 1)
        let operation = Task {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            return try await source.search(query)
        }
        operation.cancel()
        do { _ = try await operation.value; XCTFail("Cancelled call must not send") }
        catch { XCTAssertEqual(error as? SourceError, .cancelled) }
        XCTAssertEqual(recorder.requests.count, 1)
    }

    func testEmptyCredentialsAndInvalidQueriesNeverSendRequests() async throws {
        let recorder = SourceRecorder { _ in (200, [:], self.json(["organic": []])) }
        for badKey in ["", "  ", "foo\nbar", "secret\tkey"] {
            do { _ = try await client(.serper, recorder, key: badKey).search(query); XCTFail("Must require key") }
            catch { XCTAssertEqual(error as? SourceError, .missingKey) }
        }
        do { _ = try await client(.yandex, recorder).search(query); XCTFail("Must require folder") }
        catch { XCTAssertEqual(error as? SourceError, .missingFolderID) }
        var excessive = query; excessive.limit = 11
        do { _ = try await client(.serper, recorder).search(excessive); XCTFail("Must bound limit") }
        catch { XCTAssertEqual(error as? SourceError, .invalidQuery) }
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testHTTPFailuresAndRedirectNeverLeakOrRetry() async throws {
        for status in [301, 401, 403, 429, 500] {
            let recorder = SourceRecorder { _ in (status, ["Location": "https://example.org/other"], Data(self.key.utf8)) }
            do { _ = try await client(.serper, recorder).search(query); XCTFail("Must reject HTTP") }
            catch { XCTAssertFalse(error.localizedDescription.contains(key)) }
            XCTAssertEqual(recorder.requests.count, 1)
        }
        let delegate = SourceRedirectDelegate()
        let redirected = URLRequest(url: URL(string: "https://another-host.example/")!)
        let response = HTTPURLResponse(url: URL(string: "https://google.serper.dev/search")!, statusCode: 302, httpVersion: nil, headerFields: nil)!
        var called = false
        delegate.urlSession(.shared, task: URLSession.shared.dataTask(with: redirected), willPerformHTTPRedirection: response, newRequest: redirected) {
            XCTAssertNil($0); called = true
        }
        XCTAssertTrue(called)
    }

    func testMCPDiscoversOnlyToolsWithProtocolAndSessionHeaders() async throws {
        let recorder = SourceRecorder { request in
            let body = SourceRecorder.body(request)
            switch body["method"] as? String {
            case "initialize":
                return (200, ["Mcp-Session-Id": "session-not-for-disk"], self.json([
                    "jsonrpc": "2.0", "id": 1, "result": ["protocolVersion": "2025-06-18", "capabilities": ["tools": [:]], "instructions": self.key]
                ]))
            case "notifications/initialized": return (202, [:], Data())
            case "tools/list":
                return (200, ["Content-Type": "text/event-stream"], Data("""
                event: message
                data: {"jsonrpc":"2.0","method":"notifications/message","params":{"data":"ignore"}}

                event: message
                data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"search_vk","description":"Описание","inputSchema":{"type":"object","properties":{"token":{"type":"string"}}}}],"nextCursor":"next-page"}}


                """.utf8))
            default: XCTFail("Unexpected or unsafe MCP method"); return (500, [:], Data())
            }
        }
        let run = try await client(.targetHunter, recorder).discoverTools()
        XCTAssertEqual(recorder.bodies.compactMap { $0["method"] as? String }, ["initialize", "notifications/initialized", "tools/list"])
        XCTAssertNil(recorder.requests.first?.value(forHTTPHeaderField: "Mcp-Session-Id"))
        for request in recorder.requests.dropFirst() {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Mcp-Session-Id"), "session-not-for-disk")
            XCTAssertEqual(request.value(forHTTPHeaderField: "MCP-Protocol-Version"), "2025-06-18")
        }
        for request in recorder.requests {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer " + key)
            XCTAssertEqual(request.url?.absoluteString, "https://vk-mcp.targethunter.ru/mcp")
        }
        XCTAssertEqual(run.tools.map(\.name), ["search_vk"])
        XCTAssertTrue(run.tools[0].inputSchemaJSON.contains("token"))
        XCTAssertTrue(run.hasMore)
        XCTAssertTrue(run.results.isEmpty)
        let stored = String(data: try SourceCollectionStore.encode(run), encoding: .utf8)!
        XCTAssertFalse(stored.contains("session-not-for-disk"))
        XCTAssertFalse(stored.contains(key))
    }

    func testMCPRejectsUnexpectedProtocolAndSanitizesRPCError() async throws {
        for response in [
            ["jsonrpc": "2.0", "id": 1, "result": ["protocolVersion": "future", "capabilities": ["tools": [:]]]] as [String: Any],
            ["jsonrpc": "2.0", "id": 1, "error": ["code": -1, "message": key]]
        ] {
            let recorder = SourceRecorder { _ in (200, [:], self.json(response)) }
            do { _ = try await client(.targetHunter, recorder).discoverTools(); XCTFail("Should reject") }
            catch { XCTAssertFalse(error.localizedDescription.contains(key)) }
            XCTAssertEqual(recorder.requests.count, 1)
        }
    }

    func testStoreRoundtripAndCorruptionBlocksOverwrite() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SourceCollectionStore(directoryURL: directory)
        let result = SourceSearchResult(provider: .serper, request: query, url: "https://example.com", title: "Example", snippet: "Extract")
        var run = SourceCollectionRun(provider: .serper, request: query, results: [result], rawResponse: "{\"organic\":[]}")
        try store.save(run)
        XCTAssertEqual(try store.loadRuns().first?.results.first?.snippet, "Extract")
        XCTAssertEqual(try store.loadRuns().first?.results.first?.id, result.id)
        run.id = "../escape"
        XCTAssertThrowsError(try store.save(run))
        run.id = "other"; run.version = 2
        XCTAssertThrowsError(try store.save(run))
        run.version = 1; run.results[0].companyID = "mismatched"
        XCTAssertThrowsError(try store.save(run))
        let original = try XCTUnwrap(try store.loadRuns().first)
        let path = directory.appendingPathComponent(original.id + ".json")
        let corrupt = Data("{broken".utf8)
        try corrupt.write(to: path)
        XCTAssertThrowsError(try store.loadRuns())
        XCTAssertThrowsError(try store.save(original))
        XCTAssertEqual(try Data(contentsOf: path), corrupt)
    }

    func testEvidenceIdentityDeduplicatesIdenticalContentButRetainsChangedSnippet() {
        let first = SourceSearchResult(provider: .serper, request: query, url: "https://example.com", title: "Example", snippet: "Old")
        let repeated = SourceSearchResult(provider: .serper, request: query, url: "https://example.com", title: "Example", snippet: "Old", retrievedAt: .distantFuture)
        let changed = SourceSearchResult(provider: .serper, request: query, url: "https://example.com", title: "Example", snippet: "New")
        XCTAssertEqual(first.id, repeated.id)
        XCTAssertNotEqual(first.id, changed.id)
    }

    private func client(_ provider: SourceProvider, _ recorder: SourceRecorder, key: String? = nil, folderID: String = "") -> SourceClient {
        SourceURLProtocol.recorder = recorder
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [SourceURLProtocol.self]
        return SourceClient(provider: provider, apiKey: key ?? self.key, folderID: folderID, configuration: config)
    }
    private func json(_ object: [String: Any]) -> Data { try! JSONSerialization.data(withJSONObject: object) }
}

private final class SourceRecorder {
    typealias Handler = (URLRequest) -> (Int, [String: String], Data)
    let handler: Handler
    var requests: [URLRequest] = []
    var bodies: [[String: Any]] = []
    init(_ handler: @escaping Handler) { self.handler = handler }
    static func body(_ request: URLRequest) -> [String: Any] {
        guard let data = request.httpBody else { return [:] }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    }
}
private final class SourceURLProtocol: URLProtocol {
    static var recorder: SourceRecorder?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let recorder = Self.recorder else { fatalError("Missing fixture") }
        var normalized = request
        if normalized.httpBody == nil, let stream = normalized.httpBodyStream {
            stream.open(); defer { stream.close() }
            var data = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(contentsOf: buffer.prefix(count))
            }
            normalized.httpBody = data
        }
        recorder.requests.append(normalized); recorder.bodies.append(SourceRecorder.body(normalized))
        let (status, headers, data) = recorder.handler(normalized)
        let response = HTTPURLResponse(url: normalized.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
