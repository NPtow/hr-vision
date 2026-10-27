import Foundation
import XCTest
@testable import SignalsCore

final class TargetHunterImportTests: XCTestCase {
    private let taskID = "6acb09bc42b2d3532942b7a2"
    private let date = Date(timeIntervalSince1970: 1_791_691_456)
    private var request: SourceSearchRequest {
        .init(companyID: "brunoyam", companyName: "Бруноям", query: "Бруноям", limit: 1_000)
    }

    func testActualReceiptShapeRetainsFiveOfFifteenAsPartialCandidates() throws {
        let data = try receipt(groups: [
            group(45408462, name: "Школа Бруноям", screenName: "brunoyam", members: 3111),
            group(45435127, name: "Учебный центр &quot;Бруноям&quot;", screenName: "brunoyam1", members: 0),
            group(73677689, name: "Веб-студия Бруноям", screenName: "bruno_web_development", members: 6),
            group(96483195, name: "бруноям", screenName: "club96483195", members: 0),
            group(183281786, name: "SMM Бруноям, июнь 2019", screenName: "club183281786", members: 9, closed: 1)
        ])
        let run = try imported(data, reported: 15)
        XCTAssertEqual(run.version, 2)
        XCTAssertEqual(run.results.count, 5)
        XCTAssertEqual(run.targetHunterTask?.reportedCount, 15)
        XCTAssertEqual(run.targetHunterTask?.receivedCount, 5)
        XCTAssertEqual(run.targetHunterTask?.completeness, .partial)
        XCTAssertTrue(run.hasMore)
        XCTAssertEqual(run.results.first?.vkCommunity?.membersCount, 3111)
        XCTAssertEqual(run.results.last?.vkCommunity?.isClosed, 1)
        XCTAssertEqual(run.results.first?.vkCommunity?.affiliation, .unverified)
        XCTAssertEqual(run.results.first?.url, "https://vk.com/club45408462")
        XCTAssertEqual(run.results[1].title, "Учебный центр \"Бруноям\"")
        let evidence = run.results[0].evidence(collectionID: run.id)
        XCTAssertEqual(evidence.kind, "Кандидат сообщества VK")
        XCTAssertEqual(evidence.provenance?.contentType, "vkCommunityCandidate")
        XCTAssertTrue(evidence.excerpt.contains("не подтверждена"))
        let restored = try SourceCollectionStore.decode(SourceCollectionStore.encode(run))
        XCTAssertEqual(restored.targetHunterTask, run.targetHunterTask)
        XCTAssertEqual(restored.results.first?.vkCommunity, run.results.first?.vkCommunity)
    }

    func testMoreThanTenCommunitiesAreSupportedAndThousandIsBounded() throws {
        let eleven = try imported(receipt(groups: (1...11).map { group($0) }), reported: 11)
        XCTAssertEqual(eleven.results.count, 11)
        XCTAssertEqual(eleven.targetHunterTask?.completeness, .complete)
        XCTAssertFalse(eleven.hasMore)
        let thousand = try imported(receipt(groups: (1...1_000).map { group($0) }), reported: 1_000)
        XCTAssertEqual(thousand.results.count, 1_000)
        XCTAssertThrowsError(try imported(receipt(groups: (1...1_001).map { group($0) }), reported: 1_001))
        var limited = request; limited.limit = 10
        XCTAssertThrowsError(try TargetHunterResultImporter.makeRun(request: limited, taskID: taskID, reportedCount: 11,
                                                                   responseData: receipt(groups: (1...11).map { group($0) })))
    }

    func testUnknownCountAndContradictoryCountsNeverClaimComplete() throws {
        let unknown = try imported(receipt(groups: [group(1)]), reported: nil)
        XCTAssertEqual(unknown.targetHunterTask?.completeness, .unknown)
        XCTAssertTrue(unknown.hasMore)
        let contradictory = try imported(receipt(groups: [group(1)]), reported: 0)
        XCTAssertEqual(contradictory.targetHunterTask?.completeness, .unknown)
        let empty = try imported(receipt(groups: []), reported: 0)
        XCTAssertEqual(empty.targetHunterTask?.completeness, .complete)
        XCTAssertTrue(empty.results.isEmpty)
        let pendingOrPartial = try imported(receipt(groups: []), reported: 15)
        XCTAssertEqual(pendingOrPartial.targetHunterTask?.completeness, .partial)
        XCTAssertTrue(pendingOrPartial.hasMore)
    }

    func testFailedMissingMalformedOrWrongTaskCannotBecomeEmptySuccess() throws {
        for payload: [String: Any] in [
            ["status": false], ["status": true, "type": "vk_groups_array_object"],
            ["status": true, "type": "vk_users_array_object", "data": []],
            ["status": true, "type": "vk_groups_array_object", "data": "not-an-array"]
        ] { XCTAssertThrowsError(try imported(json(payload), reported: 0)) }
        XCTAssertThrowsError(try imported(json(["isError": true, "content": [["type": "text", "text": "tariff denied"]]]), reported: 0))
        XCTAssertThrowsError(try TargetHunterResultImporter.makeRun(request: request, taskID: "wrong-task", reportedCount: 1,
                                                                   responseData: receipt(groups: [group(1)])))
        XCTAssertThrowsError(try TargetHunterResultImporter.makeRun(request: request, taskID: taskID, moduleID: "vk/1", reportedCount: 1,
                                                                   responseData: receipt(groups: [group(1)])))
        XCTAssertThrowsError(try imported(Data(repeating: 32, count: 4_000_001), reported: 0))
    }

    func testStructuredMCPResultAndReceiptTimestampAreSupported() throws {
        let structured = try json(["structuredContent": ["status": true, "type": "vk_groups_array_object", "data": [group(1)]]])
        let run = try imported(structured, reported: 1)
        XCTAssertEqual(run.results.count, 1)
        let receiptRun = try TargetHunterResultImporter.makeRun(request: request, taskID: taskID, reportedCount: 1,
                                                                responseData: receipt(groups: [group(1)]))
        XCTAssertEqual(ISO8601DateFormatter().string(from: receiptRun.completedAt), "2026-10-11T04:04:16Z")
    }

    func testInvalidCommunityFieldsAndDuplicateIDsAreRejected() throws {
        var badID = group(1); badID["id"] = -1
        var badMembers = group(1); badMembers["members_count"] = -1
        var badClosed = group(1); badClosed["is_closed"] = 3
        var badHandle = group(1); badHandle["screen_name"] = "user@evil.example/x"
        var badName = group(1); badName["name"] = " "
        var boolNumber = group(1); boolNumber["id"] = true
        for group in [badID, badMembers, badClosed, badHandle, badName, boolNumber] {
            XCTAssertThrowsError(try imported(receipt(groups: [group]), reported: 1))
        }
        XCTAssertThrowsError(try imported(receipt(groups: [group(1), group(1)]), reported: 2))
    }

    func testMetadataNotSuppliedRemainsUnknownAndEnvelopeFieldsAreNotRetained() throws {
        let publicGroup: [String: Any] = ["id": 1, "name": "Public community", "access_token": "never-retain-this"]
        let data = try receipt(groups: [publicGroup], extra: ["Authorization": "never-retain-this", "Mcp-Session-Id": "never-retain-session"])
        let run = try imported(data, reported: 1)
        let community = try XCTUnwrap(run.results.first?.vkCommunity)
        XCTAssertNil(community.membersCount)
        XCTAssertNil(community.isClosed)
        XCTAssertNil(community.screenName)
        let serialized = String(decoding: try SourceCollectionStore.encode(run), as: UTF8.self)
        XCTAssertFalse(serialized.contains("never-retain"))
        XCTAssertFalse(serialized.contains("access_token"))
        XCTAssertFalse(serialized.contains("Authorization"))
    }

    func testLegacyVersionOneRunsDecodeWithoutNewOptionalFields() throws {
        let snippet = SourceSearchResult(provider: .serper, request: request, url: "https://example.org", title: "School", snippet: "Text")
        var searchRequest = request; searchRequest.limit = 10
        var search = SourceCollectionRun(provider: .serper, request: searchRequest, results: [snippet], rawResponse: "{}")
        // The default request limit is irrelevant to the result identity.
        search.startedAt = date; search.completedAt = date
        let discovery = SourceCollectionRun(provider: .targetHunter, tools: [], rawResponse: "{}")
        for run in [search, discovery] {
            let data = try SourceCollectionStore.encode(run)
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertNil(object["targetHunterTask"])
            let decoded = try SourceCollectionStore.decode(data)
            XCTAssertEqual(decoded.version, 1)
            XCTAssertNil(decoded.targetHunterTask)
            XCTAssertNil(decoded.results.first?.vkCommunity)
        }
    }

    func testStoreIdempotenceIncrementalMergeAndLegacyPreservation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SourceCollectionStore(directoryURL: directory)
        let legacy = SourceCollectionRun(provider: .targetHunter, tools: [], rawResponse: "{}")
        try store.save(legacy)
        let legacyURL = directory.appendingPathComponent(legacy.id + ".json")
        let legacyData = try Data(contentsOf: legacyURL)
        let first = try imported(receipt(groups: [group(1), group(2)]), reported: 3)
        try store.save(first)
        let runURL = directory.appendingPathComponent(first.id + ".json")
        let before = try Data(contentsOf: runURL)
        let newerSame = try TargetHunterResultImporter.makeRun(request: request, taskID: taskID, reportedCount: 3,
                                                               responseData: receipt(groups: [group(1), group(2)]),
                                                               retrievedAt: date.addingTimeInterval(60))
        try store.save(newerSame)
        XCTAssertEqual(try Data(contentsOf: runURL), before)
        let second = try imported(receipt(groups: [group(2), group(3)]), reported: 3)
        XCTAssertEqual(first.id, second.id)
        try store.save(second)
        let combined = try XCTUnwrap(try store.loadRuns().first { $0.id == first.id })
        XCTAssertEqual(combined.results.compactMap { $0.vkCommunity?.id }, [1, 2, 3])
        XCTAssertEqual(combined.targetHunterTask?.receivedCount, 3)
        XCTAssertEqual(combined.targetHunterTask?.completeness, .complete)
        try store.save(first) // A smaller repeated export cannot erase the third object.
        XCTAssertEqual(try store.loadRuns().first { $0.id == first.id }?.results.count, 3)
        XCTAssertEqual(try store.loadRuns().count, 2)
        XCTAssertEqual(try Data(contentsOf: legacyURL), legacyData)
    }

    func testStoreRejectsTamperingAndKeepsCorruptFilesUntouched() throws {
        let original = try imported(receipt(groups: [group(1)]), reported: 1)
        var altered = original; altered.results[0].companyID = "another"
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        altered = original; altered.results[0].url = "https://evil.example/"
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        altered = original; altered.results[0].snippet = "Подтверждённая принадлежность компании"
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        altered = original; altered.targetHunterTask?.completeness = .partial
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        altered = original; altered.rawResponse = "{\"Authorization\":\"secret\"}"
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        altered = original; altered.version = 3
        XCTAssertThrowsError(try SourceCollectionStore.encode(altered))
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let bad = directory.appendingPathComponent("bad.json")
        let broken = Data("{broken".utf8); try broken.write(to: bad)
        XCTAssertThrowsError(try SourceCollectionStore(directoryURL: directory).save(original))
        XCTAssertEqual(try Data(contentsOf: bad), broken)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), ["bad.json"])
    }

    func testDifferentCompaniesKeepIndependentTaskRunsAndChangedMetadataChangesEvidenceIdentity() throws {
        let first = try imported(receipt(groups: [group(1)]), reported: 1)
        var another = request; another.companyID = "other-school"
        let other = try TargetHunterResultImporter.makeRun(request: another, taskID: taskID, reportedCount: 1,
                                                           responseData: receipt(groups: [group(1)]), retrievedAt: date)
        XCTAssertNotEqual(first.id, other.id)
        XCTAssertNotEqual(first.results.first?.id, other.results.first?.id)
        let changed = try imported(receipt(groups: [group(1, members: 99)]), reported: 1)
        XCTAssertNotEqual(first.results.first?.id, changed.results.first?.id)
    }

    private func imported(_ data: Data, reported: Int?) throws -> SourceCollectionRun {
        try TargetHunterResultImporter.makeRun(request: request, taskID: taskID, reportedCount: reported,
                                              responseData: data, retrievedAt: date)
    }
    private func group(_ id: Int, name: String = "Community", screenName: String? = nil, members: Int = 10, closed: Int = 0) -> [String: Any] {
        ["id": id, "name": name, "screen_name": screenName ?? "club\(id)", "members_count": members, "is_closed": closed, "type": "group"]
    }
    private func receipt(groups: [[String: Any]], extra: [String: Any] = [:]) throws -> Data {
        var receipt: [String: Any] = ["version": 1, "provider": "targetHunter", "operation": "vk_task_result_get",
                                      "arguments": ["task_id": taskID, "project_id": "public-project-id"],
                                      "retrievedAt": "2026-10-11T04:04:16Z",
                                      "result": ["content": [["type": "text", "text": String(decoding: try json(["status": true, "type": "vk_groups_array_object", "data": groups]), as: UTF8.self)]]]]
        extra.forEach { receipt[$0.key] = $0.value }
        return try json(receipt)
    }
    private func json(_ object: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: object) }
}
