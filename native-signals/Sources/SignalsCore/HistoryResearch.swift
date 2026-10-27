import Foundation

/// Dates supplied by a source. Retrieval time is never a publication-date fallback.
public enum HistoryDate {
    public static func parse(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        if let date = iso.date(from: value) { return date }
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) { return date }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "Europe/Moscow")
        formatter.isLenient = false
        formatter.dateFormat = "yyyy-MM-dd"
        guard value.count == 10 else { return nil }
        return formatter.date(from: value)
    }
}

public struct SourceHistoryArchive: Codable, Sendable, Identifiable {
    public var version: Int
    public var id: String
    public var companyID: String
    public var companyName: String
    public var provider: String
    public var windowStart: String
    public var windowEnd: String
    public var collectedAt: String
    public var documents: [SourceHistoryDocument]
    public var coverage: [SourceHistoryCoverage]
    public var notes: [String]

    public init(version: Int = 1, id: String, companyID: String, companyName: String, provider: String,
                windowStart: String, windowEnd: String, collectedAt: String,
                documents: [SourceHistoryDocument] = [], coverage: [SourceHistoryCoverage] = [], notes: [String] = []) {
        self.version = version; self.id = id; self.companyID = companyID; self.companyName = companyName
        self.provider = provider; self.windowStart = windowStart; self.windowEnd = windowEnd
        self.collectedAt = collectedAt; self.documents = documents; self.coverage = coverage; self.notes = notes
    }

    public var isValid: Bool {
        version == 1 && !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !companyID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && documents.allSatisfy { $0.companyID == companyID && !$0.id.isEmpty }
    }

    private enum CodingKeys: String, CodingKey {
        case version, id, companyID, companyName, provider, windowStart, windowEnd, collectedAt, documents, coverage, notes
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decode(Int.self, forKey: .version); id = try c.decode(String.self, forKey: .id)
        companyID = try c.decode(String.self, forKey: .companyID)
        companyName = try c.decode(String.self, forKey: .companyName)
        provider = try c.decode(String.self, forKey: .provider)
        windowStart = try c.decode(String.self, forKey: .windowStart)
        windowEnd = try c.decode(String.self, forKey: .windowEnd)
        collectedAt = try c.decode(String.self, forKey: .collectedAt)
        documents = try c.decode([SourceHistoryDocument].self, forKey: .documents)
        coverage = try c.decodeIfPresent([SourceHistoryCoverage].self, forKey: .coverage) ?? []
        notes = (try? c.decode([String].self, forKey: .notes)) ?? []
        guard isValid else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                debugDescription: "Unsupported history version or inconsistent company scope"))
        }
    }
}

public struct SourceHistoryDocument: Codable, Sendable, Identifiable {
    public var id: String
    public var companyID: String
    public var source: String
    public var sourceID: String?
    public var url: String
    public var title: String
    public var text: String
    public var kind: String
    public var author: String?
    public var publishedAt: String?
    public var retrievedAt: String
    public var quality: String
    public var relationship: String
    public var relationshipSourceURL: String?
    public var status: String
    public var jobTitle: String?
    public var jobFunction: String?
    public var salary: String?
    public var location: String?
    public var employment: String?
    public var employerID: String?
    public var contact: String?
    public var hiringDisposition: String? = nil
    public var hiringQuote: String? = nil
    public var reviewedAt: String? = nil

    public init(id: String, companyID: String, source: String, url: String, title: String, text: String,
                kind: String, retrievedAt: String, quality: String, relationship: String, status: String = "unknown",
                sourceID: String? = nil, author: String? = nil, publishedAt: String? = nil,
                relationshipSourceURL: String? = nil, jobTitle: String? = nil, jobFunction: String? = nil,
                salary: String? = nil, location: String? = nil, employment: String? = nil,
                employerID: String? = nil, contact: String? = nil) {
        self.id = id; self.companyID = companyID; self.source = source; self.sourceID = sourceID
        self.url = url; self.title = title; self.text = text; self.kind = kind; self.author = author
        self.publishedAt = publishedAt; self.retrievedAt = retrievedAt; self.quality = quality
        self.relationship = relationship; self.relationshipSourceURL = relationshipSourceURL; self.status = status
        self.jobTitle = jobTitle; self.jobFunction = jobFunction; self.salary = salary; self.location = location
        self.employment = employment; self.employerID = employerID; self.contact = contact
    }

    public var hasVerifiedRelationship: Bool { ["officialWebsite", "employerID", "reviewedIdentity"].contains(relationship) }
    private enum CodingKeys: String, CodingKey {
        case id, companyID, source, sourceID, url, title, text, kind, author, publishedAt, retrievedAt, quality
        case relationship, relationshipSourceURL, status, jobTitle, jobFunction, salary, location, employment, employerID, contact, hiringDisposition, hiringQuote, reviewedAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); companyID = try c.decode(String.self, forKey: .companyID)
        source = try c.decode(String.self, forKey: .source); url = try c.decode(String.self, forKey: .url)
        title = try c.decode(String.self, forKey: .title); text = try c.decode(String.self, forKey: .text)
        kind = try c.decode(String.self, forKey: .kind); retrievedAt = try c.decode(String.self, forKey: .retrievedAt)
        quality = try c.decode(String.self, forKey: .quality); relationship = try c.decode(String.self, forKey: .relationship)
        status = try c.decode(String.self, forKey: .status)
        sourceID = try? c.decode(String.self, forKey: .sourceID); author = try? c.decode(String.self, forKey: .author)
        publishedAt = try? c.decode(String.self, forKey: .publishedAt)
        relationshipSourceURL = try? c.decode(String.self, forKey: .relationshipSourceURL)
        jobTitle = try? c.decode(String.self, forKey: .jobTitle); jobFunction = try? c.decode(String.self, forKey: .jobFunction)
        salary = try? c.decode(String.self, forKey: .salary); location = try? c.decode(String.self, forKey: .location)
        employment = try? c.decode(String.self, forKey: .employment); employerID = try? c.decode(String.self, forKey: .employerID)
        contact = try? c.decode(String.self, forKey: .contact)
        hiringDisposition = try? c.decode(String.self, forKey: .hiringDisposition)
        hiringQuote = try? c.decode(String.self, forKey: .hiringQuote)
        reviewedAt = try? c.decode(String.self, forKey: .reviewedAt)
    }
}

public struct SourceHistoryCoverage: Codable, Sendable, Identifiable {
    public var id: String
    public var source: String
    public var sourceURL: String?
    public var status: String
    public var requestedFrom: String?
    public var requestedTo: String?
    public var earliestPublishedAt: String?
    public var latestPublishedAt: String?
    public var receivedCount: Int
    public var note: String
    public var checkedAt: String?

    public init(id: String, source: String, sourceURL: String? = nil, status: String = "partial",
                requestedFrom: String? = nil, requestedTo: String? = nil, earliestPublishedAt: String? = nil,
                latestPublishedAt: String? = nil, receivedCount: Int = 0, note: String = "", checkedAt: String? = nil) {
        self.id = id; self.source = source; self.sourceURL = sourceURL; self.status = status
        self.requestedFrom = requestedFrom; self.requestedTo = requestedTo; self.earliestPublishedAt = earliestPublishedAt
        self.latestPublishedAt = latestPublishedAt; self.receivedCount = receivedCount; self.note = note; self.checkedAt = checkedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, source, sourceURL, status, requestedFrom, requestedTo, earliestPublishedAt, latestPublishedAt, receivedCount, note, checkedAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); source = try c.decode(String.self, forKey: .source)
        status = try c.decode(String.self, forKey: .status)
        sourceURL = try? c.decode(String.self, forKey: .sourceURL)
        requestedFrom = try? c.decode(String.self, forKey: .requestedFrom); requestedTo = try? c.decode(String.self, forKey: .requestedTo)
        earliestPublishedAt = try? c.decode(String.self, forKey: .earliestPublishedAt)
        latestPublishedAt = try? c.decode(String.self, forKey: .latestPublishedAt)
        receivedCount = max(0, (try? c.decode(Int.self, forKey: .receivedCount)) ?? 0)
        note = (try? c.decode(String.self, forKey: .note)) ?? ""
        checkedAt = try? c.decode(String.self, forKey: .checkedAt)
    }
}

/// Read-only store. A malformed file does not suppress other companies or overwrite any archive.
public struct SourceHistoryStore: Sendable {
    public let directory: URL
    public init(directory: URL? = nil) {
        let base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        self.directory = directory ?? base.appendingPathComponent("source-history", isDirectory: true)
    }
    public func load() -> (archives: [SourceHistoryArchive], errors: [String]) {
        guard FileManager.default.fileExists(atPath: directory.path) else { return ([], []) }
        var archives: [SourceHistoryArchive] = [], errors: [String] = []
        do {
            let urls = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
                .filter { $0.pathExtension.lowercased() == "json" }.sorted { $0.path < $1.path }
            for url in urls {
                do { archives.append(try JSONDecoder().decode(SourceHistoryArchive.self, from: Data(contentsOf: url))) }
                catch { errors.append("Не прочитан архив \(url.lastPathComponent): \(error.localizedDescription)") }
            }
        } catch { errors.append("Не прочитан каталог истории источников: \(error.localizedDescription)") }
        return (archives, errors)
    }
}

public enum SourceHistoryMaterials {
    /// `materials` must already be company-scoped (the output of CompanyResearch.build).
    public static func merge(companyID: String, materials: [ResearchMaterial], archives: [SourceHistoryArchive]) -> [ResearchMaterial] {
        var result = materials
        var byID: [String: Int] = [:], byURL: [String: Int] = [:]
        for (i, item) in result.enumerated() {
            if byID[item.id] == nil { byID[item.id] = i }
            if let url = item.url, !HistoryIdentity.isSearchPage(url), let key = HistoryIdentity.canonicalURL(url), byURL[key] == nil { byURL[key] = i }
        }
        for archive in archives where archive.isValid && archive.companyID == companyID {
            for doc in archive.documents {
                let item = material(doc, archiveID: archive.id)
                let key = item.url.flatMap { HistoryIdentity.isSearchPage($0) ? nil : HistoryIdentity.canonicalURL($0) }
                if let index = [byID[item.id], key.flatMap { byURL[$0] }].compactMap({ $0 }).min() {
                    let old = result[index]
                    // A longer candidate text must not inherit the trusted relationship of the existing document.
                    let oldTrust = HistoryIdentity.isTrusted(old), newTrust = doc.hasVerifiedRelationship
                    let useNew = oldTrust != newTrust ? newTrust : (item.hasFullText != old.hasFullText ? item.hasFullText : item.retrievedAt > old.retrievedAt)
                    var merged = useNew ? item : old
                    merged.id = old.id
                    merged.providers = Array(Set(old.providers + item.providers)).sorted()
                    var evidenceIDs: Set<String> = []
                    merged.evidence = (old.evidence + item.evidence).filter { evidenceIDs.insert($0.id).inserted }
                    // Do not import a candidate's date into an otherwise trusted observation.
                    if oldTrust == newTrust { merged.publishedAt = merged.publishedAt ?? old.publishedAt ?? item.publishedAt }
                    result[index] = merged
                } else {
                    byID[item.id] = result.count
                    if let key { byURL[key] = result.count }
                    result.append(item)
                }
            }
        }
        return result.sorted {
            let a = $0.publishedAt ?? $0.retrievedAt, b = $1.publishedAt ?? $1.retrievedAt
            return a == b ? $0.id < $1.id : a > b
        }
    }

    public static func material(_ document: SourceHistoryDocument, archiveID: String) -> ResearchMaterial {
        let quality = document.quality == "fullText" ? "Полный текст сохранён в архиве истории."
            : document.quality == "snippet" ? "Фрагмент; полный текст источника не получен." : "Метаданные источника; полнота текста не подтверждена."
        let relationship: String
        switch document.relationship {
        case "officialWebsite": relationship = "Источник связан с официальным сайтом компании"
        case "employerID": relationship = "Связь по подтверждённому employer ID"
        case "reviewedIdentity": relationship = "Связь с компанией проверена вручную"
        default: relationship = "Кандидат · связь с компанией не подтверждена"
        }
        let evidence = Evidence(id: "history:\(archiveID):\(document.id)", title: document.title, excerpt: document.text,
            url: document.url, dateLabel: document.publishedAt ?? "Дата публикации неизвестна", kind: document.kind, isDemo: false,
            provenance: .init(provider: document.source, query: "История источников", retrievedAt: document.retrievedAt,
                              channel: document.author, collectionID: archiveID, contentType: quality))
        let kind: ResearchMaterialKind = document.kind == "vacancy" ? .hiring
            : ["post", "discussion", "comment"].contains(document.kind) ? .publication : document.kind == "profile" ? .profile : .page
        return ResearchMaterial(id: "history:\(document.source):\(document.id)", title: document.title, text: document.text,
            url: document.url, dateLabel: evidence.dateLabel, publishedAt: HistoryDate.parse(document.publishedAt),
            retrievedAt: HistoryDate.parse(document.retrievedAt) ?? .distantPast, kind: kind, providers: [document.source],
            quality: quality, relationship: relationship, evidence: [evidence], originalAvailable: HistoryIdentity.validOriginal(document.url),
            isOfficialDomain: document.relationship == "officialWebsite", hasFullText: document.quality == "fullText")
    }
}

public struct HiringObservation: Identifiable, Sendable {
    public var id: String
    public var observedAt: Date
    public var status: String
    public var text: String
    public var url: String?
}

public struct HiringPublication: Identifiable, Sendable {
    public var id: String
    public var companyID: String
    public var title: String
    public var function: String
    public var publishedAt: Date?
    public var source: String
    public var sourceID: String?
    public var url: String?
    public var status: String
    public var statusObservedAt: Date?
    public var quote: String
    public var text: String
    public var relationship: String
    public var salary: String?
    public var location: String?
    public var employment: String?
    public var employerID: String?
    public var contact: String?
    public var materialID: String
    public var lastObservedAt: Date
    public var uncertainty: [String]
    public var observations: [HiringObservation]
    /// Other distinct postings with the same role label. This is not a proven republication or an opening count.
    public var possibleRepeatCount: Int = 0
    public var isConfirmed: Bool { uncertainty.isEmpty }
    public var searchText: String { [title, function, source, text, relationship, salary ?? "", location ?? ""].joined(separator: " ") }
    public var statusLabel: String {
        switch status {
        case "active": return "Активна у источника"
        case "archived": return "В архиве источника"
        case "unavailable": return "Недоступна при проверке"
        default: return "Статус неизвестен"
        }
    }
}

public struct HiringMonthCount: Identifiable, Sendable {
    public var id: String
    public var label: String
    public var count: Int
    public var publicationIDs: [String]
    public var yearMonth: String { id }
}

public struct HiringFunctionCount: Identifiable, Sendable {
    public var function: String
    public var count: Int
    public var publicationIDs: [String]
    public var id: String { function }
}

public struct HiringResearch: Sendable {
    public var companyID: String
    public var start: Date
    public var end: Date
    public var publications: [HiringPublication]
    public var undatedPublications: [HiringPublication]
    public var uncertainPublications: [HiringPublication]
    public var outsideWindowCount: Int
    public var monthlyCounts: [HiringMonthCount]
    public var sourceCoverage: [SourceHistoryCoverage]
    public var roleFunctions: [HiringFunctionCount]
    public var notes: [String]

    /// Counts source publications, never actual hires, unique openings, or uninterrupted vacancy duration.
    /// `materials` must be the selected company's CompanyResearch projection.
    public static func build(companyID: String, materials: [ResearchMaterial], archives: [SourceHistoryArchive], start: Date, end: Date) -> Self {
        let selected = archives.filter { $0.isValid && $0.companyID == companyID }
        let archivedDocuments = selected.flatMap { archive in archive.documents.map { (document: $0, archiveID: archive.id) } }
        // A review belongs to the publication, not just to one collected copy. Otherwise an older
        // trusted copy can resurrect an exclusion or turn a reviewed candidate back into confirmed hiring.
        let reviewed = archivedDocuments.filter {
            HistoryDate.parse($0.document.reviewedAt) != nil || ["excluded", "reviewedVacancy"].contains($0.document.hiringDisposition ?? "")
        }.sorted {
            let left = HistoryDate.parse($0.document.reviewedAt) ?? .distantPast
            let right = HistoryDate.parse($1.document.reviewedAt) ?? .distantPast
            if left != right { return left > right }
            let observedLeft = HistoryDate.parse($0.document.retrievedAt) ?? .distantPast
            let observedRight = HistoryDate.parse($1.document.retrievedAt) ?? .distantPast
            if observedLeft != observedRight { return observedLeft > observedRight }
            let leftExcluded = $0.document.hiringDisposition == "excluded", rightExcluded = $1.document.hiringDisposition == "excluded"
            if leftExcluded != rightExcluded { return leftExcluded }
            if $0.document.hasVerifiedRelationship != $1.document.hasVerifiedRelationship { return !$0.document.hasVerifiedRelationship }
            return $0.document.id == $1.document.id ? $0.archiveID < $1.archiveID : $0.document.id < $1.document.id
        }
        var reviewedIDs: [String: Int] = [:], reviewedURLs: [String: Int] = [:], reviewedSourceIDs: [String: Int] = [:]
        for (index, item) in reviewed.enumerated() {
            let doc = item.document, id = "history:\(item.document.source):\(item.document.id)"
            if reviewedIDs[id] == nil { reviewedIDs[id] = index }
            if !HistoryIdentity.isSearchPage(doc.url), let key = HistoryIdentity.canonicalURL(doc.url), reviewedURLs[key] == nil { reviewedURLs[key] = index }
            if let sourceID = doc.sourceID?.nilIfEmpty {
                let key = sourceName(doc.source, url: doc.url) + ":" + sourceID
                if reviewedSourceIDs[key] == nil { reviewedSourceIDs[key] = index }
            }
        }
        func reviewIndex(id: String, url: String?, document: SourceHistoryDocument? = nil) -> Int? {
            let byURL = url.flatMap { HistoryIdentity.isSearchPage($0) ? nil : HistoryIdentity.canonicalURL($0) }.flatMap { reviewedURLs[$0] }
            let bySourceID = document.flatMap { doc in
                doc.sourceID?.nilIfEmpty.flatMap { reviewedSourceIDs[sourceName(doc.source, url: doc.url) + ":" + $0] }
            }
            return [reviewedIDs[id], byURL, bySourceID].compactMap { $0 }.min()
        }
        var candidates: [HiringPublication] = []
        for item in archivedDocuments {
            let doc = item.document
            guard reviewIndex(id: "history:\(doc.source):\(doc.id)", url: doc.url, document: doc) == nil else { continue }
            let material = SourceHistoryMaterials.material(doc, archiveID: item.archiveID)
            if let row = publication(companyID: companyID, material: material, document: doc) { candidates.append(row) }
        }
        for (index, item) in reviewed.enumerated() {
            let doc = item.document
            guard reviewIndex(id: "history:\(doc.source):\(doc.id)", url: doc.url, document: doc) == index else { continue }
            let material = SourceHistoryMaterials.material(doc, archiveID: item.archiveID)
            if let row = publication(companyID: companyID, material: material, document: doc) { candidates.append(row) }
        }
        let sourceDocuments = selected.flatMap(\.documents).sorted {
            if $0.hasVerifiedRelationship != $1.hasVerifiedRelationship { return $0.hasVerifiedRelationship }
            return (HistoryDate.parse($0.retrievedAt) ?? .distantPast) > (HistoryDate.parse($1.retrievedAt) ?? .distantPast)
        }
        var documentIDs: [String: Int] = [:], documentURLs: [String: Int] = [:]
        for (index, doc) in sourceDocuments.enumerated() {
            let id = "history:\(doc.source):\(doc.id)"
            if documentIDs[id] == nil { documentIDs[id] = index }
            if !HistoryIdentity.isSearchPage(doc.url), let key = HistoryIdentity.canonicalURL(doc.url), documentURLs[key] == nil { documentURLs[key] = index }
        }
        for material in materials {
            let urlIndex = material.url.flatMap { HistoryIdentity.isSearchPage($0) ? nil : HistoryIdentity.canonicalURL($0) }.flatMap { documentURLs[$0] }
            let index = [documentIDs[material.id], urlIndex].compactMap { $0 }.min()
            let matching = index.map { sourceDocuments[$0] }
            // Reviewed documents have already produced the authoritative candidate (or exclusion).
            // The visible material is still preserved and its ID is reattached below.
            if reviewIndex(id: material.id, url: material.url, document: matching) != nil { continue }
            if let row = publication(companyID: companyID, material: material, document: matching) { candidates.append(row) }
        }
        var unique: [HiringPublication] = []
        // Quality/identity takes precedence over an unverified copy's recency.
        for row in candidates.sorted(by: preferred) {
            if let index = unique.firstIndex(where: { samePublication($0, row) }) {
                var kept = unique[index]
                let eligible = kept.isConfirmed == row.isConfirmed
                if eligible {
                    kept.publishedAt = kept.publishedAt ?? row.publishedAt
                    if row.lastObservedAt > kept.lastObservedAt {
                        kept.lastObservedAt = row.lastObservedAt
                    }
                    if let observedAt = row.statusObservedAt, observedAt > (kept.statusObservedAt ?? .distantPast) {
                        kept.status = row.status; kept.statusObservedAt = observedAt
                    }
                }
                var observed = Set(kept.observations.map(\.id))
                kept.observations += row.observations.filter { observed.insert($0.id).inserted }
                unique[index] = kept
            } else { unique.append(row) }
        }
        let materialIDs = Set(materials.map(\.id))
        var visibleByURL: [String: String] = [:]
        for item in materials {
            if let raw = item.url, !HistoryIdentity.isSearchPage(raw), let key = HistoryIdentity.canonicalURL(raw), visibleByURL[key] == nil { visibleByURL[key] = item.id }
        }
        for i in unique.indices where !materialIDs.contains(unique[i].materialID) {
            if let key = unique[i].url.flatMap(HistoryIdentity.canonicalURL), let id = visibleByURL[key] { unique[i].materialID = id }
        }
        let ordered = unique.sorted {
            let a = $0.publishedAt ?? .distantPast, b = $1.publishedAt ?? .distantPast
            return a == b ? $0.id < $1.id : a > b
        }
        var confirmed = ordered.filter { $0.isConfirmed && $0.publishedAt.map { $0 >= start && $0 <= end } == true }
        let undated = ordered.filter { $0.isConfirmed && $0.publishedAt == nil }
        let uncertain = ordered.filter { !$0.isConfirmed && ($0.publishedAt == nil || $0.publishedAt.map { $0 >= start && $0 <= end } == true) }
        let outside = ordered.filter { $0.isConfirmed && $0.publishedAt.map { $0 < start || $0 > end } == true }.count
        let roleGroups = Dictionary(grouping: confirmed, by: { normalizedRole($0.title) })
        for index in confirmed.indices {
            let key = normalizedRole(confirmed[index].title)
            confirmed[index].possibleRepeatCount = key.isEmpty ? 0 : max(0, (roleGroups[key]?.count ?? 1) - 1)
        }
        let functionGroups: [String: [HiringPublication]] = Dictionary(grouping: confirmed, by: \.function)
        var functions: [HiringFunctionCount] = []
        for (function, rows) in functionGroups {
            functions.append(HiringFunctionCount(function: function, count: rows.count, publicationIDs: rows.map(\.id)))
        }
        functions.sort { $0.count == $1.count ? $0.function < $1.function : $0.count > $1.count }
        var coverage = selected.flatMap(\.coverage)
        let represented = Set(coverage.map { sourceName($0.source, url: $0.sourceURL) })
        let historyMaterials = selected.flatMap { archive in archive.documents.map { SourceHistoryMaterials.material($0, archiveID: archive.id) } }
        let sourceGroups = Dictionary(grouping: materials + historyMaterials, by: { sourceName($0.providers.first ?? "Сайт", url: $0.url) })
        for source in sourceGroups.keys.sorted() where !represented.contains(source) {
            let items = sourceGroups[source] ?? []
            let uniqueIDs = Set(items.map { $0.url.flatMap(HistoryIdentity.canonicalURL) ?? $0.id })
            coverage.append(.init(id: "saved-materials:\(companyID):\(source)", source: source, status: "partial", receivedCount: uniqueIDs.count,
                note: "Сохранённые материалы; обход всего источника и полнота истории за период не подтверждены."))
        }
        // Identical check IDs can appear in immutable snapshots; show the latest check without losing raw files.
        var checks: [String: SourceHistoryCoverage] = [:]
        for check in coverage.sorted(by: { (HistoryDate.parse($0.checkedAt) ?? .distantPast) < (HistoryDate.parse($1.checkedAt) ?? .distantPast) }) { checks[check.id] = check }
        return Self(companyID: companyID, start: start, end: end, publications: confirmed, undatedPublications: undated,
                    uncertainPublications: uncertain, outsideWindowCount: outside, monthlyCounts: months(start: start, end: end, publications: confirmed),
                    sourceCoverage: checks.values.sorted { $0.source == $1.source ? $0.id < $1.id : $0.source < $1.source }, roleFunctions: functions,
                    notes: selected.flatMap(\.notes) + ["Считаются доступные размещения с известной датой, а не число нанятых или уникальных позиций. Ноль найденных публикаций не доказывает отсутствие найма."])
    }

    private static func publication(companyID: String, material: ResearchMaterial, document: SourceHistoryDocument?) -> HiringPublication? {
        if document?.hiringDisposition == "excluded" { return nil }
        let explicit = document?.hiringDisposition == "reviewedVacancy" || (document?.kind == "vacancy" && !(document?.jobTitle?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true))
        guard let quote = hiringQuote(title: material.title, text: material.text,
                                      explicitRole: explicit ? (document?.jobTitle ?? document?.title) : nil,
                                      preferredQuote: document?.hiringQuote) else { return nil }
        let title = document?.jobTitle?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty ?? roleTitle(quote)
        let source = sourceName(document?.source ?? material.providers.first ?? "Сайт", url: material.url)
        let trusted = document?.hasVerifiedRelationship ?? HistoryIdentity.isTrusted(material)
        let sufficientText = (material.hasFullText && !material.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            || (explicit && document?.quality == "metadata")
        var uncertainty: [String] = []
        if !trusted { uncertainty.append("Связь источника с компанией не подтверждена") }
        if !sufficientText { uncertainty.append("Доступен только фрагмент; содержание объявления не подтверждено") }
        if !material.originalAvailable || !HistoryIdentity.validOriginal(material.url) { uncertainty.append("Не установлена ссылка на исходное объявление") }
        if document?.kind == "comment" { uncertainty.append("Комментарий: связь автора с работодателем отдельно не подтверждена") }
        if document?.employerID == nil && matches(#"(?:партн[её]р[а-яё]*|клиент[а-яё]*|друзья).{0,45}(?:ищет|ищут|ищем|нанима|ваканси)|(?:ваканси[а-яё]*|ищ[а-яё]*).{0,40}(?:наш[а-яё]*\s+партн[её]р|друг[а-яё]*\s+компани)"#, quote) {
            uncertainty.append("В тексте найм отнесён к другой организации")
        }
        let status = document.map { ["active", "archived", "unavailable"].contains($0.status) ? $0.status : "unknown" } ?? "unknown"
        let statusObservedAt = document.flatMap { HistoryDate.parse($0.retrievedAt) }
        let observationStatus = statusObservedAt == material.retrievedAt ? status : "unknown"
        let id = material.url.flatMap { HistoryIdentity.isSearchPage($0) ? nil : HistoryIdentity.canonicalURL($0) }.map { "publication:" + $0 } ?? "publication:" + material.id
        return HiringPublication(id: id, companyID: companyID, title: title, function: document?.jobFunction?.nilIfEmpty ?? roleFunction(title + " " + quote),
            publishedAt: material.publishedAt, source: source, sourceID: document?.sourceID ?? document?.id, url: material.url,
            status: status, statusObservedAt: statusObservedAt, quote: quote, text: material.text, relationship: material.relationship,
            salary: document?.salary, location: document?.location, employment: document?.employment, employerID: document?.employerID,
            contact: document?.contact, materialID: material.id, lastObservedAt: material.retrievedAt, uncertainty: uncertainty,
            observations: [.init(id: "\(material.id):\(material.retrievedAt.timeIntervalSince1970):\(observationStatus)", observedAt: material.retrievedAt, status: observationStatus, text: material.text, url: material.url)])
    }

    private static func preferred(_ lhs: HiringPublication, _ rhs: HiringPublication) -> Bool {
        if lhs.uncertainty.count != rhs.uncertainty.count { return lhs.uncertainty.count < rhs.uncertainty.count }
        if lhs.lastObservedAt != rhs.lastObservedAt { return lhs.lastObservedAt > rhs.lastObservedAt }
        if (lhs.sourceID != nil) != (rhs.sourceID != nil) { return lhs.sourceID != nil }
        return lhs.id < rhs.id
    }
    private static func samePublication(_ a: HiringPublication, _ b: HiringPublication) -> Bool {
        a.id == b.id || HistoryIdentity.sameURL(a.url, b.url)
            || (a.source == b.source && a.sourceID != nil && a.sourceID == b.sourceID)
    }

    private static func hiringQuote(title: String, text: String, explicitRole: String?, preferredQuote: String?) -> String? {
        if let explicitRole, !explicitRole.isEmpty {
            if let quote = preferredQuote?.trimmingCharacters(in: .whitespacesAndNewlines), !quote.isEmpty, text.contains(quote) { return quote }
            if let role = text.range(of: explicitRole, options: .caseInsensitive) {
                let begin = text.index(role.lowerBound, offsetBy: -80, limitedBy: text.startIndex) ?? text.startIndex
                let end = text.index(role.upperBound, offsetBy: 520, limitedBy: text.endIndex) ?? text.endIndex
                return String(text[begin..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            return text.isEmpty ? title : String(text.prefix(700))
        }
        guard matches(#"ищем|ищу|ищет|ищут|требуе|разыскив|приглаша|нанима|набира|нужен|нужна|нужны|ваканси|hiring|looking\s+for"#, title + " " + text) else { return nil }
        // Work from a sentence/line, so a distant "ищем" in an enrollment advert cannot qualify a mentioned teacher.
        let lines = text.components(separatedBy: CharacterSet(charactersIn: "\n.!?"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        let fragments = lines + (title.isEmpty ? [] : [title])
        let role = #"(?:менеджер[а-яё]*|преподавател[а-яё]*|учител[а-яё]*|репетитор[а-яё]*|куратор[а-яё]*|методист[а-яё]*|наставник[а-яё]*|маркетолог[а-яё]*|таргетолог[а-яё]*|продюсер[а-яё]*|дизайнер[а-яё]*|разработчик[а-яё]*|редактор[а-яё]*|копирайтер[а-яё]*|сотрудник[а-яё]*|специалист[а-яё]*|руководител[а-яё]*|директор[а-яё]*|рекрутер[а-яё]*|оператор[а-яё]*|аналитик[а-яё]*|ассистент[а-яё]*|администратор[а-яё]*|эксперт[а-яё]*|стаж[её]р[а-яё]*|HR|SMM|sales|teacher|developer|manager)"#
        let action = #"(?:ищем|ищу|ищет|ищут|требуется|требуются|разыскиваем|приглашаем|нанимаем|набираем|нужен|нужна|нужны|нужен\s+нам|hiring|looking\s+for)"#
        let employment = matches(#"(?:в\s+(?:нашу\s+)?команду|на\s+работу|зарплат|заработн|вознаграждени|гонорар|₽\s*/\s*час|резюме|отклик|обязанност|ваканси|собеседован|соискател|кандидат|оплачиваем[а-яё]*\s+стажиров)"#, (title + " " + text).lowercased())
        for fragment in fragments {
            let lower = fragment.lowercased()
            guard matches(action + "|ваканси|требуе", lower) else { continue }
            guard employment || matches(#"^\s*(?:мы\s+)?(?:ищем|нанимаем|набираем|требуется|требуются)\s+"# + role, lower) else { continue }
            if matches(#"(?:не\s+(?:ищем|нанимаем|требуется|требуются)|вакансий\s+нет|ваканси[а-яё]*\s+(?:(?:пока|сейчас)\s+)?нет|набор\s+(?:ученик[а-яё]*|учащ[а-яё]*|школьник[а-яё]*|студент[а-яё]*))"#, lower) { continue }
            if matches(#"(?:поможем|научим|обучим).{0,50}(?:ваканси|найти\s+работ|стать)|(?:ищем|приглашаем|набираем).{0,25}(?:ученик|учащ|школьник)|(?:ищем|приглашаем|набираем).{0,25}студент[а-яё]*.{0,30}(?:на\s+курс|на\s+обучени)|(?:как\s+найти|обзор|подборка).{0,30}ваканси|^(?:зачем|почему|как\s+стать)\b|для\s+(?:моего\s+)?реб[её]нка"#, lower) { continue }
            let learning = matches(#"(?:на\s+(?:наш[а-яё]*\s+)?(?:курс|обучени|заняти)|ищ[а-яё]*\s+(?:себе\s+)?репетитор|подготов(?:ка|ки|ку)\s+к\s+(?:егэ|огэ)|научим|обучим|стать\s+(?:менеджер|преподавател)|для\s+(?:моего\s+)?реб[её]нка|ищем\s+ученик)"#, lower)
            if learning && !employment { continue }
            if matches("\\b" + action + #"\b.{0,100}\b"# + role + #"\b"#, lower)
                || matches("\\b" + role + #"\b.{0,80}\b"# + #"(?:в\s+команду|требуется|требуются|вакансия)\b"#, lower)
                || matches(#"(?:^[^\p{L}]*ваканси[а-яё]*|(?:открыт[а-яё]*|есть|нов[а-яё]*|горящ[а-яё]*)\s+ваканси[а-яё]*)\b.{0,80}\b"# + role, lower) {
                return fragment
            }
            if matches(#"(?:набираем|приглашаем).{0,30}студент[а-яё]*.{0,30}оплачиваем[а-яё]*\s+стажиров"#, lower) { return fragment }
        }
        return nil
    }

    private static func roleTitle(_ quote: String) -> String {
        let cleaned = quote.replacingOccurrences(of: #"(?i)^.*?\b(?:ищем|ищу|разыскиваем|нанимаем|набираем|приглашаем|требуется|требуются|вакансия|нужен|нужна|нужны)\s*[:—–-]?\s*(?:в\s+(?:нашу\s+)?команду\s*)?"#, with: "", options: .regularExpression)
        return String((cleaned.nilIfEmpty ?? quote).prefix(160))
    }
    private static func roleFunction(_ text: String) -> String {
        let lower = text.lowercased()
        if matches(#"(?:руководител|директор|head).{0,40}(?:продаж|sales)|\bроп\b"#, lower) { return "Руководство продажами" }
        if matches(#"лидоген|лид[а-яё]*.{0,12}генерац|sdr|lead\s*gen"#, lower) { return "Лидогенерация" }
        if matches(#"продаж|sales|закрыти[а-яё]*\s+сдел"#, lower) { return "Продажи" }
        if matches(#"преподавател|учител|репетитор|teacher"#, lower) { return "Преподавание" }
        if matches(#"методист|куратор|наставник"#, lower) { return "Методика и сопровождение" }
        if matches(#"маркет|smm|таргет|контент|редактор|копирайтер|продюсер"#, lower) { return "Маркетинг и контент" }
        if matches(#"рекрутер|\bhr\b|подбор\s+персонала"#, lower) { return "HR и подбор" }
        if matches(#"разработчик|программист|developer|дизайнер"#, lower) { return "Разработка и дизайн" }
        if matches(#"руководител|директор"#, lower) { return "Управление" }
        return "Не определена"
    }
    private static func normalizedRole(_ title: String) -> String {
        title.lowercased().replacingOccurrences(of: #"[^\p{L}\p{N}]+"#, with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces)
    }
    private static let regexCache = NSCache<NSString, NSRegularExpression>()
    private static func matches(_ pattern: String, _ value: String) -> Bool {
        let key = pattern as NSString
        let regex: NSRegularExpression
        if let cached = regexCache.object(forKey: key) { regex = cached }
        else { guard let compiled = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return false }; regexCache.setObject(compiled, forKey: key); regex = compiled }
        return regex.firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) != nil
    }
    private static func sourceName(_ source: String, url: String?) -> String {
        let name = source.lowercased(), host = URL(string: url ?? "")?.host?.lowercased() ?? ""
        if name.contains("telegram") || name.contains("telemetr") || ["t.me", "telegram.me"].contains(host) { return "Telegram" }
        if name == "vk" || name.contains("вконтакте") || host == "vk.com" || host == "vk.ru" { return "VK" }
        if name.contains("superjob") || host.hasSuffix("superjob.ru") { return "SuperJob" }
        if name == "hh" || name.contains("headhunter") || host.hasSuffix("hh.ru") { return "HH" }
        return source
    }
    private static func months(start: Date, end: Date, publications: [HiringPublication]) -> [HiringMonthCount] {
        guard start <= end else { return [] }
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "Europe/Moscow")!
        guard var cursor = calendar.date(from: calendar.dateComponents([.year, .month], from: start)) else { return [] }
        let formatter = DateFormatter(); formatter.calendar = calendar; formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "ru_RU")
        var output: [HiringMonthCount] = []
        while cursor <= end {
            guard let next = calendar.date(byAdding: .month, value: 1, to: cursor), next > cursor else { break }
            formatter.dateFormat = "yyyy-MM"; let key = formatter.string(from: cursor)
            formatter.dateFormat = "LLL yyyy"; let label = formatter.string(from: cursor)
            let rows = publications.filter { $0.publishedAt.map { $0 >= cursor && $0 < next } == true }
            output.append(.init(id: key, label: label, count: rows.count, publicationIDs: rows.map(\.id)))
            cursor = next
        }
        return output
    }
}

private enum HistoryIdentity {
    static func canonicalURL(_ value: String) -> String? {
        guard var url = URLComponents(string: value), let host = url.host?.lowercased(), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { return nil }
        url.scheme = "https"; url.host = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
        if url.host == "telegram.me" { url.host = "t.me" }
        if url.host == "m.vk.com" || url.host == "vk.ru" { url.host = "vk.com" }
        if url.host == "t.me" && url.path.hasPrefix("/s/") { url.path = String(url.path.dropFirst(2)) }
        url.fragment = nil
        url.queryItems = url.queryItems?.filter { !$0.name.lowercased().hasPrefix("utm_") && !["fbclid", "gclid", "yclid", "hhtmfrom", "from", "trk"].contains($0.name.lowercased()) }
            .sorted { $0.name == $1.name ? ($0.value ?? "") < ($1.value ?? "") : $0.name < $1.name }
        if url.queryItems?.isEmpty == true { url.queryItems = nil }
        while url.path.hasSuffix("/") && url.path.count > 1 { url.path.removeLast() }
        return url.string
    }
    static func isSearchPage(_ value: String) -> Bool {
        guard let url = URLComponents(string: value) else { return false }
        return url.host?.contains("telemetr.me") == true && url.path.hasPrefix("/posts")
    }
    static func validOriginal(_ value: String?) -> Bool {
        guard let value else { return false }
        return canonicalURL(value) != nil && !isSearchPage(value)
    }
    static func sameURL(_ lhs: String?, _ rhs: String?) -> Bool {
        guard let lhs, let rhs, !isSearchPage(lhs), !isSearchPage(rhs), let a = canonicalURL(lhs), let b = canonicalURL(rhs) else { return false }
        return a == b
    }
    static func isTrusted(_ material: ResearchMaterial) -> Bool {
        let relationship = material.relationship.lowercased()
        if ["кандидат", "не проверен", "не подтвержден", "требует проверки", "не установлена"].contains(where: relationship.contains) { return false }
        // "Относится к компании · проверено вручную" checks relevance, not the employer/author identity.
        if relationship.contains("относится к компании") { return material.isOfficialDomain }
        return material.isOfficialDomain || ["связь с компанией проверена вручную", "опубликовано школой", "подтверждённому employer id", "связана с официальным"].contains(where: relationship.contains)
    }
}

private extension String {
    var nilIfEmpty: String? { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self }
}
