import XCTest
@testable import SignalsCore

final class CompanyResearchTests: XCTestCase {
    private let date = Date(timeIntervalSince1970: 1_791_676_800)
    private var company: CompanyDossier {
        .init(id: "school", name: "PARTA", subtitle: "School", website: "https://school.example", initials: "P", isDemo: false, evidence: [], parameters: [], hypotheses: [])
    }
    private var request: SourceSearchRequest { .init(companyID: "school", companyName: "PARTA", query: "PARTA", limit: 100) }
    private func run(_ provider: SourceProvider, _ results: [SourceSearchResult], raw: String = "{}", operation: HarvestOperation? = nil, target: String? = nil) -> SourceCollectionRun {
        var value = SourceCollectionRun(provider: provider, request: request, results: results, rawResponse: raw, startedAt: date, completedAt: date)
        if let operation { value.harvest = .init(operation: operation, targetURL: target, jobTitles: nil, receivedCount: results.count, reportedCount: results.count, totalPages: 1) }
        return value
    }
    private func person(_ id: String, employer: String?, title: String = "Manager") -> SourceSearchResult {
        .init(request: request, url: "https://www.linkedin.com/in/\(id)", title: id, text: title,
              metadata: .init(operation: .people, positions: [.init(companyName: "PARTA", title: title, companyURL: employer)]), retrievedAt: date)
    }

    func testSearchDeduplicatesURLsButRetainsProviderQuotesAndQueryParameters() {
        let a = SourceSearchResult(provider: .serper, request: request, url: "https://school.example/news/?utm_source=google", title: "News", snippet: "First extract", retrievedAt: date)
        let b = SourceSearchResult(provider: .yandex, request: request, url: "https://www.school.example/news", title: "News", snippet: "A longer different extract", retrievedAt: date)
        let c = SourceSearchResult(provider: .yandex, request: request, url: "https://school.example/news?page=2", title: "Other page", snippet: "Different page", retrievedAt: date)
        let research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run(.serper, [a]), run(.yandex, [b, c])], telegramRuns: [])
        XCTAssertEqual(research.materials.count, 2)
        let merged = research.materials.first { $0.evidence.count == 2 }!
        XCTAssertEqual(Set(merged.providers), ["Google", "Яндекс"])
        XCTAssertEqual(Set(merged.evidence.map(\.excerpt)), [a.snippet, b.snippet])
        XCTAssertNil(merged.publishedAt, "Collection date must not be promoted to publication date")
        XCTAssertTrue(merged.isOfficialDomain)
    }

    func testPeopleMustMatchCompanyIdentityNotJustTheSameName() {
        let target = "https://www.linkedin.com/company/partaru"
        let companies = run(.harvest, [], raw: #"{"elements":[{"id":"123","linkedinUrl":"https://www.linkedin.com/company/partaru/"}]}"#, operation: .companies)
        let people = run(.harvest, [person("same", employer: "https://www.linkedin.com/company/123"), person("foreign", employer: "https://www.linkedin.com/company/456"), person("name-only", employer: nil)], operation: .people, target: target)
        let research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [companies, people], telegramRuns: [])
        XCTAssertEqual(research.linkedPeople.map(\.name), ["same"])
        XCTAssertEqual(research.otherPeople.count, 2)
        XCTAssertEqual(research.otherPeople.first { $0.name == "foreign" }?.affiliation, .otherCompany)
        XCTAssertEqual(research.otherPeople.first { $0.name == "name-only" }?.affiliation, .unresolved)
        XCTAssertTrue(research.linkedPeople[0].explanation.contains("отдельно не проверено"))
        XCTAssertEqual(research.materials.count, 0, "People belong in Contacts, not duplicate material cards")
    }

    func testTelegramSearchURLDoesNotCollapseDifferentPostsAndFullTextWins() {
        let query = TelemetrQuery(companyID: "school", companyName: "PARTA", query: "PARTA", limit: 20)
        let one = TelemetrPost(companyID: "school", companyName: "PARTA", query: "PARTA", channelID: "a", postID: "1", text: "Short", origin: .telemetrWebsite, sourcePageURL: "https://telemetr.me/posts?text=PARTA", textFromCard: false)
        var expanded = one; expanded.text = "Full text from opened card"; expanded.textFromCard = true; expanded.relevance = .confirmed
        var two = one; two.postID = "2"; two.text = "Different post"
        var rejected = one; rejected.postID = "3"; rejected.relevance = .rejected
        let first = TelemetrCollectionRun(origin: .telemetrWebsite, queries: [.init(query: query, posts: [one, two, rejected])])
        let second = TelemetrCollectionRun(origin: .telemetrWebsite, queries: [.init(query: query, posts: [expanded])])
        let research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: [first, second])
        XCTAssertEqual(research.materials.count, 2)
        let full = research.materials.first { $0.id.hasSuffix(":1") }!
        XCTAssertEqual(full.text, expanded.text)
        XCTAssertTrue(full.quality.contains("раскрытой"))
        XCTAssertTrue(full.relationship.contains("вручную"))
        XCTAssertFalse(full.originalAvailable)
    }

    func testAddressesHaveProvenanceAndNeverTurnIntoNamedEmployees() {
        let result = SourceSearchResult(provider: .serper, request: request, url: "https://school.example/jobs", title: "Jobs", snippet: "Вакансия: jobs@school.example, +7 (999) 123-45-67. Канал https://t.me/school_jobs/42", retrievedAt: date)
        let same = SourceSearchResult(provider: .yandex, request: request, url: result.url, title: result.title, snippet: result.snippet, retrievedAt: date)
        let research = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run(.serper, [result]), run(.yandex, [same])], telegramRuns: [])
        XCTAssertEqual(research.addresses.count, 3)
        XCTAssertEqual(research.linkedPeople.count, 0)
        for address in research.addresses {
            XCTAssertEqual(address.affiliation, .extracted)
            XCTAssertEqual(address.materialIDs.count, 1)
            XCTAssertEqual(address.sourceURLs, [result.url])
            XCTAssertTrue(address.explanation.contains("не установленный личный контакт"))
        }
        XCTAssertEqual(research.materials.first?.kind, .hiring)
    }

    func testSavedEvidenceDoesNotDuplicateTheAlreadyCollectedPost() {
        let post = TelemetrPost(companyID: "school", companyName: "PARTA", query: "PARTA", channelID: "a", postID: "1", text: "Post", origin: .telemetrWebsite)
        let collection = TelemetrCollectionRun(origin: .telemetrWebsite, queries: [.init(query: .init(companyID: "school", companyName: "PARTA", query: "PARTA", limit: 1), posts: [post])])
        let saved = Evidence(id: "telemetr-school-\(post.id)", title: "Saved post", excerpt: post.text, url: nil, dateLabel: "unknown", kind: "Telegram", isDemo: false)
        let value = CompanyResearch.build(company: company, evidence: [saved], sourceRuns: [], telegramRuns: [collection])
        XCTAssertEqual(value.materials.count, 1)
        XCTAssertTrue(value.materials[0].evidence.contains { $0.id == saved.id })
    }

    func testAddressesIncludeOtherSnippetsAndURLOnlyTelegramWithPhoneDeduplication() {
        let phone = SourceSearchResult(provider: .serper, request: request, url: "https://school.example/team", title: "Team", snippet: "8 (800) 222-86-63", retrievedAt: date)
        let longer = SourceSearchResult(provider: .yandex, request: request, url: phone.url, title: phone.title, snippet: "A different long extract with none of the contact information", retrievedAt: date)
        let samePhone = SourceSearchResult(provider: .yandex, request: request, url: "https://school.example/about", title: "About", snippet: "+7 800 222 86 63", retrievedAt: date)
        let telegram = SourceSearchResult(provider: .yandex, request: request, url: "https://t.me/school_news", title: "Telegram", snippet: "", retrievedAt: date)
        let value = CompanyResearch.build(company: company, evidence: [], sourceRuns: [run(.serper, [phone]), run(.yandex, [longer, samePhone, telegram])], telegramRuns: [])
        XCTAssertEqual(value.addresses.count, 2)
        XCTAssertEqual(value.addresses.first { $0.kind == .phone }?.materialIDs.count, 2)
        XCTAssertTrue(value.addresses.contains { $0.address == telegram.url })
        XCTAssertTrue(value.materials.first { $0.url == phone.url }?.searchText.contains(phone.snippet) == true)
    }

    func testRemovingFavoritePreservesManualMaterialAndSurvivesSaving() throws {
        let evidence = Evidence(id: "manual", title: "Manual", excerpt: "Source text", url: nil, dateLabel: "unknown", kind: "Manual", isDemo: false)
        var review = CompanyReview(addedEvidence: [evidence])
        XCTAssertEqual(review.favoriteIDs, [evidence.id])
        review.toggleFavorite(evidenceIDs: [evidence.id])
        let restored = try JSONDecoder().decode(CompanyReview.self, from: JSONEncoder().encode(review))
        XCTAssertTrue(restored.favoriteIDs.isEmpty)
        XCTAssertEqual(restored.addedEvidence.first?.excerpt, evidence.excerpt)
        review.toggleFavorite(evidenceIDs: [evidence.id])
        XCTAssertEqual(review.favoriteIDs, [evidence.id])
    }

    func testRejectedTelegramPostDoesNotReappearFromPreviouslySavedEvidence() {
        var post = TelemetrPost(companyID: "school", companyName: "PARTA", query: "PARTA", channelID: "a", postID: "1", text: "Unrelated", origin: .telemetrWebsite)
        post.relevance = .rejected
        let run = TelemetrCollectionRun(origin: .telemetrWebsite, queries: [.init(query: .init(companyID: "school", companyName: "PARTA", query: "PARTA", limit: 1), posts: [post])])
        let saved = Evidence(id: "telemetr-school-\(post.id)", title: "Saved", excerpt: post.text, url: nil, dateLabel: "unknown", kind: "Telegram", isDemo: false)
        XCTAssertTrue(CompanyResearch.build(company: company, evidence: [saved], sourceRuns: [], telegramRuns: [run]).materials.isEmpty)
    }

    func testNoPeopleSearchIsDifferentFromAnEmptySearch() {
        XCTAssertFalse(CompanyResearch.build(company: company, evidence: [], sourceRuns: [], telegramRuns: []).peopleSearchPerformed)
        let empty = run(.harvest, [], operation: .people, target: "https://www.linkedin.com/company/123")
        XCTAssertTrue(CompanyResearch.build(company: company, evidence: [], sourceRuns: [empty], telegramRuns: []).peopleSearchPerformed)
    }

    func testInstalledDataProjectionWhenRequested() throws {
        guard let output = ProcessInfo.processInfo.environment["TG_SIGNALS_RESEARCH_AUDIT"] else { throw XCTSkip("Set audit output path to inspect installed data read-only") }
        let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TG Signals")
        let sources = try SourceCollectionStore(directoryURL: base.appendingPathComponent("source-runs")).loadRuns()
        let telegram = try TelemetrCollectionStore(directoryURL: base.appendingPathComponent("collection-runs")).loadRuns()
        let snapshot = try WorkspaceRepository(fileURL: base.appendingPathComponent("workspace.json")).load()
        let catalog = try CompanyCatalog.loadBundled().companies.filter { !$0.isDemo }
        var summaries: [[String: Any]] = []
        for company in catalog {
            let value = CompanyResearch.build(company: company, evidence: company.evidence + (snapshot.reviews[company.id]?.addedEvidence ?? []), sourceRuns: sources, telegramRuns: telegram)
            XCTAssertEqual(Set(value.materials.map(\.id)).count, value.materials.count)
            XCTAssertEqual(Set(value.contacts.map(\.id)).count, value.contacts.count)
            XCTAssertTrue(value.materials.allSatisfy { !$0.evidence.isEmpty && $0.evidence.allSatisfy { !$0.isDemo } })
            XCTAssertTrue(value.contacts.allSatisfy { contact in contact.materialIDs.allSatisfy { id in value.materials.contains { $0.id == id } } })
            if company.name == "PARTA" {
                XCTAssertEqual(value.linkedPeople.count, 2)
                XCTAssertEqual(value.otherPeople.count, 23)
                XCTAssertEqual(value.materials.filter { $0.id.hasPrefix("telegram:") }.count, 8)
                XCTAssertTrue(value.addresses.contains { $0.address == "onlainshkolaparta@yandex.ru" })
            }
            summaries.append(["companyID": company.id, "company": company.name, "materials": value.materials.count,
                              "profilesWithCompanyID": value.linkedPeople.count, "otherProfiles": value.otherPeople.count,
                              "addresses": value.addresses.count, "peopleSearchPerformed": value.peopleSearchPerformed,
                              "people": value.linkedPeople.map { ["name": $0.name, "role": $0.role, "url": $0.address] }])
        }
        let report: [String: Any] = ["companies": summaries, "sourceRuns": sources.count, "telegramRuns": telegram.count, "generatedAt": ISO8601DateFormatter().string(from: Date())]
        try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: URL(fileURLWithPath: output))
    }
}
