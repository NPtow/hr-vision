import Foundation
import XCTest
@testable import SignalsCore

final class CompanyCatalogTests: XCTestCase {
    func testDiscoveryAddsProductEvidenceWithoutInventingPainAndPreservesSeeds() throws {
        let result = try CompanyCatalog.load(data: data([record()]))
        XCTAssertEqual(result.addedCount, 1)
        XCTAssertTrue(result.duplicateNotes.isEmpty)
        XCTAssertEqual(Array(result.companies.prefix(13)).map(\.id), SeedCatalog.companies.map(\.id))
        let original = SeedCatalog.companies.first { $0.id == "setters" }!
        let preserved = result.companies.first { $0.id == "setters" }!
        XCTAssertEqual(preserved.evidence.map(\.id), original.evidence.map(\.id))
        let company = try XCTUnwrap(result.companies.last)
        XCTAssertEqual(company.parameters.count, 24)
        XCTAssertEqual(company.hypotheses.count, 6)
        XCTAssertTrue(company.hypotheses.allSatisfy { $0.state == .unknown })
        XCTAssertEqual(company.parameters.filter { $0.state == .supported }.map(\.id), ["product_type"])
        XCTAssertTrue(company.parameters.filter { $0.id != "product_type" }.allSatisfy { $0.evidenceIDs.isEmpty })
        XCTAssertEqual(company.discovery?.sizeStatus, "unknown")
        XCTAssertEqual(company.discovery?.lastCheckedAt, "2026-10-11")
        XCTAssertEqual(company.evidence.first?.kind, "Поисковый индекс официального сайта")
        XCTAssertEqual(company.discovery?.hasReadPage, false)
        XCTAssertEqual(Set(company.parameters.first { $0.id == "product_type" }!.evidenceIDs), Set(company.evidence.map(\.id)))
    }

    func testDomainDuplicatesIncludingSeedAliasesAreReportedWithoutRenamingSeed() throws {
        let result = try CompanyCatalog.load(data: data([
            record(id: "discovery-one", website: "https://www.example.ru/courses"),
            record(id: "discovery-two", website: "http://example.ru/"),
            record(id: "discovery-hexlet", website: "https://hexlet.io"),
            record(id: "discovery-brunoyam", website: "https://www.brunoyam.com/")
        ]))
        XCTAssertEqual(result.addedCount, 1)
        XCTAssertEqual(result.duplicateNotes.count, 3)
        XCTAssertTrue(result.companies.contains { $0.id == "hexlet" })
        XCTAssertFalse(result.companies.contains { $0.id == "discovery-hexlet" })
        XCTAssertEqual(result.companies.count, 14)
    }

    func testConflictingIDAndInvalidOrMissingEvidenceFailExplicitly() throws {
        XCTAssertThrowsError(try CompanyCatalog.load(data: data([
            record(id: "discovery-same", website: "https://first.ru"),
            record(id: "discovery-same", website: "https://second.ru")
        ])))
        var noSource = record()
        noSource["sources"] = []
        XCTAssertThrowsError(try CompanyCatalog.load(data: data([noSource])))
        var invalidAccess = record()
        var sources = invalidAccess["sources"] as! [[String: Any]]
        sources[0]["access"] = "assumed"
        invalidAccess["sources"] = sources
        XCTAssertThrowsError(try CompanyCatalog.load(data: data([invalidAccess])))
        var externalSource = record()
        sources[0]["access"] = "page_read"
        sources[0]["url"] = "https://unrelated.ru/list"
        externalSource["sources"] = sources
        XCTAssertThrowsError(try CompanyCatalog.load(data: data([externalSource])))
        XCTAssertThrowsError(try CompanyCatalog.load(data: Data("{broken".utf8)))
    }

    func testEvidenceIDsStayStableWhenSourcesAreReordered() throws {
        var input = record()
        var sources = input["sources"] as! [[String: Any]]
        var second = sources[0]
        second["url"] = "https://example.ru/about"
        second["access"] = "page_read"
        sources.append(second)
        input["sources"] = sources
        let first = try CompanyCatalog.load(data: data([input])).companies.last!
        input["sources"] = Array(sources.reversed())
        let secondResult = try CompanyCatalog.load(data: data([input])).companies.last!
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: first.evidence.map { ($0.url!, $0.id) }),
                       Dictionary(uniqueKeysWithValues: secondResult.evidence.map { ($0.url!, $0.id) }))
        XCTAssertEqual(first.discovery?.hasReadPage, true)
    }

    func testLegacyDossierAndWorkspaceReviewsRemainCompatible() throws {
        let encoded = try JSONEncoder().encode(SeedCatalog.companies[0])
        var object = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        object.removeValue(forKey: "discovery")
        let restored = try JSONDecoder().decode(CompanyDossier.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(restored.discovery)
        XCTAssertEqual(restored.id, "brunoyam")
        let snapshot = WorkspaceSnapshot(reviews: ["setters": CompanyReview(note: "Не менять", checkedEvidenceIDs: ["setters-source-1"])])
        let bytes = try JSONEncoder().encode(snapshot)
        _ = try CompanyCatalog.load(data: data([record()]))
        let restoredSnapshot = try JSONDecoder().decode(WorkspaceSnapshot.self, from: bytes)
        XCTAssertEqual(restoredSnapshot.reviews["setters"]?.note, "Не менять")
        XCTAssertEqual(restoredSnapshot.reviews["setters"]?.checkedEvidenceIDs, ["setters-source-1"])
    }

    func testBundledCatalogLoadsAndKeepsReferencesValid() throws {
        let catalog = try CompanyCatalog.loadBundled()
        XCTAssertEqual(Set(catalog.companies.map(\.id)).count, catalog.companies.count)
        XCTAssertEqual(catalog.companies.filter(\.isDemo).count, 3)
        XCTAssertEqual(catalog.companies.filter { $0.discovery != nil }.count, catalog.addedCount)
        for company in catalog.companies {
            XCTAssertEqual(company.parameters.count, 24, company.id)
            let evidenceIDs = Set(company.evidence.map(\.id))
            for parameter in company.parameters {
                XCTAssertTrue(Set(parameter.evidenceIDs).isSubset(of: evidenceIDs), company.id)
            }
        }
    }

    func testCatalogSearchIncludesWebsiteProductAndAudience() throws {
        let company = try CompanyCatalog.load(data: data([record()])).companies.last!
        XCTAssertTrue(company.matchesCatalogSearch(" EXAMPLE.ru "))
        XCTAssertTrue(company.matchesCatalogSearch("композиции"))
        XCTAssertTrue(company.matchesCatalogSearch("взрослые"))
        XCTAssertFalse(company.matchesCatalogSearch("Несуществующее направление"))
        XCTAssertEqual(company.catalogSegment, "Творческое образование")
    }

    private func data(_ records: [[String: Any]]) throws -> Data {
        try JSONSerialization.data(withJSONObject: records)
    }

    private func record(id: String = "discovery-example-ru", website: String = "https://example.ru") -> [String: Any] {
        ["id": id, "name": "Пример", "website": website, "segment": "Творческое образование",
         "product": "Платные курсы композиции", "audience": "Взрослые", "sizeStatus": "unknown",
         "groupNote": "Принадлежность к группе не проверена", "discoveryQuery": "курсы композиции",
         "discoveryUrl": website,
         "sources": [["url": website, "title": "Курсы школы", "excerpt": "Школа предлагает курсы композиции",
                      "access": "search_index", "checkedAt": "2026-10-11"]]]
    }
}
