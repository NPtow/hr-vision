import XCTest
@testable import SignalsCore

final class HistoryResearchTests: XCTestCase {
    private let start = HistoryDate.parse("2026-04-12T00:00:00+03:00")!
    private let end = HistoryDate.parse("2026-10-12T14:00:00+03:00")!
    private func document(_ id: String = "1", text: String = "Ищем менеджера по продажам. Отправляйте резюме.",
                          date: String? = "2026-05-12T10:00:00+03:00", relationship: String = "officialWebsite",
                          quality: String = "fullText", companyID: String = "school") -> SourceHistoryDocument {
        .init(id: id, companyID: companyID, source: "Telegram", url: "https://t.me/school_jobs/\(id)", title: "Новости школы",
              text: text, kind: "post", retrievedAt: "2026-10-12T12:00:00+03:00", quality: quality,
              relationship: relationship, publishedAt: date)
    }
    private func archive(_ documents: [SourceHistoryDocument], companyID: String = "school") -> SourceHistoryArchive {
        .init(id: "archive-\(companyID)", companyID: companyID, companyName: "School", provider: "Telegram",
              windowStart: "2026-04-12T00:00:00+03:00", windowEnd: "2026-10-12T14:00:00+03:00",
              collectedAt: "2026-10-12T12:00:00+03:00", documents: documents)
    }
    private func research(_ docs: [SourceHistoryDocument], materials: [ResearchMaterial] = []) -> HiringResearch {
        .build(companyID: "school", materials: materials, archives: [archive(docs)], start: start, end: end)
    }

    func testRussianHiringLanguageProvidesExactQuoteAndDoesNotInferStatus() {
        let texts = ["Ищем менеджера по продажам. Отправляйте резюме.", "Приглашаем преподавателя Python вести занятия; 1 500 ₽/час",
                     "Требуется куратор студентов: проверка заданий, график 5/2", "Ищем менеджера. Обучение за счёт компании",
                     "Ищем экспертов для записи курса, гонорар по договору", "Набираем студентов на оплачиваемую стажировку в отдел продаж",
                     "Ищем преподавателя на курс Python, оплата 2000 ₽/час; присылайте резюме"]
        let docs = texts.enumerated().map { document(String($0.offset), text: $0.element) }
        let value = research(docs)
        XCTAssertEqual(value.publications.count, texts.count)
        XCTAssertTrue(value.publications.allSatisfy { $0.status == "unknown" })
        XCTAssertTrue(value.publications.allSatisfy { $0.text.contains($0.quote) })
        XCTAssertTrue(value.roleFunctions.contains { $0.function == "Продажи" })
        XCTAssertTrue(value.roleFunctions.contains { $0.function == "Преподавание" })
    }

    func testSchoolEnrollmentAdvertisingAndLearningAreNotHiring() {
        let texts = ["Открыт набор студентов на курс аналитики", "Нам нужен именно ты! Набираем учеников на новый поток",
                     "Ищем в команду учеников для учебного хакатона", "После курса поможем найти вакансии менеджера и составить резюме",
                     "Приглашаем преподавателей на курс повышения квалификации", "Ищем репетитора для моего ребёнка",
                     "Научим тебя стать менеджером по продажам!", "Мы не ищем менеджера по продажам",
                     "Вакансий сейчас нет. Наши преподаватели готовят к ЕГЭ.", "Ищем учеников: лучшие преподаватели и кураторы ЕГЭ",
                     "Приглашаем преподавателей на обучение. Выдадим сертификат.", "Как найти вакансию менеджера после нашего курса",
                     "Наши выпускники находят вакансии менеджеров по продажам", "Набираем студентов на курс Аналитик данных. Поможем с трудоустройством.",
                     "Зачем бизнесу нужен аналитик данных?"]
        for (index, text) in texts.enumerated() {
            let value = research([document(String(index), text: text)])
            XCTAssertTrue(value.publications.isEmpty, text)
            XCTAssertTrue(value.uncertainPublications.isEmpty, text)
        }
    }

    func testDateWindowUsesPublicationNotRetrievalDateAndUnknownIsSeparate() {
        let value = research([
            document("inside", date: "2026-05-12T10:00:00+03:00"),
            document("start", date: "2026-04-12T00:00:00+03:00"),
            document("end", date: "2026-10-12T14:00:00+03:00"),
            document("older", date: "2026-04-11T23:59:59+03:00"),
            document("future", date: "2026-10-12T14:00:01+03:00"),
            document("undated", date: nil), document("invalid", date: "not a date")])
        XCTAssertEqual(value.publications.count, 3)
        XCTAssertEqual(value.undatedPublications.count, 2)
        XCTAssertTrue(value.undatedPublications.allSatisfy { $0.publishedAt == nil })
        XCTAssertEqual(value.outsideWindowCount, 2)
        XCTAssertEqual(value.monthlyCounts.reduce(0) { $0 + $1.count }, 3)
        XCTAssertEqual(value.monthlyCounts.map(\.yearMonth), ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
    }

    func testCalendarMonthUsesMoscowTimezoneAndPartialMonthBounds() {
        let value = research([document("one", date: "2026-05-31T21:30:00Z"), document("two", date: "2026-04-01T12:00:00Z")])
        XCTAssertEqual(value.publications.count, 1)
        XCTAssertEqual(value.monthlyCounts.first { $0.yearMonth == "2026-06" }?.count, 1)
        XCTAssertEqual(value.monthlyCounts.first { $0.yearMonth == "2026-05" }?.count, 0)
        XCTAssertEqual(value.outsideWindowCount, 1)
    }

    func testCandidateSnippetAndOtherEmployerNeverEnterConfirmedCounts() {
        let value = research([document("candidate", relationship: "candidate"), document("snippet", quality: "snippet"),
                              document("partner", text: "Наш партнёр ищет менеджера по продажам. Резюме отправляйте партнёру.")])
        XCTAssertTrue(value.publications.isEmpty)
        XCTAssertEqual(value.uncertainPublications.count, 3)
        XCTAssertEqual(value.monthlyCounts.reduce(0) { $0 + $1.count }, 0)
        XCTAssertTrue(value.uncertainPublications.first { $0.url?.hasSuffix("partner") == true }?.uncertainty.contains { $0.contains("другой организации") } == true)
    }

    func testExplicitVacancyMetadataAndObservedStatus() {
        var vacancy = document("hh", text: "", relationship: "employerID", quality: "metadata")
        vacancy.source = "HH"; vacancy.url = "https://hh.ru/vacancy/123"; vacancy.kind = "vacancy"
        vacancy.title = "Руководитель отдела продаж"; vacancy.jobTitle = vacancy.title; vacancy.employerID = "verified-123"
        vacancy.status = "archived"; vacancy.salary = "от 120 000 ₽ до вычета налогов"
        let value = research([vacancy])
        XCTAssertEqual(value.publications.count, 1)
        XCTAssertEqual(value.publications.first?.status, "archived")
        XCTAssertEqual(value.publications.first?.function, "Руководство продажами")
        XCTAssertEqual(value.publications.first?.salary, vacancy.salary)
        XCTAssertEqual(value.publications.first?.quote, vacancy.title)
        vacancy.quality = "snippet"
        XCTAssertTrue(research([vacancy]).publications.isEmpty)
    }

    func testDuplicatesUseURLOrSourceIDButNeverCollapseSameRoleAcrossDates() {
        var repeatedObservation = document("1")
        repeatedObservation.url = "https://t.me/s/school_jobs/1/?utm_source=search"
        repeatedObservation.retrievedAt = "2026-10-12T13:00:00+03:00"; repeatedObservation.status = "archived"
        var samePlatformID = document("different-local-id")
        samePlatformID.sourceID = "1"; samePlatformID.url = "https://telegram.me/school_jobs/1"
        var original = document("1"); original.sourceID = "1"
        let material = SourceHistoryMaterials.material(original, archiveID: "also-in-materials")
        let value = research([original, repeatedObservation, samePlatformID,
                              document("2", date: "2026-06-23T12:00:00+03:00")], materials: [material])
        XCTAssertEqual(value.publications.count, 2)
        XCTAssertEqual(Set(value.publications.map(\.possibleRepeatCount)), [1])
        XCTAssertEqual(value.monthlyCounts.first { $0.yearMonth == "2026-05" }?.count, 1)
        XCTAssertEqual(value.monthlyCounts.first { $0.yearMonth == "2026-06" }?.count, 1)
        XCTAssertEqual(value.publications.first { $0.url?.contains("/1") == true }?.status, "archived")
    }

    func testSameSourceIDCanDeduplicateURLAliasesButOtherSourcesRemainSeparate() {
        var a = document("first"); a.sourceID = "provider-123"
        var b = document("second"); b.sourceID = "provider-123"; b.url = "https://t.me/other_alias/99"
        var c = document("third"); c.sourceID = "provider-123"; c.source = "VK"; c.url = "https://vk.com/wall-1_99"
        XCTAssertEqual(research([a, b, c]).publications.count, 2)
    }

    func testArchivesAndDocumentsCannotLeakAcrossCompanyScope() {
        let other = archive([document(companyID: "other")], companyID: "other")
        let inconsistent = archive([document(companyID: "other")])
        let value = HiringResearch.build(companyID: "school", materials: [], archives: [other, inconsistent], start: start, end: end)
        XCTAssertTrue(value.publications.isEmpty)
        XCTAssertTrue(SourceHistoryMaterials.merge(companyID: "school", materials: [], archives: [other, inconsistent]).isEmpty)
        XCTAssertThrowsError(try JSONDecoder().decode(SourceHistoryArchive.self, from: JSONEncoder().encode(inconsistent)))
    }

    func testOptionalMissingAndMalformedFieldsAreForgivenButVersionIsValidated() throws {
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(archive([document()]))) as? [String: Any])
        json.removeValue(forKey: "coverage"); json.removeValue(forKey: "notes")
        var docs = try XCTUnwrap(json["documents"] as? [[String: Any]])
        docs[0]["salary"] = ["unexpected": 123]; docs[0]["publishedAt"] = NSNull(); docs[0]["sourceID"] = 123
        json["documents"] = docs
        let decoded = try JSONDecoder().decode(SourceHistoryArchive.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertNil(decoded.documents.first?.salary); XCTAssertNil(decoded.documents.first?.sourceID)
        XCTAssertNil(decoded.documents.first?.publishedAt); XCTAssertTrue(decoded.notes.isEmpty)
        json["version"] = 2
        XCTAssertThrowsError(try JSONDecoder().decode(SourceHistoryArchive.self, from: JSONSerialization.data(withJSONObject: json)))
    }

    func testStoreIsolatesCorruptFilesAndDoesNotMutateFiles() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let good = try JSONEncoder().encode(archive([document()]))
        let corrupt = Data("{bad json".utf8)
        try good.write(to: directory.appendingPathComponent("good.json"))
        try corrupt.write(to: directory.appendingPathComponent("bad.json"))
        let inconsistent = try JSONEncoder().encode(archive([document(companyID: "other")]))
        try inconsistent.write(to: directory.appendingPathComponent("scope.json"))
        let result = SourceHistoryStore(directory: directory).load()
        XCTAssertEqual(result.archives.count, 1); XCTAssertEqual(result.errors.count, 2)
        XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent("good.json")), good)
        XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent("bad.json")), corrupt)
    }

    func testStoreUsesEnvironmentDataDirectory() {
        let key = "TG_SIGNALS_DATA_DIR", old = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"]
        setenv(key, "/tmp/history-store-test-root", 1)
        defer { if let old { setenv(key, old, 1) } else { unsetenv(key) } }
        XCTAssertEqual(SourceHistoryStore().directory.path, "/tmp/history-store-test-root/source-history")
    }

    func testMaterialMergeKeepsVerifiedTextAndNeverPromotesCandidateDate() {
        let trusted = document("same", text: "Проверенный текст без даты", date: nil)
        let existing = SourceHistoryMaterials.material(trusted, archiveID: "trusted")
        var candidate = document("same", text: "Ищем менеджера по продажам. " + String(repeating: "Непроверенный текст. ", count: 10), relationship: "candidate")
        candidate.retrievedAt = "2026-10-12T13:00:00+03:00"
        let merged = SourceHistoryMaterials.merge(companyID: "school", materials: [existing], archives: [archive([candidate])])
        XCTAssertEqual(merged.count, 1); XCTAssertEqual(merged.first?.text, trusted.text)
        XCTAssertNil(merged.first?.publishedAt)
        XCTAssertFalse(merged.first?.relationship.contains("Кандидат") ?? true)
        XCTAssertEqual(merged.first?.evidence.count, 2)
        let candidates = SourceHistoryMaterials.merge(companyID: "school", materials: [], archives: [archive([candidate])])
        XCTAssertFalse(candidates[0].isOfficialDomain)
        XCTAssertTrue(candidates[0].relationship.contains("Кандидат"))
    }

    func testTelegramSearchPageDoesNotCollapseDistinctPostsOrCountAsOriginal() {
        var a = document("1"), b = document("2")
        a.url = "https://telemetr.me/posts?text=school"; b.url = a.url
        let merged = SourceHistoryMaterials.merge(companyID: "school", materials: [], archives: [archive([a, b])])
        XCTAssertEqual(merged.count, 2)
        let value = research([a, b])
        XCTAssertTrue(value.publications.isEmpty); XCTAssertEqual(value.uncertainPublications.count, 2)
    }

    func testCoveragePreservesUnavailabilityAndMaterialOnlySourceIsPartial() {
        var saved = archive([])
        saved.coverage = [.init(id: "vk", source: "VK", status: "unavailable", note: "403 при проверке")]
        let material = SourceHistoryMaterials.material(document(), archiveID: "tg")
        let value = HiringResearch.build(companyID: "school", materials: [material], archives: [saved], start: start, end: end)
        XCTAssertEqual(value.sourceCoverage.first { $0.source == "VK" }?.status, "unavailable")
        XCTAssertEqual(value.sourceCoverage.first { $0.source == "Telegram" }?.status, "partial")
        XCTAssertTrue(value.sourceCoverage.first { $0.source == "Telegram" }?.note.contains("не подтверждены") == true)
    }

    func testCommentsAndEmptyFullTextDoNotBecomeConfirmedEmployerAnnouncements() {
        var comment = document("comment"); comment.kind = "comment"
        var empty = document("empty", text: ""); empty.title = "Вакансия менеджера"
        let merged = SourceHistoryMaterials.merge(companyID: "school", materials: [], archives: [archive([comment, empty])])
        let value = research([comment, empty], materials: merged)
        XCTAssertTrue(value.publications.isEmpty)
        XCTAssertEqual(value.uncertainPublications.count, 2)
        XCTAssertEqual(value.sourceCoverage.first?.status, "partial", "Archive documents need coverage even if no separate check was saved")
        XCTAssertEqual(value.sourceCoverage.first?.receivedCount, 2)
    }

    func testReviewedRelevanceDoesNotProveEmployerIdentityAndTrackingURLsDeduplicate() {
        var relevanceOnly = SourceHistoryMaterials.material(document("relevance"), archiveID: "tg")
        relevanceOnly.relationship = "Относится к компании · проверено вручную"; relevanceOnly.isOfficialDomain = false
        let unverified = HiringResearch.build(companyID: "school", materials: [relevanceOnly], archives: [], start: start, end: end)
        XCTAssertTrue(unverified.publications.isEmpty); XCTAssertEqual(unverified.uncertainPublications.count, 1)
        var hh = document("123"); hh.source = "HH"; hh.url = "https://hh.ru/vacancy/123"
        var legacy = SourceHistoryMaterials.material(hh, archiveID: "legacy")
        legacy.id = "legacy-hh"; legacy.url = "https://hh.ru/vacancy/123?hhtmFrom=vacancy_search_list&from=search"
        XCTAssertEqual(research([hh], materials: [legacy]).publications.count, 1)
    }

    func testLaterTextReadPreservesProviderStatusAndItsActualObservationDate() {
        var doc = document("hh"); doc.status = "archived"; doc.salary = "120 000 ₽"; doc.employerID = "123"
        var material = SourceHistoryMaterials.material(doc, archiveID: "legacy")
        material.id = "newer-text-only"; material.retrievedAt = HistoryDate.parse("2026-10-12T13:00:00+03:00")!
        let row = research([doc], materials: [material]).publications.first!
        XCTAssertEqual(row.status, "archived"); XCTAssertEqual(row.salary, doc.salary)
        XCTAssertEqual(row.employerID, doc.employerID)
        XCTAssertEqual(row.statusObservedAt, HistoryDate.parse(doc.retrievedAt))
        XCTAssertEqual(row.lastObservedAt, material.retrievedAt)
        XCTAssertEqual(row.observations.first { $0.observedAt == material.retrievedAt }?.status, "unknown")
    }
    func testEditorialExclusionSurvivesDecodingAndMergedMaterials() throws {
        var doc = document("excluded", text: "Ищем менеджера. Пример из статьи, не объявление.")
        doc.hiringDisposition = "excluded"
        let restored = try JSONDecoder().decode(SourceHistoryDocument.self, from: JSONEncoder().encode(doc))
        XCTAssertEqual(restored.hiringDisposition, "excluded")
        let merged = SourceHistoryMaterials.merge(companyID: "school", materials: [], archives: [archive([restored])])
        XCTAssertTrue(research([restored], materials: merged).publications.isEmpty)
    }

    func testReviewedQuoteUsesExactRoleFragmentAfterLongNavigation() throws {
        let quote = "Менеджер по продажам. Обязанности: консультации клиентов. Резюме отправляйте в отдел кадров."
        var doc = document("career", text: String(repeating: "Курсы | Блог | О компании | ", count: 80) + "\n" + quote)
        doc.kind = "vacancy"; doc.jobTitle = "Менеджер по продажам"; doc.hiringQuote = quote
        let decoded = try JSONDecoder().decode(SourceHistoryDocument.self, from: JSONEncoder().encode(doc))
        XCTAssertEqual(decoded.hiringQuote, quote)
        let row = try XCTUnwrap(research([decoded]).publications.first)
        XCTAssertEqual(row.quote, quote)
        XCTAssertTrue(row.text.contains(row.quote))
    }

    func testInvalidPreferredQuoteFallsBackToExactContextAroundRole() throws {
        let body = "Менеджер по продажам. Обязанности: консультации клиентов. Требования: опыт продаж."
        var doc = document("career", text: String(repeating: "Навигация | Каталог курсов | ", count: 80) + "\n" + body)
        doc.kind = "vacancy"; doc.jobTitle = "Менеджер по продажам"
        doc.hiringQuote = "Выдуманная зарплата 500 000 ₽ и обещание вакансии"
        let row = try XCTUnwrap(research([doc]).publications.first)
        XCTAssertTrue(row.quote.contains(doc.jobTitle!))
        XCTAssertTrue(row.quote.contains("Обязанности"))
        XCTAssertFalse(row.quote.contains("500 000"))
        XCTAssertTrue(row.text.contains(row.quote))
        XCTAssertNotEqual(row.quote, String(doc.text.prefix(700)))

        var nonHiring = document("post", text: "Полезные статьи и бесплатные учебные материалы")
        nonHiring.hiringQuote = nonHiring.text
        XCTAssertTrue(research([nonHiring]).publications.isEmpty, "A preferred quote alone must not classify a post as hiring")
    }

    func testReviewedExclusionSuppressesOriginalArchiveAndVisibleMaterialByCanonicalURL() throws {
        let original = document("original", text: "Ищем менеджера по продажам. Отправляйте резюме.")
        var review = original
        review.id = "review-copy"; review.url = "https://telegram.me/s/school_jobs/original/?utm_source=review"
        review.hiringDisposition = "excluded"; review.reviewedAt = "2026-10-13T10:00:00+03:00"
        let decoded = try JSONDecoder().decode(SourceHistoryDocument.self, from: JSONEncoder().encode(review))
        XCTAssertEqual(decoded.reviewedAt, review.reviewedAt)
        var visible = SourceHistoryMaterials.material(original, archiveID: "old")
        visible.id = "legacy-visible"
        for docs in [[original, decoded], [decoded, original]] {
            let result = research(docs, materials: [visible])
            XCTAssertTrue(result.publications.isEmpty)
            XCTAssertTrue(result.undatedPublications.isEmpty)
            XCTAssertTrue(result.uncertainPublications.isEmpty)
        }
    }

    func testLatestReviewedUncertaintyOverridesTrustedOriginalWithoutPromotingCandidate() throws {
        var original = document("original")
        original.retrievedAt = "2026-10-14T10:00:00+03:00"
        var review = original
        review.id = "review-copy"; review.relationship = "candidate"; review.kind = "vacancy"
        review.jobTitle = "Менеджер по продажам"; review.reviewedAt = "2026-10-13T10:00:00+03:00"
        review.retrievedAt = "2026-10-12T10:00:00+03:00"
        var visible = SourceHistoryMaterials.material(original, archiveID: "old")
        visible.id = "legacy-visible"
        for docs in [[original, review], [review, original]] {
            let result = research(docs, materials: [visible])
            XCTAssertTrue(result.publications.isEmpty)
            XCTAssertEqual(result.uncertainPublications.count, 1)
            let row = try XCTUnwrap(result.uncertainPublications.first)
            XCTAssertTrue(row.uncertainty.contains { $0.contains("Связь источника") })
            XCTAssertTrue(row.relationship.contains("Кандидат"))
            XCTAssertEqual(row.materialID, visible.id)
        }
        review.reviewedAt = nil
        XCTAssertEqual(research([original, review], materials: [visible]).publications.count, 1,
                       "An unreviewed untrusted copy must retain the previous trusted-copy precedence")
    }

    func testLatestExplicitReviewWinsRatherThanArchiveOrderOrTrust() {
        let original = document("original")
        var excluded = original; excluded.id = "excluded"
        excluded.hiringDisposition = "excluded"; excluded.reviewedAt = "2026-10-12T12:00:00+03:00"
        var confirmed = original; confirmed.id = "confirmed"; confirmed.kind = "vacancy"
        confirmed.hiringDisposition = "reviewedVacancy"; confirmed.reviewedAt = "2026-10-13T12:00:00+03:00"
        XCTAssertEqual(research([confirmed, original, excluded]).publications.count, 1)
        excluded.reviewedAt = "2026-10-14T12:00:00+03:00"
        XCTAssertTrue(research([confirmed, original, excluded]).publications.isEmpty)
        XCTAssertTrue(research([excluded, original, confirmed]).publications.isEmpty)
    }

    func testReviewedExclusionMatchesExplicitProviderIDButNotSearchPageNeighbours() {
        var original = document("original"); original.sourceID = "provider-42"
        var review = original; review.id = "review-copy"; review.url = "https://t.me/renamed_channel/42"
        review.hiringDisposition = "excluded"; review.reviewedAt = "2026-10-13T12:00:00+03:00"
        XCTAssertTrue(research([original, review]).publications.isEmpty)

        var unreviewedSearchPost = document("separate")
        unreviewedSearchPost.url = "https://telemetr.me/posts?text=school"
        review.url = unreviewedSearchPost.url
        let result = research([unreviewedSearchPost, review])
        XCTAssertEqual(result.uncertainPublications.count, 1, "A shared search URL cannot identify the reviewed original")
    }

    func testSchoolAdviceAndCourseInvitationsAreNotEmployerRecruitment() {
        for text in ["Ребёнок молчит. Родители говорят: ему нужен репетитор. Запишитесь на курс, оплата на сайте.", "Приглашаем учителей и репетиторов на бесплатный вебинар по обучению.", "Он ищет физика, добивается экспертизы, эксперимента. Обсуждаем сюжет книги."] {
            XCTAssertTrue(research([document("ad", text: text)]).publications.isEmpty, text)
        }
    }

    func testHiringReferencePointsToVisibleMergedMaterial() {
        let doc = document()
        var legacy = SourceHistoryMaterials.material(doc, archiveID: "older")
        legacy.id = "existing-visible-material"
        let materials = SourceHistoryMaterials.merge(companyID: "school", materials: [legacy], archives: [archive([doc])])
        let result = research([doc], materials: materials)
        XCTAssertEqual(result.publications.first?.materialID, materials.first?.id)
    }

}
