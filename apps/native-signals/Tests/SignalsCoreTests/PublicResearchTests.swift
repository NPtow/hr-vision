import XCTest
@testable import SignalsCore

final class PublicResearchTests: XCTestCase {
    let date = "2026-10-12T01:00:00Z"
    var company: CompanyDossier { .init(id: "school", name: "School", subtitle: "School", website: "https://school.example", initials: "S", isDemo: false, evidence: [], parameters: [], hypotheses: []) }
    func record(companyID: String = "school") -> PublicResearchRecord {
        .init(version: 1, companyID: companyID, companyName: "School", searchedAt: date,
              documents: [.init(id: "doc", companyID: companyID, url: "https://school.example/contacts", title: "Контакты", text: "Сотрудничество: @school_partner", kind: "website", provider: "Официальный сайт", retrievedAt: date, publishedAt: nil, quality: "fullText", official: true)],
              channels: [.init(url: "https://t.me/school", sourceURL: "https://school.example/contacts", context: "Наш Telegram", basis: "officialWebsite", purpose: "unknown", title: "School", bio: "Обучение", status: "read", accountType: "channel", retrievedAt: date),
                         .init(url: "https://t.me/unrelated", sourceURL: "https://search.example", context: "Search match", basis: "searchCandidate", purpose: "cooperation")],
              contacts: [.init(url: "https://t.me/school_partner", label: "Партнёрства", purpose: "cooperation", context: "Сотрудничество: @school_partner", sourceURL: "https://school.example/contacts", basis: "officialWebsite")], checks: [], notes: [])
    }
    func testContactPurposeNeedsPublishedContextAndSearchCandidateIsNotPromoted() {
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: record())
        XCTAssertEqual(value.publishedContacts.count, 2)
        XCTAssertEqual(value.cooperationContacts.count, 1)
        XCTAssertEqual(value.cooperationContacts[0].sourceQuote, "Сотрудничество: @school_partner")
        XCTAssertEqual(value.cooperationContacts[0].materialIDs, ["url:https://school.example/contacts"])
        XCTAssertFalse(value.contacts.contains { $0.address.contains("unrelated") })
        XCTAssertEqual(value.linkedPeople.count, 0)
        XCTAssertNil(value.fullMaterials[0].publishedAt)
    }
    func testWrongCompanyArchiveCannotLeakIntoDossier() {
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: record(companyID: "different"))
        XCTAssertTrue(value.materials.isEmpty)
        XCTAssertTrue(value.contacts.isEmpty)
        XCTAssertNil(value.publicResearch)
    }
    func testReadTextWinsEvenIfFragmentIsLongerAndOldEvidenceIsRetained() {
        let request = SourceSearchRequest(companyID: "school", companyName: "School", query: "School")
        let result = SourceSearchResult(provider: .serper, request: request, url: "https://school.example/contacts?utm_source=google", title: "Fragment", snippet: String(repeating: "navigation ", count: 40))
        let run = SourceCollectionRun(provider: .serper, request: request, results: [result], rawResponse: "{}")
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run], telegramRuns: [], publicResearch: record())
        XCTAssertEqual(value.materials.count, 1)
        XCTAssertTrue(value.materials[0].hasFullText)
        XCTAssertEqual(value.materials[0].text, "Сотрудничество: @school_partner")
        XCTAssertEqual(value.materials[0].evidence.count, 2)
        XCTAssertEqual(value.fragments.count, 0)
    }
    func testUnreadDeletedPostIsStillAFragment() {
        let post = TelemetrPost(companyID: "school", companyName: "School", query: "School", channelID: "1", postID: "2", text: "Deleted fragment", origin: .telemetrWebsite, sourceDeleted: true, textFromCard: false)
        let run = TelemetrCollectionRun(origin: .telemetrWebsite, queries: [.init(query: .init(companyID: "school", companyName: "School", query: "School", limit: 10), posts: [post])])
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [run])
        XCTAssertEqual(value.fullMaterials.count, 0)
        XCTAssertEqual(value.fragments.count, 1)
    }
    func testStoreIsolatesCorruptRecordAndLeavesFilesUntouched() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let data = try JSONEncoder().encode(record())
        try data.write(to: dir.appendingPathComponent("school.json"))
        let corrupt = Data("bad json".utf8)
        try corrupt.write(to: dir.appendingPathComponent("broken.json"))
        let loaded = PublicResearchStore(directory: dir).load()
        XCTAssertEqual(loaded.records.count, 1)
        XCTAssertEqual(loaded.errors.count, 1)
        XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent("broken.json")), corrupt)
        XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent("school.json")), data)
    }

    func testPublishedContactTransportDoesNotInventTelegramAccountsOrPeople() throws {
        var archive = record()
        archive.channels = []
        let addresses: [(String, ResearchContactKind)] = [
            ("mailto:HR@school.example", .email), ("tel:+7 (999) 123-45-67", .phone),
            ("https://t.me/school_hr", .telegram), ("https://vk.com/school_hr", .publicLink),
            ("https://www.linkedin.com/in/school-contact", .publicLink),
            ("https://school.example/partners", .publicLink), ("https://t.me.attacker.example/school", .publicLink)
        ]
        archive.contacts = addresses.map { address, _ in
            .init(url: address, label: "Контакт", purpose: "general", context: "Опубликованный контакт", sourceURL: "https://school.example/contacts", basis: "officialWebsite")
        }
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: archive)
        XCTAssertEqual(value.publishedContacts.count, addresses.count)
        XCTAssertTrue(value.linkedPeople.isEmpty)
        XCTAssertTrue(value.otherPeople.isEmpty)
        for (address, kind) in addresses {
            let contact = try XCTUnwrap(value.contacts.first { $0.address == address })
            XCTAssertEqual(contact.kind, kind)
            XCTAssertFalse(contact.explanation.contains("школы"))
        }
        // Exact historical identifiers must survive the new comparison keys.
        XCTAssertEqual(value.contacts.first { $0.kind == .email }?.id, "published:mailto:hr@school.example")
        XCTAssertEqual(value.contacts.first { $0.kind == .telegram }?.id, "published:https://t.me/school_hr")
        XCTAssertEqual(value.contacts.first { $0.kind == .email }?.actionURL?.absoluteString, "mailto:hr@school.example")
        XCTAssertEqual(value.contacts.first { $0.kind == .phone }?.actionURL?.absoluteString, "tel:+79991234567")
        XCTAssertEqual(value.contacts.first { $0.kind == .publicLink }?.kind.openActionTitle, "Открыть ссылку")
    }

    func testPublishedPhoneAndEmailVariantsReplaceExtractedDuplicatesWithoutLosingSourcesOrID() throws {
        var archive = record()
        archive.channels = []
        archive.documents[0].text = "Контакты: +7 (999) 123-45-67, HR@school.example"
        archive.contacts = [
            .init(url: "tel:+79991234567", label: "Приёмная", purpose: "general", context: "Приёмная", sourceURL: "https://school.example/contacts", basis: "officialWebsite"),
            .init(url: "8 (999) 123-45-67", label: "Партнёрам", purpose: "cooperation", context: "По вопросам сотрудничества", sourceURL: "https://school.example/partners", basis: "officialWebsite"),
            .init(url: "mailto:hr@school.example", label: "HR", purpose: "hiring", context: "Отдел кадров", sourceURL: "https://school.example/contacts", basis: "officialWebsite"),
            .init(url: "HR@school.example", label: "Почта", purpose: "general", context: "Напишите нам", sourceURL: "https://school.example/about", basis: "officialWebsite")
        ]
        let request = SourceSearchRequest(companyID: "school", companyName: "School", query: "School")
        let result = SourceSearchResult(provider: .serper, request: request, url: "https://directory.example/school", title: "Контакты", snippet: "8 (999) 123-45-67, hr@school.example")
        let run = SourceCollectionRun(provider: .serper, request: request, results: [result], rawResponse: "{}")
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run], telegramRuns: [], publicResearch: archive)
        XCTAssertEqual(value.contacts.count, 2, "Different formatting and extraction do not inflate contact counts")
        let phone = try XCTUnwrap(value.contacts.first { $0.kind == .phone })
        XCTAssertEqual(phone.id, "published:tel:+79991234567", "A purpose upgrade retains the original published ID")
        XCTAssertEqual(phone.purpose, .cooperation)
        XCTAssertEqual(phone.sourceQuote, "По вопросам сотрудничества")
        XCTAssertEqual(Set(phone.materialIDs), ["url:https://school.example/contacts", "url:https://directory.example/school"])
        XCTAssertTrue(phone.sourceURLs.contains("https://directory.example/school"))
        XCTAssertTrue(phone.sourceURLs.first?.hasPrefix("https://school.example/") == true)
        XCTAssertEqual(value.contacts.first { $0.kind == .email }?.id, "published:mailto:hr@school.example")
    }

    func testContactAddressNormalizationKeepsDifferentExtensionsAndSafeActionsSeparate() {
        XCTAssertEqual(CompanyResearch.contactAddressKey("tel:%2B7(999)123-45-67"), CompanyResearch.contactAddressKey("8 999 123 45 67"))
        XCTAssertEqual(CompanyResearch.contactAddressKey("MAILTO:HR@school.example?subject=Hello"), CompanyResearch.contactAddressKey("hr@school.example"))
        XCTAssertNotEqual(CompanyResearch.contactAddressKey("tel:+79991234567;ext=12"), CompanyResearch.contactAddressKey("tel:+79991234567;ext=34"))
        XCTAssertNotEqual(CompanyResearch.contactAddressKey("tel:+79991234567;ext=12"), CompanyResearch.contactAddressKey("+79991234567"))
        XCTAssertNotEqual(CompanyResearch.contactAddressKey("tel:+79991234567"), CompanyResearch.contactAddressKey("tel:+79991234568"))
        XCTAssertEqual(CompanyResearch.contactActionURL("tel:+85212345678", kind: .phone)?.absoluteString, "tel:+85212345678", "An international +8 prefix is not the Russian trunk prefix")
        XCTAssertNil(CompanyResearch.contactActionURL("javascript:alert(1)", kind: .publicLink))
        XCTAssertNil(CompanyResearch.contactActionURL("https://school.example", kind: .email))
        XCTAssertNil(CompanyResearch.contactActionURL("tel:unknown", kind: .phone))
        XCTAssertEqual(CompanyResearch.contactKind("https://www.linkedin.com/in/someone"), .publicLink)
    }

    func testTelegramBioQuoteUsesItsProfileSourceAndLegacyRecordsUseWebsiteContext() throws {
        var archive = record()
        archive.contacts = []
        archive.channels = [archive.channels[0]]
        archive.documents = []
        archive.channels[0].bio = "Партнёрства: @manager"
        archive.channels[0].context = "Наш Telegram"
        archive.channels[0].profileSourceURL = "https://t.me/school"
        // The optional source survives native archive decoding; it used to be ignored.
        archive = try JSONDecoder().decode(PublicResearchRecord.self, from: JSONEncoder().encode(archive))
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: archive)
        let fromProfile = try XCTUnwrap(value.contacts.first)
        XCTAssertEqual(fromProfile.id, "published:https://t.me/school")
        XCTAssertEqual(fromProfile.sourceQuote, "Партнёрства: @manager")
        XCTAssertEqual(fromProfile.sourceURLs, ["https://t.me/school"])
        archive.channels[0].profileSourceURL = nil
        let legacy = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [], publicResearch: archive)
        XCTAssertEqual(legacy.contacts.first?.id, fromProfile.id)
        XCTAssertEqual(legacy.contacts.first?.sourceQuote, "Наш Telegram")
        XCTAssertEqual(legacy.contacts.first?.sourceURLs, ["https://school.example/contacts"])
    }
}
