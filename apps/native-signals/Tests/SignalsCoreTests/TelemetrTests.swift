import Foundation
import XCTest
@testable import SignalsCore

final class TelemetrTests: XCTestCase {
    private let token = "test-only-secret-never-a-real-key"

    func testPaidTelegramGateAndLegacyUnmeteredGrant() throws {
        let paid = try JSONDecoder().decode(TelemetrLimits.self, from: limitsData())
        XCTAssertNil(paid.companySearchBlockReason)
        XCTAssertEqual(paid.requests?.resetAt, 1800000000)
        for model in ["trial", "none", "future-unknown"] {
            var limits = paid; limits.model = model
            XCTAssertNotNil(limits.companySearchBlockReason)
        }
        var wrong = paid; wrong.platform = "max"
        XCTAssertNotNil(wrong.companySearchBlockReason)
        wrong.platform = nil
        XCTAssertNotNil(wrong.companySearchBlockReason)
        var low = paid; low.posts?.remaining = 19
        XCTAssertNotNil(low.companySearchBlockReason)
        low = paid; low.requests?.remaining = 2
        XCTAssertNotNil(low.companySearchBlockReason)
        var legacy = paid; legacy.model = "monthly"; legacy.posts = .init(limit: 0, used: 0, remaining: 0)
        XCTAssertNil(legacy.companySearchBlockReason)
    }

    func testTrialDoesNotSendAnyPaidSearch() async throws {
        let recorder = StubRecorder { _ in (200, [:], self.limitsData(model: "trial")) }
        let client = client(recorder)
        do { _ = try await client.collectPilot(); XCTFail("Trial must not search companies") }
        catch { XCTAssertTrue(error.localizedDescription.contains("демо-канале")) }
        XCTAssertEqual(recorder.paths, ["/v1/limits"])
    }

    func testPilotHasThreeBoundedSearchesAndFreeQuotaSnapshots() async throws {
        let recorder = StubRecorder { request in
            if request.url!.path == "/v1/limits" { return (200, [:], self.limitsData()) }
            let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
            let limit = Int(items.first { $0.name == "limit" }!.value!)!
            let query = items.first { $0.name == "query" }!.value!
            return (200, ["X-RateLimit-Requests-Remaining": "99", "Authorization": "do-not-save"], self.postsData(count: limit, query: query))
        }
        let run = try await client(recorder).collectPilot()
        XCTAssertEqual(run.status, .complete)
        XCTAssertEqual(run.queries.map(\.query.limit), [7, 7, 6])
        XCTAssertEqual(run.posts.count, 20)
        XCTAssertEqual(recorder.paths, ["/v1/limits", "/channels/posts/search", "/channels/posts/search", "/channels/posts/search", "/v1/limits"])
        XCTAssertNotNil(run.limitsBefore); XCTAssertNotNil(run.limitsAfter)
        for result in run.queries {
            XCTAssertEqual(result.rateLimitHeaders, ["x-ratelimit-requests-remaining": "99"])
            XCTAssertTrue(result.posts.allSatisfy { $0.relevance == .unreviewed && $0.origin == .telemetrAPI })
            XCTAssertEqual(result.posts.first?.sourceURL, "https://t.me/channel/1")
            XCTAssertEqual(result.posts.first?.publishedAt, Date(timeIntervalSince1970: 1791700000))
        }
        for request in recorder.requests {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer " + token)
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.host, "api.telemetr.me")
            XCTAssertFalse(request.url!.absoluteString.contains(token))
        }
    }

    func testPartialRunRetainsSuccessAndStopsAfterRateLimitWithoutRetry() async throws {
        let recorder = StubRecorder { request in
            if request.url!.path == "/v1/limits" { return (200, [:], self.limitsData()) }
            if request.url!.absoluteString.contains("karpov.courses") {
                return (429, ["X-RateLimit-Requests-Remaining": "0"], Data("server echoed \(self.token)".utf8))
            }
            return (200, [:], self.postsData(count: 1))
        }
        let run = try await client(recorder).collectPilot()
        XCTAssertEqual(run.status, .partial)
        XCTAssertEqual(run.posts.count, 1)
        XCTAssertEqual(run.queries.count, 2)
        XCTAssertEqual(run.queries.last?.rateLimitHeaders["x-ratelimit-requests-remaining"], "0")
        XCTAssertTrue(run.errorMessage?.contains("429") == true)
        XCTAssertFalse(run.errorMessage?.contains(token) == true)
        XCTAssertEqual(recorder.paths.count, 4)
        XCTAssertEqual(recorder.paths.last, "/v1/limits")
    }

    func testDecodesMixedIDsMissingMetadataDeduplicatesAndRedactsRawBody() async throws {
        let body = Data("""
        {"status":"ok","response":{"count":3,"total_count":12,"items":[
          {"id":"99","channel_id":123,"date":null,"link":null,"text":"\(token)","channel":null},
          {"id":99,"channel_id":"123","text":"duplicate"},
          {"id":99,"channel_id":456,"date":"1791700000","link":"t.me/channel/99","channel":{"id":456,"title":"Школа"}}
        ]}}
        """.utf8)
        let recorder = StubRecorder { request in
            request.url!.path == "/v1/limits" ? (200, [:], self.limitsData()) : (200, [:], body)
        }
        let run = try await client(recorder).collectPilot()
        let result = try XCTUnwrap(run.queries.first)
        XCTAssertEqual(result.posts.count, 2)
        XCTAssertEqual(result.posts[0].id, "telegram:123:99")
        XCTAssertNil(result.posts[0].publishedAt)
        XCTAssertNil(result.posts[0].sourceURL)
        XCTAssertEqual(result.posts[0].text, "[REDACTED]")
        XCTAssertEqual(result.posts[1].text, "")
        XCTAssertEqual(result.posts[1].channelTitle, "Школа")
        XCTAssertEqual(result.totalCount, 12)
        let saved = String(data: try TelemetrCollectionStore.encode(run), encoding: .utf8)!
        XCTAssertFalse(saved.contains(token))
    }

    func testHTTPErrorDoesNotExposeBodyAndEmptyTokenNeverSendsRequest() async throws {
        let recorder = StubRecorder { _ in (401, [:], Data(self.token.utf8)) }
        do { _ = try await client(recorder).fetchLimits(); XCTFail("Expected 401") }
        catch {
            XCTAssertTrue(error.localizedDescription.contains("401"))
            XCTAssertFalse(error.localizedDescription.contains(token))
        }
        let before = recorder.requests.count
        do { _ = try await client(recorder, token: "  ").fetchLimits(); XCTFail("Expected missing key") }
        catch { XCTAssertTrue(error.localizedDescription.contains("Введите ключ")) }
        XCTAssertEqual(recorder.requests.count, before)
    }

    func testWebsiteImportPreservesProvenanceDateAndReviewOnReimport() throws {
        try withStore { store in
            var post = websitePost()
            post.sourceDateLabel = "2026-10-11 11:32:19"
            post.sourceURL = "https://t.me/+provider-invite-link"
            let run = websiteRun(posts: [post, post])
            try store.save(run)
            XCTAssertEqual(try store.loadRuns().first?.posts.count, 1)
            try store.updateRelevance(postID: post.id, companyID: post.companyID, relevance: .confirmed)
            _ = try store.importRun(data: TelemetrCollectionStore.encode(run))
            let restored = try XCTUnwrap(store.loadRuns().first?.posts.first)
            XCTAssertEqual(restored.relevance, .confirmed)
            XCTAssertEqual(restored.origin, .telemetrWebsite)
            XCTAssertNil(restored.publishedAt)
            XCTAssertEqual(restored.sourceDateLabel, post.sourceDateLabel)
            XCTAssertEqual(restored.sourceURL, post.sourceURL)
        }
    }

    func testCorruptCollectionBlocksOverwriteAndPreservesBytes() throws {
        try withStore { store in
            let run = websiteRun(posts: [websitePost()])
            try store.save(run)
            let url = store.directoryURL.appendingPathComponent(run.id + ".json")
            let corrupt = Data("{broken saved results".utf8)
            try corrupt.write(to: url)
            XCTAssertThrowsError(try store.loadRuns())
            XCTAssertThrowsError(try store.save(run))
            XCTAssertEqual(try Data(contentsOf: url), corrupt)
        }
    }

    func testReplayingConfirmedExportCannotOverwriteNewerLocalRejection() throws {
        try withStore { store in
            let post = websitePost()
            try store.save(websiteRun(posts: [post]))
            try store.updateRelevance(postID: post.id, companyID: post.companyID, relevance: .confirmed)
            let confirmedExport = try TelemetrCollectionStore.encode(XCTUnwrap(store.loadRuns().first))
            try store.updateRelevance(postID: post.id, companyID: post.companyID, relevance: .rejected)

            _ = try store.importRun(data: confirmedExport)
            XCTAssertEqual(try store.loadRuns().first?.posts.first?.relevance, .rejected)

            // A later explicit review remains authoritative and can reverse that decision.
            try store.updateRelevance(postID: post.id, companyID: post.companyID, relevance: .confirmed)
            XCTAssertEqual(try store.loadRuns().first?.posts.first?.relevance, .confirmed)
        }
    }

    func testArchivedBrowserCardSurvivesImportWithoutInventedOriginal() throws {
        try withStore { store in
            var post = websitePost()
            post.sourceURL = nil
            post.sourcePageURL = "https://telemetr.me/posts?text=school"
            post.sourceDeleted = true
            post.textFromCard = true
            let imported = try store.importRun(data: TelemetrCollectionStore.encode(websiteRun(posts: [post])))
            let restored = try XCTUnwrap(imported.posts.first)
            XCTAssertNil(restored.sourceURL)
            XCTAssertEqual(restored.sourcePageURL, post.sourcePageURL)
            XCTAssertEqual(restored.sourceDeleted, true)
            XCTAssertEqual(restored.textFromCard, true)
            XCTAssertEqual(restored.relevance, .unreviewed)
        }
    }

    func testStoreRejectsPathTraversalFutureVersionAndFalseAPIProvenance() throws {
        try withStore { store in
            var run = websiteRun(posts: [websitePost()])
            run.id = "../escape"
            XCTAssertThrowsError(try store.save(run))
            run.id = "valid"; run.version = 2
            XCTAssertThrowsError(try store.save(run))
            run.version = 1; run.queries[0].posts[0].origin = .telemetrAPI
            XCTAssertThrowsError(try store.save(run))
            run.queries[0].posts[0].origin = .telemetrWebsite
            run.limitsBefore = .init(model: "step", platform: "telegram")
            XCTAssertThrowsError(try store.save(run))
            XCTAssertTrue(try store.loadRuns().isEmpty)
        }
    }

    func testLegacyEvidenceStillLoadsAndImportedProvenanceSurvivesWorkspaceSave() throws {
        let legacy = Data("""
        {"version":1,"reviews":{"brunoyam":{"note":"Старая заметка","decision":"research","checkedEvidenceIDs":[],"addedEvidence":[{"id":"legacy","title":"Исследование","excerpt":"Наблюдение","url":"https://example.com","dateLabel":"06.10.2026","kind":"Ручное","isDemo":false}]}}}
        """.utf8)
        var snapshot = try JSONDecoder().decode(WorkspaceSnapshot.self, from: legacy)
        XCTAssertNil(snapshot.reviews["brunoyam"]?.addedEvidence.first?.provenance)
        snapshot.reviews["brunoyam"]?.addedEvidence.append(.init(
            id: "telegram:123:42", title: "Публикация", excerpt: "Исходный текст", url: "https://t.me/channel/42",
            dateLabel: "2026-10-11 12:00:00", kind: "Telegram", isDemo: false,
            provenance: .init(provider: "Сайт Telemetr", query: "Бруноям", retrievedAt: "2026-10-11T12:30:00Z",
                              channel: "Канал", collectionID: "website-test", contentType: "originalPost")))
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("telemetr-workspace-test-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        let repository = WorkspaceRepository(fileURL: url.appendingPathComponent("workspace.json"))
        try repository.save(snapshot)
        let restored = try repository.load()
        XCTAssertEqual(restored.reviews["brunoyam"]?.note, "Старая заметка")
        XCTAssertNil(restored.reviews["brunoyam"]?.addedEvidence.first?.provenance)
        let provenance = try XCTUnwrap(restored.reviews["brunoyam"]?.addedEvidence.last?.provenance)
        XCTAssertEqual(provenance.provider, "Сайт Telemetr")
        XCTAssertEqual(provenance.query, "Бруноям")
        XCTAssertEqual(provenance.contentType, "originalPost")
        XCTAssertEqual(provenance.collectionID, "website-test")
    }

    private func client(_ recorder: StubRecorder, token: String? = nil) -> TelemetrClient {
        TelemetrURLProtocol.recorder = recorder
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TelemetrURLProtocol.self]
        return TelemetrClient(token: token ?? self.token, session: URLSession(configuration: config))
    }

    private func limitsData(model: String = "step") -> Data {
        Data("""
        {"model":"\(model)","platform":"telegram",
        "requests":{"limit":10000,"used":1,"remaining":9999,"reset_at":1800000000},
        "channels":{"limit":50,"used":0,"remaining":50,"reset_at":1800000000},
        "search":{"limit":1000,"used":0,"remaining":1000,"reset_at":1800000000},
        "posts":{"limit":2000,"used":0,"remaining":2000,"reset_at":1800000000}}
        """.utf8)
    }

    private func postsData(count: Int, query: String = "Бруноям") -> Data {
        let items: [[String: Any]] = (1...count).map { number in
            ["id": number, "channel_id": 123, "text": query + " publication", "date": 1791700000,
             "link": "t.me/channel/\(number)", "channel": ["id": 123, "title": "Канал"]]
        }
        return try! JSONSerialization.data(withJSONObject: ["status": "ok", "response": ["count": count, "total_count": 42, "items": items]])
    }

    func testSegmentTitleAndInterpretationRemainSeparateFromSourceOnImport() throws {
        try withStore { store in
            var post = websitePost()
            post.textFromCard = false
            post.sourcePageURL = "https://telemetr.me/posts?text=test"
            post.signalSummary = "Публикация сообщает о новом продукте."
            post.outreachHypothesis = "Проверить потребность в продажах."
            var run = websiteRun(posts: [post])
            run.title = "ЕГЭ и ОГЭ"
            let saved = try store.importRun(data: TelemetrCollectionStore.encode(run))
            XCTAssertEqual(saved.title, run.title)
            XCTAssertEqual(saved.posts.first?.text, "Публичный пост")
            XCTAssertEqual(saved.posts.first?.textFromCard, false)
            XCTAssertEqual(saved.posts.first?.signalSummary, post.signalSummary)
            XCTAssertEqual(saved.posts.first?.outreachHypothesis, post.outreachHypothesis)
            XCTAssertNil(saved.posts.first?.sourceDeleted)
            XCTAssertEqual(saved.posts.first?.relevance, .unreviewed)
        }
    }

    private func websitePost() -> TelemetrPost {
        .init(companyID: "brunoyam", companyName: "Бруноям", query: "Бруноям", channelID: "123", postID: "42",
              text: "Публичный пост", retrievedAt: Date(timeIntervalSince1970: 1791700000), origin: .telemetrWebsite)
    }
    private func websiteRun(posts: [TelemetrPost]) -> TelemetrCollectionRun {
        .init(id: "website-test", startedAt: Date(timeIntervalSince1970: 1791700000), origin: .telemetrWebsite,
              queries: [.init(query: .pilot[0], posts: posts)])
    }
    private func withStore(_ body: (TelemetrCollectionStore) throws -> Void) throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("telemetr-test-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        try body(TelemetrCollectionStore(directoryURL: url))
    }
}

private final class StubRecorder {
    typealias Handler = (URLRequest) -> (Int, [String: String], Data)
    private let lock = NSLock()
    private var recorded: [URLRequest] = []
    let handler: Handler
    init(_ handler: @escaping Handler) { self.handler = handler }
    var requests: [URLRequest] { lock.lock(); defer { lock.unlock() }; return recorded }
    var paths: [String] { requests.map { $0.url!.path } }
    func respond(_ request: URLRequest) -> (Int, [String: String], Data) {
        lock.lock(); recorded.append(request); lock.unlock()
        return handler(request)
    }
}

private final class TelemetrURLProtocol: URLProtocol {
    static var recorder: StubRecorder?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let recorder = Self.recorder else { fatalError("Test recorder not configured") }
        let (code, headers, data) = recorder.respond(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
