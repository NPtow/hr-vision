import Foundation
import XCTest
@testable import SignalsCore

final class HarvestTests: XCTestCase {
    private let secret = "harvest-fixture-secret-123456789"
    private var query: SourceSearchRequest { .init(companyID: "school", companyName: "School", query: "School", limit: 100) }
    private func client(_ body: [String: Any], status: Int = 200) -> HarvestClient {
        HarvestProtocol.requests = []
        HarvestProtocol.body = try! JSONSerialization.data(withJSONObject: body)
        HarvestProtocol.status = status
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [HarvestProtocol.self]
        return HarvestClient(apiKey: secret, configuration: config)
    }

    func testCompanyPageRetainsMoreThanTenAndMarksContinuation() async throws {
        let entries: [[String: Any]] = (1...50).map { ["name": "School \($0)", "linkedinUrl": "https://ru.linkedin.com/company/school-\($0)/?trk=x", "summary": secret, "email": "drop@example.org"] }
        let run = try await client(["elements": entries, "pagination": ["totalPages": 3, "totalElements": 123], "api_key": secret]).collect(query, operation: .companies)
        XCTAssertEqual(run.results.count, 50); XCTAssertEqual(run.harvest?.receivedCount, 50)
        XCTAssertTrue(run.hasMore); XCTAssertEqual(run.version, 3)
        XCTAssertEqual(run.results.first?.url, "https://www.linkedin.com/company/school-1")
        let saved = try SourceCollectionStore.encode(run)
        XCTAssertFalse(String(decoding: saved, as: UTF8.self).contains(secret))
        XCTAssertFalse(String(decoding: saved, as: UTF8.self).contains("drop@example.org"))
        XCTAssertEqual(try SourceCollectionStore.decode(saved).results.count, 50)
        XCTAssertEqual(HarvestProtocol.requests.count, 1)
        XCTAssertEqual(HarvestProtocol.requests[0].httpMethod, "GET")
        XCTAssertEqual(HarvestProtocol.requests[0].url?.host, "api.harvestapi.io")
        XCTAssertEqual(HarvestProtocol.requests[0].value(forHTTPHeaderField: "X-API-Key"), secret)
    }

    func testPeopleFilterUsesCurrentCompaniesAndRetainsUnverifiedRoles() async throws {
        let run = try await client(["elements": [["firstName": "Test", "lastName": "Person",
            "linkedinUrl": "https://www.linkedin.com/in/test",
            "currentPositions": [["title": "Head of Sales", "companyName": "School", "companyLinkedinUrl": "https://www.linkedin.com/company/school"]]]]])
            .collect(query, operation: .people, targetURL: "https://www.linkedin.com/company/school", jobTitles: "Sales,Founder")
        let url = try XCTUnwrap(HarvestProtocol.requests.first?.url)
        let parameters = Dictionary(uniqueKeysWithValues: URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(parameters["currentCompanies"], "https://www.linkedin.com/company/school")
        XCTAssertEqual(parameters["currentJobTitles"], "Sales,Founder")
        XCTAssertEqual(parameters["page"], "1"); XCTAssertNil(parameters["findEmail"])
        XCTAssertEqual(run.results.first?.linkedIn?.affiliation, "unverified")
        XCTAssertEqual(run.results.first?.linkedIn?.positions.first?.title, "Head of Sales")
        XCTAssertEqual(run.results.first?.evidence(collectionID: run.id).provenance?.contentType, "linkedInPerson")
        _ = try SourceCollectionStore.encode(run)
    }

    func testPostRetainsAuthorDateAndRepostWithoutAssigningTargetAsAuthor() async throws {
        let run = try await client(["elements": [["linkedinUrl": "https://www.linkedin.com/posts/original-123",
            "content": "Public text", "author": ["name": "Original Author", "linkedinUrl": "https://www.linkedin.com/in/original"],
            "repostId": "123", "postedAt": ["date": "2026-10-01T12:00:00Z"]]]])
            .collect(query, operation: .posts, targetURL: "https://www.linkedin.com/in/resharer")
        let result = try XCTUnwrap(run.results.first)
        XCTAssertEqual(result.linkedIn?.authorName, "Original Author")
        XCTAssertEqual(result.linkedIn?.authorURL, "https://www.linkedin.com/in/original")
        XCTAssertEqual(result.linkedIn?.publishedAt, "2026-10-01T12:00:00Z")
        XCTAssertTrue(result.linkedIn?.isRepost ?? false)
        XCTAssertEqual(result.evidence(collectionID: run.id).dateLabel, "2026-10-01T12:00:00Z")
        var tampered = run; tampered.results[0].linkedIn?.affiliation = "confirmed"
        XCTAssertThrowsError(try SourceCollectionStore.encode(tampered))
        _ = try SourceCollectionStore.encode(run)
    }

    func testErrorsNeverRetryOrExposeSecret() async throws {
        for status in [302, 401, 402, 403, 429, 500] {
            do {
                _ = try await client(["error": secret], status: status).collect(query, operation: .companies)
                XCTFail("Must fail")
            } catch { XCTAssertFalse(error.localizedDescription.contains(secret)) }
            XCTAssertEqual(HarvestProtocol.requests.count, 1)
        }
        do {
            _ = try await client(["error": secret, "elements": []]).collect(query, operation: .companies)
            XCTFail("Application-level failure must not become an empty success")
        } catch { XCTAssertEqual(error as? SourceError, .invalidResponse) }
    }

    func testRejectsWrongTargetsBeforeNetwork() async throws {
        let source = client(["elements": []])
        for url in ["https://evil.example/company/school", "https://linkedin.com.evil.example/company/school", "https://user:pass@linkedin.com/company/school", "https://linkedin.com/in/person"] {
            do { _ = try await source.collect(query, operation: .people, targetURL: url); XCTFail("Bad target") }
            catch { }
        }
        XCTAssertEqual(HarvestProtocol.requests.count, 0)
    }

    func testEmptyResultAndRejectedRecordsAreNotConfused() async throws {
        let empty = try await client(["elements": [], "pagination": ["totalElements": 0, "totalPages": 0]]).collect(query, operation: .companies)
        XCTAssertEqual(empty.results.count, 0); XCTAssertFalse(empty.hasMore)
        do {
            _ = try await client(["elements": [["name": "Bad", "linkedinUrl": "https://evil.example"]]]).collect(query, operation: .companies)
            XCTFail("Unparseable records must not become no results")
        } catch { XCTAssertEqual(error as? SourceError, .invalidResponse) }
    }

    func testStorePreservesLegacyAndRequiresTypedHarvestVersion() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SourceCollectionStore(directoryURL: directory)
        let oldQuery = SourceSearchRequest(companyID: "school", companyName: "School", query: "School")
        let oldRun = SourceCollectionRun(provider: .serper, request: oldQuery, results: [], rawResponse: "{\"organic\":[]}")
        try store.save(oldRun)
        let before = try Data(contentsOf: directory.appendingPathComponent(oldRun.id + ".json"))
        var newRun = try await client(["elements": []]).collect(query, operation: .companies)
        try store.save(newRun)
        XCTAssertEqual(try store.loadRuns().count, 2)
        XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent(oldRun.id + ".json")), before)
        newRun.version = 1
        XCTAssertThrowsError(try store.save(newRun))
    }
}

private final class HarvestProtocol: URLProtocol {
    static var requests: [URLRequest] = []
    static var body = Data()
    static var status = 200
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: nil, headerFields: [:])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
