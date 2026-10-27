import Foundation
import XCTest
@testable import SignalsCore

final class SignalsCoreTests: XCTestCase {
    func testMissingWorkspaceStartsEmptyWithoutCreatingFile() throws {
        try withTemporaryWorkspace { url in
            let workspace = try WorkspaceRepository(fileURL: url).load()
            XCTAssertEqual(workspace.version, 1)
            XCTAssertTrue(workspace.reviews.isEmpty)
            XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        }
    }

    func testReviewAndManualEvidenceSurviveRelaunchAndReplacement() throws {
        try withTemporaryWorkspace { url in
            let evidence = Evidence(id: "manual-1", title: "Заметка исследователя", excerpt: "Нужно уточнить границы бизнеса.", url: "https://example.com/company", dateLabel: "06.10.2026", kind: "Добавлено вручную", isDemo: false)
            let review = CompanyReview(question: "Что изменилось в продажах?", note: "Позвонить после сверки источника.", decision: .research, addedEvidence: [evidence], checkedEvidenceIDs: ["brunoyam-source-1"])
            let repository = WorkspaceRepository(fileURL: url)
            try repository.save(WorkspaceSnapshot(reviews: ["brunoyam": review]))
            let first = try WorkspaceRepository(fileURL: url).load()
            XCTAssertEqual(first.reviews["brunoyam"]?.decision, .research)
            XCTAssertEqual(first.reviews["brunoyam"]?.question, review.question)
            XCTAssertEqual(first.reviews["brunoyam"]?.addedEvidence.first?.excerpt, evidence.excerpt)
            XCTAssertEqual(first.reviews["brunoyam"]?.checkedEvidenceIDs, ["brunoyam-source-1"])

            var changed = first
            changed.reviews["brunoyam"]?.note = "Проверка завершена."
            changed.reviews["brunoyam"]?.decision = .discuss
            try repository.save(changed)
            let second = try WorkspaceRepository(fileURL: url).load()
            XCTAssertEqual(second.reviews["brunoyam"]?.note, "Проверка завершена.")
            XCTAssertEqual(second.reviews["brunoyam"]?.decision, .discuss)
            XCTAssertEqual(second.reviews["brunoyam"]?.addedEvidence.first?.id, "manual-1")
        }
    }

    func testMalformedWorkspaceThrowsAndPreservesOriginalBytes() throws {
        try withTemporaryWorkspace { url in
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let malformed = Data("{broken saved research".utf8)
            try malformed.write(to: url)
            XCTAssertThrowsError(try WorkspaceRepository(fileURL: url).load())
            XCTAssertEqual(try Data(contentsOf: url), malformed)
        }
    }

    func testHypothesisQuestionsRemainDistinctAfterPersistence() throws {
        try withTemporaryWorkspace { url in
            var review = CompanyReview.empty
            review.setQuestion("Под какую задачу набираете продавцов?", for: "transformation")
            review.setQuestion("На каком шаге теряются обращения?", for: "conversion")
            let repository = WorkspaceRepository(fileURL: url)
            try repository.save(WorkspaceSnapshot(reviews: ["demo-change": review]))
            var restored = try XCTUnwrap(repository.load().reviews["demo-change"])
            XCTAssertEqual(restored.question(for: "transformation"), "Под какую задачу набираете продавцов?")
            XCTAssertEqual(restored.question(for: "conversion"), "На каком шаге теряются обращения?")
            XCTAssertNil(restored.question(for: "capacity"))
            restored.setQuestion(nil, for: "transformation")
            XCTAssertNil(restored.question(for: "transformation"))
            XCTAssertEqual(restored.question(for: "conversion"), "На каком шаге теряются обращения?")
        }
    }

    func testLegacyQuestionDecodesOnlyForTransformationAndClearsOnEditOrReset() throws {
        let oldData = Data("""
        {"question":"Старый сохранённый вопрос","note":"Старая заметка","decision":"research","addedEvidence":[],"checkedEvidenceIDs":[]}
        """.utf8)
        var review = try JSONDecoder().decode(CompanyReview.self, from: oldData)
        XCTAssertNil(review.questionsByHypothesis)
        XCTAssertEqual(review.question(for: "transformation"), "Старый сохранённый вопрос")
        XCTAssertNil(review.question(for: "conversion"))
        review.setQuestion("Отдельный вопрос о конверсии", for: "conversion")
        XCTAssertEqual(review.question(for: "transformation"), "Старый сохранённый вопрос")
        review.setQuestion("Новый вопрос о перестройке", for: "transformation")
        XCTAssertNil(review.question)
        XCTAssertEqual(review.question(for: "transformation"), "Новый вопрос о перестройке")
        review.setQuestion(nil, for: "transformation")
        XCTAssertNil(review.question(for: "transformation"))
        XCTAssertEqual(review.question(for: "conversion"), "Отдельный вопрос о конверсии")

        var resetLegacy = try JSONDecoder().decode(CompanyReview.self, from: oldData)
        resetLegacy.setQuestion(nil, for: "transformation")
        XCTAssertNil(resetLegacy.question)
        XCTAssertNil(resetLegacy.question(for: "transformation"))
    }

    func testUnsupportedVersionCannotSilentlyResetOrOverwriteWorkspace() throws {
        try withTemporaryWorkspace { url in
            let repository = WorkspaceRepository(fileURL: url)
            try repository.save(WorkspaceSnapshot(reviews: ["saved": .empty]))
            let original = try Data(contentsOf: url)
            XCTAssertThrowsError(try repository.save(WorkspaceSnapshot(version: 99)))
            XCTAssertEqual(try Data(contentsOf: url), original)

            let future = Data("{\"version\":99,\"reviews\":{}}".utf8)
            try future.write(to: url)
            XCTAssertThrowsError(try repository.load())
            XCTAssertEqual(try Data(contentsOf: url), future)
        }
    }

    func testEveryDossierUsesSameSmallModelAndValidEvidenceReferences() throws {
        let companies = SeedCatalog.companies
        XCTAssertEqual(companies.count, 13)
        XCTAssertEqual(Set(companies.map(\.id)).count, companies.count)
        let parameterIDs = Set(try XCTUnwrap(companies.first).parameters.map(\.id))
        let hypothesisIDs = Set(try XCTUnwrap(companies.first).hypotheses.map(\.id))
        XCTAssertEqual(parameterIDs.count, 24)
        XCTAssertEqual(hypothesisIDs.count, 6)
        XCTAssertLessThanOrEqual(parameterIDs.count + hypothesisIDs.count, 50)
        for company in companies {
            XCTAssertEqual(Set(company.parameters.map(\.id)), parameterIDs, company.name)
            XCTAssertEqual(Set(company.hypotheses.map(\.id)), hypothesisIDs, company.name)
            XCTAssertEqual(company.parameters.count, 24, company.name)
            XCTAssertEqual(company.hypotheses.count, 6, company.name)
            let evidenceIDs = Set(company.evidence.map(\.id))
            XCTAssertEqual(evidenceIDs.count, company.evidence.count, company.name)
            for parameter in company.parameters {
                XCTAssertTrue(Set(parameter.evidenceIDs).isSubset(of: evidenceIDs), "\(company.name): \(parameter.id)")
                if parameter.state != .unknown { XCTAssertFalse(parameter.evidenceIDs.isEmpty, parameter.id) }
            }
            for hypothesis in company.hypotheses {
                XCTAssertTrue(Set(hypothesis.parameterIDs).isSubset(of: parameterIDs), hypothesis.id)
            }
        }
    }

    func testRealCompaniesHaveSourcesButNoInventedBusinessPainOrNumbers() {
        let real = SeedCatalog.companies.filter { !$0.isDemo }
        XCTAssertEqual(real.count, 10)
        let allowedEstablishedFacts: Set<String> = ["sales_model", "product_type", "sales_path"]
        for company in real {
            XCTAssertNotNil(company.website)
            XCTAssertTrue(company.hypotheses.allSatisfy { $0.state == .unknown }, company.name)
            for parameter in company.parameters {
                if !allowedEstablishedFacts.contains(parameter.id) {
                    XCTAssertEqual(parameter.state, .unknown, "\(company.name): \(parameter.id)")
                    XCTAssertTrue(parameter.evidenceIDs.isEmpty)
                }
                XCTAssertNil(parameter.value.rangeOfCharacter(from: .decimalDigits), company.name)
                XCTAssertFalse(parameter.value.contains("%"), company.name)
            }
            for evidence in company.evidence {
                XCTAssertFalse(evidence.isDemo)
                XCTAssertEqual(URL(string: evidence.url ?? "")?.scheme, "https")
            }
        }
    }

    func testUnknownIsNotZeroOrAnUnsupportedNegativeConclusion() throws {
        let unknown = try XCTUnwrap(SeedCatalog.companies.first { $0.id == "demo-unknown" })
        XCTAssertTrue(unknown.hypotheses.allSatisfy { $0.state == .unknown })
        let revenue = try XCTUnwrap(unknown.parameters.first { $0.id == "revenue_trend" })
        XCTAssertEqual(revenue.state, .unknown)
        XCTAssertEqual(revenue.value, "Нет проверенных данных")
        XCTAssertTrue(revenue.evidenceIDs.isEmpty)
        let restored = try JSONDecoder().decode(CompanyDossier.self, from: JSONEncoder().encode(unknown))
        XCTAssertEqual(restored.parameters.first { $0.id == "revenue_trend" }?.state, .unknown)
        XCTAssertTrue(restored.hypotheses.allSatisfy { $0.state == .unknown })
    }

    func testDemoScenariosClearlySeparateSupportUnknownAndConflict() throws {
        let demos = SeedCatalog.companies.filter(\.isDemo)
        XCTAssertEqual(demos.count, 3)
        for company in demos {
            XCTAssertNil(company.website)
            XCTAssertTrue(company.evidence.allSatisfy { $0.isDemo && $0.url == nil })
        }
        let change = try XCTUnwrap(demos.first { $0.id == "demo-change" })
        XCTAssertEqual(change.hypotheses.first { $0.id == "transformation" }?.state, .supported)
        XCTAssertEqual(change.hypotheses.first { $0.id == "transformation" }?.parameterIDs, ["revenue_trend", "sales_hiring"])
        XCTAssertEqual(change.parameters.first { $0.id == "revenue_trend" }?.state, .supported)
        XCTAssertEqual(change.parameters.first { $0.id == "sales_hiring" }?.state, .supported)
        let conflict = try XCTUnwrap(demos.first { $0.id == "demo-conflict" })
        XCTAssertEqual(conflict.hypotheses.first { $0.id == "transformation" }?.state, .conflict)
        let conflictingRevenue = try XCTUnwrap(conflict.parameters.first { $0.id == "revenue_trend" })
        XCTAssertEqual(conflictingRevenue.state, .conflict)
        XCTAssertEqual(conflictingRevenue.evidenceIDs.count, 2)
    }

    private func withTemporaryWorkspace(_ body: (URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("SignalsCoreTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        try body(directory.appendingPathComponent("nested/workspace.json"))
    }
}
