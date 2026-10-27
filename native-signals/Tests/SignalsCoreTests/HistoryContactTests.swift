import XCTest
@testable import SignalsCore

final class HistoryContactTests: XCTestCase {
    private let date = "2026-10-14T01:00:00Z"
    private var company: CompanyDossier {
        .init(id: "school", name: "Company", subtitle: "Company", website: "https://academy-fixture.ru", initials: "C", isDemo: false, evidence: [], parameters: [], hypotheses: [])
    }
    private func document(_ id: String, text: String, companyID: String = "school", url: String? = nil) -> SourceHistoryDocument {
        .init(id: id, companyID: companyID, source: "VK", url: url ?? "https://vk.com/wall-123_\(id)", title: "Публикация",
              text: text, kind: "post", retrievedAt: date, quality: "fullText", relationship: "officialWebsite")
    }
    private func archive(_ documents: [SourceHistoryDocument], companyID: String = "school") -> SourceHistoryArchive {
        .init(id: "history-" + companyID, companyID: companyID, companyName: "Company", provider: "VK",
              windowStart: "2026-04-14", windowEnd: "2026-10-14", collectedAt: date, documents: documents)
    }
    private func empty() -> CompanyResearch {
        CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [])
    }

    func testHistoryPostContactsBecomeVisibleCandidatesWithExactQuotesAndSources() throws {
        let doc = document("1", text: "Ищем менеджера. Пишите jobs@academy-fixture.ru, звоните +7 (999) 123-45-67 или https://t.me/company_jobs.")
        var research = empty()
        XCTAssertTrue(research.contacts.isEmpty)
        research.mergeSourceHistory(companyID: company.id, archives: [archive([doc])])
        XCTAssertEqual(research.contacts.count, 3)
        XCTAssertTrue(research.publishedContacts.isEmpty)
        XCTAssertTrue(research.linkedPeople.isEmpty)
        for contact in research.contacts {
            XCTAssertEqual(contact.affiliation, .extracted)
            XCTAssertEqual(contact.purpose, .unknown)
            XCTAssertEqual(contact.materialIDs, ["history:VK:1"])
            XCTAssertEqual(contact.sourceURLs, [doc.url])
            XCTAssertFalse(contact.sourceQuote.isEmpty)
            XCTAssertTrue(doc.text.contains(contact.sourceQuote))
            XCTAssertTrue(contact.sourceQuote.contains(contact.address))
        }
    }

    func testHistoryContactDuplicatesKeepPublishedIDAndPurposeAndMergeProvenance() throws {
        let published = PublicResearchRecord(version: 1, companyID: company.id, companyName: company.name, searchedAt: date,
            documents: [], channels: [], contacts: [
                .init(url: "mailto:jobs@academy-fixture.ru", label: "Кадры", purpose: "hiring", context: "Для откликов на вакансии",
                      sourceURL: "https://academy-fixture.ru/careers", basis: "officialWebsite")
            ], checks: [], notes: [])
        var research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: published)
        let original = try XCTUnwrap(research.contacts.first)
        let docs = [document("1", text: "Отклики: JOBS@academy-fixture.ru; +7 (999) 123-45-67"),
                    document("2", text: "Вопросы: jobs@academy-fixture.ru; 8 (999) 123-45-67")]
        research.mergeSourceHistory(companyID: company.id, archives: [archive(docs)])
        research.mergeSourceHistory(companyID: company.id, archives: [archive(docs)])
        XCTAssertEqual(research.contacts.count, 2, "Same address, formatting and replay cannot inflate the count")
        let email = try XCTUnwrap(research.contacts.first { $0.kind == .email })
        XCTAssertEqual(email.id, original.id)
        XCTAssertEqual(email.name, original.name)
        XCTAssertEqual(email.affiliation, .published)
        XCTAssertEqual(email.purpose, .hiring)
        XCTAssertEqual(email.sourceQuote, original.sourceQuote)
        XCTAssertEqual(Set(email.materialIDs), ["history:VK:1", "history:VK:2"])
        XCTAssertEqual(Set(email.sourceURLs), Set(["https://academy-fixture.ru/careers"] + docs.map(\.url)))
        XCTAssertEqual(research.contacts.first { $0.kind == .phone }?.affiliation, .extracted)
    }

    func testEnrichedExistingMaterialAddsItsNewContactWithoutChangingMaterialID() throws {
        let request = SourceSearchRequest(companyID: company.id, companyName: company.name, query: "careers")
        let url = "https://academy-fixture.ru/careers"
        let search = SourceSearchResult(provider: .serper, request: request, url: url, title: "Карьера", snippet: "Работа в компании")
        let run = SourceCollectionRun(provider: .serper, request: request, results: [search], rawResponse: "{}")
        var research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run], telegramRuns: [])
        let originalID = try XCTUnwrap(research.materials.first?.id)
        let doc = document("career", text: "Мы ищем менеджера. Пишите careers@academy-fixture.ru.", url: url)
        research.mergeSourceHistory(companyID: company.id, archives: [archive([doc])])
        XCTAssertEqual(research.materials.count, 1)
        XCTAssertEqual(research.materials.first?.id, originalID)
        let contact = try XCTUnwrap(research.contacts.first)
        XCTAssertEqual(contact.address, "careers@academy-fixture.ru")
        XCTAssertEqual(contact.materialIDs, [originalID])
        XCTAssertTrue(doc.text.contains(contact.sourceQuote))
        XCTAssertEqual(contact.affiliation, .extracted)
    }

    func testOtherCompaniesAndPartnerPublicationCannotCreateVerifiedContacts() {
        let partner = document("partner", text: "Наш партнёр ищет сотрудников: partner@partner-academy.ru")
        var typedOnly = document("typed-only", text: "Сведения о партнёре")
        typedOnly.contact = "CEO: private-owner@another-academy.ru"
        let other = document("other", text: "other-company@another-academy.ru", companyID: "another")
        var research = empty()
        research.mergeSourceHistory(companyID: company.id, archives: [archive([partner, typedOnly]), archive([other], companyID: "another"), archive([other])])
        XCTAssertTrue(research.contacts.isEmpty, "A third-party corporate mailbox in a partner article is not this company's work contact")
        XCTAssertTrue(research.publishedContacts.isEmpty)
        XCTAssertTrue(research.linkedPeople.isEmpty)
        XCTAssertTrue(research.cooperationContacts.isEmpty)
    }

    func testAddressFoundOnlyInAddedEvidenceKeepsThatEvidenceQuoteAndURL() throws {
        let request = SourceSearchRequest(companyID: company.id, companyName: company.name, query: "careers")
        let url = "https://academy-fixture.ru/careers"
        let search = SourceSearchResult(provider: .serper, request: request, url: url, title: "Карьера", snippet: "Работа в компании")
        let run = SourceCollectionRun(provider: .serper, request: request, results: [search], rawResponse: "{}")
        var research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run], telegramRuns: [])
        var candidate = document("candidate", text: "Контакт для связи: contact@academy-fixture.ru", url: url + "?utm_source=search")
        candidate.relationship = "candidate"; candidate.quality = "snippet"
        research.mergeSourceHistory(companyID: company.id, archives: [archive([candidate])])
        XCTAssertEqual(research.materials.first?.text, "Работа в компании", "The candidate must not replace the trusted material")
        let contact = try XCTUnwrap(research.contacts.first)
        XCTAssertEqual(contact.sourceURLs, [candidate.url])
        XCTAssertEqual(contact.sourceQuote, candidate.text)
        XCTAssertEqual(contact.affiliation, .extracted)
    }

    func testExamplesStudentExercisesAndAddressesWithoutBusinessContextAreNotContacts() {
        let samples = [
            "Контакт для связи: test@example.com",
            "Контакт для связи: hr@dept.example.org",
            "Учебное задание: напишите письмо на practice@academy-fixture.ru",
            "Контакты студентов: learner@academy-fixture.ru",
            "Example email: sample@academy-fixture.ru",
            "Случайная строка other@academy-fixture.ru",
            "Подпишитесь на https://t.me/another_channel",
            "Для удаления пишите на info@archive.org с указанием конкретных URL.",
            "Партнёрства: partners@academy-fixture.ru"
        ]
        var research = empty()
        research.mergeSourceHistory(companyID: company.id, archives: [archive(samples.enumerated().map { document(String($0.offset), text: $0.element) })])
        XCTAssertEqual(research.contacts.map(\.address), ["partners@academy-fixture.ru"])
        XCTAssertTrue(research.publishedContacts.isEmpty)
        XCTAssertEqual(research.contacts.first?.purpose, .unknown)
    }

    func testExistingPersonIdentityAndExplicitContactExclusionSurviveHistoryFinalization() throws {
        var research = empty()
        research.contacts = [.init(id: "known-person", name: "Иван", role: "Менеджер", address: "https://t.me/known_manager",
            kind: .person, affiliation: .profileCompany, explanation: "Проверенная связь профиля", materialIDs: [], sourceURLs: ["https://academy-fixture.ru/team"],
            retrievedAt: Date(), positions: [], query: "previous profile lookup")]
        research.publicResearch = .init(version: 1, companyID: company.id, companyName: company.name, searchedAt: date, documents: [], channels: [
            .init(url: "https://t.me/excluded_contact", sourceURL: "https://academy-fixture.ru", context: "Чужой аккаунт", basis: "excludedReference", purpose: "unknown")
        ], contacts: [], checks: [], notes: [])
        let doc = document("1", text: "Контакты https://t.me/known_manager и https://t.me/excluded_contact")
        research.mergeSourceHistory(companyID: company.id, archives: [archive([doc])])
        XCTAssertEqual(research.contacts.count, 1)
        let person = try XCTUnwrap(research.contacts.first)
        XCTAssertEqual(person.id, "known-person")
        XCTAssertEqual(person.name, "Иван")
        XCTAssertEqual(person.role, "Менеджер")
        XCTAssertEqual(person.kind, .person)
        XCTAssertEqual(person.affiliation, .profileCompany)
        XCTAssertEqual(person.materialIDs, ["history:VK:1"])
    }
}
