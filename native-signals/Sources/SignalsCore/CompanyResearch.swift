import Foundation

public enum ResearchMaterialKind: String, CaseIterable, Sendable {
    case all = "Все материалы", hiring = "Вакансии", promotion = "Реклама и акции"
    case publication = "Посты", page = "Сайты и статьи", profile = "Страницы компаний"
}

public struct ResearchMaterial: Identifiable, Sendable {
    public var id: String
    public var title: String
    public var text: String
    public var url: String?
    public var dateLabel: String
    public var publishedAt: Date?
    public var retrievedAt: Date
    public var kind: ResearchMaterialKind
    public var providers: [String]
    public var quality: String
    public var relationship: String
    public var evidence: [Evidence]
    public var originalAvailable: Bool
    public var isOfficialDomain: Bool
    public var hasFullText: Bool = false
    public var author: String? { evidence.compactMap { $0.provenance?.channel }.first }
    public var searchText: String { ([title, text, relationship] + providers + evidence.map(\.excerpt)).joined(separator: " ") }
}

public enum ResearchContactKind: String, Sendable {
    case person, email, phone, telegram, publicLink

    public var label: String {
        switch self {
        case .person: return "Профиль человека"
        case .email: return "Email"
        case .phone: return "Телефон"
        case .telegram: return "Telegram"
        case .publicLink: return "Публичная ссылка"
        }
    }

    public var openActionTitle: String {
        switch self {
        case .person: return "Открыть профиль"
        case .email: return "Открыть почту"
        case .phone: return "Позвонить"
        case .telegram: return "Открыть Telegram"
        case .publicLink: return "Открыть ссылку"
        }
    }
}
public enum ResearchAffiliation: String, Sendable {
    case profileCompany = "Компания указана в профиле"
    case unresolved = "Связь не установлена"
    case otherCompany = "Указана другая компания"
    case extracted = "Найдено в материале"
    case published = "Опубликовано компанией"
}

public struct ResearchContact: Identifiable, Sendable {
    public var id: String
    public var name: String
    public var role: String
    public var address: String
    public var kind: ResearchContactKind
    public var affiliation: ResearchAffiliation
    public var explanation: String
    public var materialIDs: [String]
    public var sourceURLs: [String]
    public var retrievedAt: Date
    public var positions: [LinkedInPosition]
    public var query: String
    public var purpose: ContactPurpose = .unknown
    public var sourceQuote: String = ""
    public var searchText: String { [name, role, address, explanation].joined(separator: " ") }
    public var actionURL: URL? { CompanyResearch.contactActionURL(address, kind: kind) }
    public var copyAddress: String {
        guard [.email, .phone].contains(kind), let separator = address.firstIndex(of: ":") else { return address }
        let scheme = address[..<separator].lowercased()
        return ["mailto", "tel"].contains(scheme) ? String(address[address.index(after: separator)...]).components(separatedBy: "?")[0] : address
    }
}

public struct CompanyResearch: Sendable {
    public init() {}
    public var materials: [ResearchMaterial] = []
    public var contacts: [ResearchContact] = []
    public var peopleSearchPerformed = false
    public var collectionNotes: [String] = []
    public var publicResearch: PublicResearchRecord?
    private var companyWebsite: String?
    public var publishedContacts: [ResearchContact] { contacts.filter { $0.affiliation == .published } }
    public var cooperationContacts: [ResearchContact] { publishedContacts.filter { $0.purpose == .cooperation } }
    public var fullMaterials: [ResearchMaterial] { materials.filter(\.hasFullText) }
    public var fragments: [ResearchMaterial] { materials.filter { !$0.hasFullText } }
    public var linkedPeople: [ResearchContact] { contacts.filter { $0.kind == .person && $0.affiliation == .profileCompany } }
    public var otherPeople: [ResearchContact] { contacts.filter { $0.kind == .person && $0.affiliation != .profileCompany } }
    public var addresses: [ResearchContact] { contacts.filter { $0.kind != .person } }

    /// A read-only projection: collection files and historical user decisions are never rewritten.
    public static func build(company: CompanyDossier, evidence: [Evidence], sourceRuns: [SourceCollectionRun], telegramRuns: [TelemetrCollectionRun], publicResearch: PublicResearchRecord? = nil) -> Self {
        var output = Self()
        output.companyWebsite = company.website
        let archive = publicResearch?.companyID == company.id ? publicResearch : nil
        output.publicResearch = archive
        let runs = sourceRuns.filter { $0.request?.companyID == company.id }.sorted { $0.completedAt > $1.completedAt }
        let selectedCompanyPages = Set(runs.filter { $0.harvest?.operation == .people }.compactMap { $0.harvest?.targetURL.map(canonicalURL) })
        var materialMap: [String: ResearchMaterial] = [:]
        var personMap: [String: ResearchContact] = [:]
        var companyAliases: [String: String] = [:]
        let rejectedEvidenceIDs = Set(telegramRuns.flatMap(\.queries).flatMap(\.posts)
            .filter { $0.companyID == company.id && $0.relevance == .rejected }
            .map { "telemetr-\(company.id)-\($0.id)" })
        // Resolve the requested LinkedIn company slug to the ID that employees' profiles contain.
        for run in runs where run.harvest?.operation == .companies {
            for item in elements(run) {
                if let url = item["linkedinUrl"] as? String, let id = scalar(item["id"]) {
                    companyAliases[canonicalURL(url)] = id
                }
            }
        }
        func add(_ item: ResearchMaterial) {
            if let old = materialMap[item.id] { var merged = merge(old, item); merged.id = old.id; materialMap[item.id] = merged }
            else if let url = item.url, !url.contains("telemetr.me/posts"), let key = materialMap.first(where: { $0.value.url.map(canonicalURL) == canonicalURL(url) })?.key, let old = materialMap[key] {
                var merged = merge(old, item); merged.id = key; materialMap[key] = merged
            }
            else { materialMap[item.id] = item }
        }
        for run in runs {
            if run.harvest?.operation == .people { output.peopleSearchPerformed = true }
            let target = run.harvest?.targetURL.map(canonicalURL)
            let targetID = target.flatMap { companyAliases[$0] } ?? target.flatMap(numericCompanyID)
            if run.hasMore { output.collectionNotes.append("\(run.provider.displayName): в сохранённом поиске есть не загруженные страницы.") }
            for result in run.results {
                // Broad company-name matches remain available in service history, not as company facts.
                if result.contentType == .linkedInCompany && !selectedCompanyPages.contains(canonicalURL(result.url)) { continue }
                if result.contentType == .linkedInPerson {
                    let positions = result.linkedIn?.positions ?? []
                    let matched = positions.filter { position in
                        guard let url = position.companyURL else { return false }
                        let normalized = canonicalURL(url)
                        return normalized == target || (targetID != nil && numericCompanyID(normalized) == targetID)
                    }
                    let allHaveDifferentIDs = !positions.isEmpty && targetID != nil && positions.allSatisfy {
                        guard let url = $0.companyURL, let id = numericCompanyID(url) else { return false }
                        return id != targetID
                    }
                    let affiliation: ResearchAffiliation = !matched.isEmpty ? .profileCompany : allHaveDifferentIDs ? .otherCompany : .unresolved
                    let identity = canonicalURL(result.url)
                    // Prefer the newest observation; never join different people based on their names.
                    if personMap[identity] == nil {
                        personMap[identity] = ResearchContact(id: "person:" + identity, name: result.title,
                            role: (matched.isEmpty ? positions : matched).map { [$0.title, $0.companyName].filter { !$0.isEmpty }.joined(separator: " · ") }.joined(separator: "\n"),
                            address: result.url, kind: .person, affiliation: affiliation,
                            explanation: !matched.isEmpty ? "В профиле указана страница компании, по которой выполнялся поиск. Текущее место работы отдельно не проверено. Почта и телефон этим запросом не получены." : "Поисковое совпадение. Не считать сотрудником выбранной компании без проверки профиля и места работы.",
                            materialIDs: [], sourceURLs: [result.url], retrievedAt: result.retrievedAt, positions: positions, query: result.query)
                    }
                    continue
                }
                let origin = result.evidence(collectionID: run.id)
                let isPost = result.contentType == .linkedInPost
                let date = result.linkedIn?.publishedAt.flatMap(parseDate)
                let official = officialDomain(result.url, company.website)
                let quality = result.contentType == .searchSnippet ? "Фрагмент поисковой выдачи. Полный текст страницы здесь не загружен." : isPost ? (result.linkedIn?.isRepost == true ? "Репост LinkedIn; автор репоста может не быть автором текста." : "Текст публикации из LinkedIn.") : "Метаданные страницы из поисковой выдачи; принадлежность требует проверки."
                add(ResearchMaterial(id: "url:" + canonicalURL(result.url), title: result.title, text: result.snippet,
                    url: result.url, dateLabel: isPost ? result.linkedIn?.publishedAt ?? "Дата публикации неизвестна" : "Дата публикации неизвестна",
                    publishedAt: date, retrievedAt: result.retrievedAt,
                    kind: classify(result.title + " " + result.snippet, fallback: isPost ? .publication : result.contentType == .searchSnippet ? .page : .profile),
                    providers: [providerName(result.provider)], quality: quality,
                    relationship: official ? "Домен компании" : isPost ? "Публикация найденного профиля · связь автора с компанией требует проверки" : "Найдено по запросу о компании",
                    evidence: [origin], originalAvailable: true, isOfficialDomain: official, hasFullText: isPost))
            }
        }
        // A Telegram search-page URL is shared by many posts and must never be a deduplication key.
        for run in telegramRuns.sorted(by: { $0.startedAt > $1.startedAt }) {
            for query in run.queries where query.query.companyID == company.id {
                if let error = query.errorMessage { output.collectionNotes.append("Telegram: " + error) }
                for post in query.posts where post.companyID == company.id && post.relevance != .rejected {
                    let provider = post.origin == .telemetrWebsite ? "Telegram · Telemetr" : "Telegram · API"
                    let quality = post.sourceDeleted == true ? "Архив удалённой публикации." : post.textFromCard == true ? "Текст раскрытой карточки; без медиа и комментариев." : "Фрагмент выдачи Telemetr; может быть сокращён."
                    let origin = Evidence(id: "telemetr-\(company.id)-\(post.id)", title: "Telegram · \(post.channelTitle?.isEmpty == false ? post.channelTitle! : company.name)",
                        excerpt: post.text, url: post.sourceURL ?? post.sourcePageURL,
                        dateLabel: post.sourceDateLabel ?? post.publishedAt.map { ISO8601DateFormatter().string(from: $0) } ?? "Дата публикации неизвестна",
                        kind: "Публикация Telegram", isDemo: false,
                        provenance: .init(provider: provider, query: post.query, retrievedAt: ISO8601DateFormatter().string(from: post.retrievedAt), channel: post.channelTitle, collectionID: run.id, contentType: quality))
                    let title = post.textFromCard == true ? readableTitle(post.text) : post.channelTitle?.isEmpty == false ? post.channelTitle! : "Telegram · название канала не сохранено"
                    add(ResearchMaterial(id: "telegram:" + post.id, title: title, text: post.text,
                        url: post.sourceURL ?? post.sourcePageURL, dateLabel: origin.dateLabel,
                        publishedAt: post.publishedAt ?? post.sourceDateLabel.flatMap(parseDate), retrievedAt: post.retrievedAt,
                        kind: classify(post.text, fallback: .publication), providers: [provider], quality: quality,
                        relationship: post.relevance == .confirmed ? "Относится к компании · проверено вручную" : "Упоминание в поиске · связь не проверена",
                        evidence: [origin], originalAvailable: post.sourceURL != nil, isOfficialDomain: false, hasFullText: post.textFromCard == true))
                }
            }
        }
        // Existing saved evidence enriches the same material, rather than duplicating its search result.
        for item in evidence where !item.isDemo && !rejectedEvidenceIDs.contains(item.id) {
            if let key = materialMap.first(where: { $0.value.evidence.contains { $0.id == item.id } })?.key {
                if materialMap[key]?.evidence.contains(where: { $0.id == item.id }) == false { materialMap[key]?.evidence.append(item) }
                continue
            }
            let url = item.url
            let official = url.map { officialDomain($0, company.website) } ?? false
            let key = url.map { "url:" + canonicalURL($0) } ?? "evidence:" + item.id
            let provenance = item.provenance
            add(ResearchMaterial(id: key, title: item.title, text: item.excerpt, url: url,
                dateLabel: item.dateLabel, publishedAt: nil, retrievedAt: provenance.flatMap { parseDate($0.retrievedAt) } ?? .distantPast,
                kind: classify(item.title + " " + item.excerpt, fallback: .page), providers: [provenance?.provider ?? item.kind],
                quality: provenance?.contentType ?? "Материал, сохранённый вручную.", relationship: official ? "Домен компании" : "Сохранено в карточке компании",
                evidence: [item], originalAvailable: true, isOfficialDomain: official))
        }
        for doc in archive?.documents ?? [] where doc.companyID == company.id && !doc.text.isEmpty {
            let isPost = doc.kind == "telegramPost"
            let quality = isPost ? "Текст открытой публикации сохранён на этом Mac. Медиа и комментарии не загружены." : doc.kind == "telegramProfile" ? "Описание открытой страницы Telegram." : "Текст страницы сохранён на этом Mac."
            let evidence = Evidence(id: doc.id, title: doc.title, excerpt: doc.text, url: doc.url, dateLabel: doc.publishedAt ?? "Дата публикации не указана", kind: isPost ? "Публикация Telegram" : "Открытая страница", isDemo: false,
                provenance: .init(provider: doc.provider, query: "Сайт и публичные страницы компании", retrievedAt: doc.retrievedAt, channel: doc.kind.hasPrefix("telegram") ? doc.title : nil, collectionID: "public-research-" + company.id, contentType: quality))
            add(ResearchMaterial(id: "url:" + canonicalURL(doc.url), title: isPost ? readableTitle(doc.text) : doc.title, text: doc.text, url: doc.url,
                dateLabel: evidence.dateLabel, publishedAt: doc.publishedAt.flatMap(parseDate), retrievedAt: parseDate(doc.retrievedAt) ?? .distantPast,
                kind: classify(isPost ? doc.text : doc.title, fallback: isPost ? .publication : doc.kind == "telegramProfile" ? .profile : .page), providers: [doc.provider], quality: quality,
                relationship: doc.official ? "Опубликовано компанией · " + doc.title : "Открытая публикация · авторство указано в источнике",
                evidence: [evidence], originalAvailable: true, isOfficialDomain: doc.official, hasFullText: doc.quality == "fullText"))
        }
        output.materials = materialMap.values.sorted {
            if $0.hasFullText != $1.hasFullText { return $0.hasFullText }
            if ($0.publishedAt != nil) != ($1.publishedAt != nil) { return $0.publishedAt != nil }
            let a = $0.publishedAt ?? $0.retrievedAt, b = $1.publishedAt ?? $1.retrievedAt
            return a == b ? $0.id < $1.id : a > b
        }
        for key in personMap.keys {
            guard var person = personMap[key] else { continue }
            person.materialIDs = output.materials.filter { material in
                material.evidence.contains { evidence in
                    runs.contains { run in run.harvest?.operation == .posts && run.harvest?.targetURL.map(canonicalURL) == key && run.results.contains { $0.id == evidence.id } }
                }
            }.map(\.id)
            personMap[key] = person
        }
        output.contacts = Array(personMap.values) + extractAddresses(output.materials)
        if let archive {
            var published: [String: ResearchContact] = [:]
            func insert(address: String, name: String, purpose: ContactPurpose, quote: String, source: String, retrieved: String) {
                let key = contactAddressKey(address)
                let materialIDs = output.materials.filter { $0.url.map(canonicalURL) == canonicalURL(source) || $0.text.contains(address) }.map(\.id)
                // Keep historical IDs used by saved boards. Address normalization is only a
                // comparison key; changing it must not rewrite published contact references.
                let item = ResearchContact(id: "published:" + canonicalURL(address).lowercased(), name: name.isEmpty ? address : name, role: purpose.label, address: address,
                    kind: contactKind(address), affiliation: .published,
                    explanation: "Контакт опубликован в источнике компании. Назначение приведено по подписи; личность владельца отдельно не проверена.", materialIDs: materialIDs, sourceURLs: [source], retrievedAt: parseDate(retrieved) ?? .distantPast, positions: [], query: "Сайт и публичные страницы компании", purpose: purpose, sourceQuote: quote)
                if let old = published[key] {
                    var merged = old
                    if purpose == .cooperation || (old.purpose == .channel && purpose != .channel) { merged = item }
                    merged.id = old.id
                    merged.materialIDs = Array(Set(old.materialIDs + item.materialIDs)).sorted()
                    merged.sourceURLs = Array(Set(old.sourceURLs + item.sourceURLs)).sorted()
                    published[key] = merged
                } else { published[key] = item }
            }
            for channel in archive.channels where channel.verified {
                let purpose: ContactPurpose = channel.accountType == "channel" ? .channel : channel.accountType == "chat" ? .chat : channel.accountType == "bot" ? .bot : ContactPurpose(rawValue: channel.purpose) ?? .unknown
                let hasProfileQuote = channel.bio?.isEmpty == false && channel.profileSourceURL?.isEmpty == false
                insert(address: channel.url, name: channel.title ?? channel.url, purpose: purpose,
                    quote: hasProfileQuote ? channel.bio! : channel.context,
                    source: hasProfileQuote ? channel.profileSourceURL! : channel.sourceURL,
                    retrieved: channel.retrievedAt ?? archive.searchedAt)
            }
            for contact in archive.contacts where ["officialWebsite", "domainInBio", "reviewedIdentity", "officialChannelPost"].contains(contact.basis) {
                insert(address: contact.url, name: contact.label, purpose: ContactPurpose(rawValue: contact.purpose) ?? .unknown, quote: contact.context, source: contact.sourceURL, retrieved: archive.searchedAt)
            }
            let excluded = Set(archive.channels.filter { $0.basis == "excludedReference" }.map { contactAddressKey($0.url) })
            for contact in output.contacts where contact.kind != .person {
                let key = contactAddressKey(contact.address)
                if var item = published[key] {
                    item.materialIDs = Array(Set(item.materialIDs + contact.materialIDs)).sorted()
                    item.sourceURLs += contact.sourceURLs.filter { !item.sourceURLs.contains($0) }
                    published[key] = item
                }
            }
            output.contacts.removeAll { $0.kind != .person && (published[contactAddressKey($0.address)] != nil || excluded.contains(contactAddressKey($0.address))) }
            output.contacts += published.values
            output.collectionNotes += archive.notes ?? []
        }
        output.contacts.sort { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        output.collectionNotes = Array(Set(output.collectionNotes)).sorted()
        return output
    }

    /// Finalize the same company-scoped projection after adding history. Revisit only new
    /// or enriched materials; a regex match never establishes ownership or a person's role.
    public mutating func mergeSourceHistory(companyID: String, archives: [SourceHistoryArchive]) {
        let previous = Dictionary(materials.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let merged = SourceHistoryMaterials.merge(companyID: companyID, materials: materials, archives: archives)
        let changed = merged.filter { item in
            guard let old = previous[item.id] else { return true }
            return old.text != item.text || old.url != item.url || old.kind != item.kind
                || old.evidence.map { [$0.id, $0.excerpt, $0.url ?? ""] } != item.evidence.map { [$0.id, $0.excerpt, $0.url ?? ""] }
        }
        materials = merged
        let excluded = Set((publicResearch?.channels ?? []).filter { $0.basis == "excludedReference" }.map { Self.contactAddressKey($0.url) })
        var index: [String: Int] = [:]
        func priority(_ contact: ResearchContact) -> Int { contact.kind == .person ? 3 : contact.affiliation == .published ? 2 : 1 }
        for (offset, contact) in contacts.enumerated() {
            let key = Self.contactAddressKey(contact.address)
            if index[key].map({ priority(contacts[$0]) < priority(contact) }) ?? true { index[key] = offset }
        }
        for candidate in Self.extractAddresses(changed, includeProfiles: true, businessContextOnly: true, companyWebsite: companyWebsite) {
            let key = Self.contactAddressKey(candidate.address)
            guard !excluded.contains(key) else { continue }
            if let offset = index[key] {
                // Preserve IDs, verified purpose, names and affiliations of existing contacts.
                contacts[offset].materialIDs = Array(Set(contacts[offset].materialIDs + candidate.materialIDs)).sorted()
                let oldURLs = Set(contacts[offset].sourceURLs)
                contacts[offset].sourceURLs += candidate.sourceURLs.filter { !oldURLs.contains($0) }
                if contacts[offset].affiliation == .extracted && contacts[offset].sourceQuote.isEmpty {
                    contacts[offset].sourceQuote = candidate.sourceQuote
                }
            } else {
                index[key] = contacts.count
                contacts.append(candidate)
            }
        }
        contacts.sort { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    public static func canonicalURL(_ raw: String) -> String {
        guard var url = URLComponents(string: raw) else { return raw }
        url.scheme = "https"
        let host = url.host?.lowercased() ?? ""
        url.host = (host == "linkedin.com" || host.hasSuffix(".linkedin.com")) ? "www.linkedin.com" : host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
        url.fragment = nil
        url.queryItems = url.queryItems?.filter { !$0.name.lowercased().hasPrefix("utm_") && !["trk", "from", "hhtmfrom", "yclid", "gclid"].contains($0.name.lowercased()) }.sorted { $0.name < $1.name }
        if url.queryItems?.isEmpty == true { url.query = nil }
        while url.path.hasSuffix("/") { url.path.removeLast() }
        return url.string ?? raw
    }

    /// A published URL is not evidence of a person's identity or their employment.
    public static func contactKind(_ raw: String) -> ResearchContactKind {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalizedEmail(value) != nil { return .email }
        if normalizedPhone(value) != nil { return .phone }
        if let host = URL(string: value)?.host?.lowercased(), ["t.me", "www.t.me", "telegram.me", "www.telegram.me"].contains(host) { return .telegram }
        return .publicLink
    }

    /// Schemes and formatting do not create additional contacts. This key is deliberately
    /// separate from canonicalURL, whose historic output is embedded in saved reference IDs.
    public static func contactAddressKey(_ raw: String) -> String {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let email = normalizedEmail(value) { return "email:" + email }
        if let phone = normalizedPhone(value) { return "phone:" + phone }
        return "link:" + canonicalURL(value).lowercased()
    }

    public static func contactActionURL(_ raw: String, kind: ResearchContactKind) -> URL? {
        switch kind {
        case .email:
            guard let email = normalizedEmail(raw) else { return nil }
            return URL(string: "mailto:" + email)
        case .phone:
            guard let phone = normalizedPhone(raw) else { return nil }
            return URL(string: "tel:" + phone)
        case .person, .telegram, .publicLink:
            guard let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
                  ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host?.isEmpty == false else { return nil }
            return url
        }
    }

    private static func normalizedEmail(_ raw: String) -> String? {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.lowercased().hasPrefix("mailto:") { value = String(value.dropFirst(7)).components(separatedBy: "?")[0] }
        value = value.removingPercentEncoding ?? value
        guard value.range(of: #"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"#, options: .regularExpression) != nil else { return nil }
        return value.lowercased()
    }

    private static func normalizedPhone(_ raw: String) -> String? {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.lowercased().hasPrefix("tel:") { value = String(value.dropFirst(4)) }
        value = value.removingPercentEncoding ?? value
        let parts = value.components(separatedBy: ";")
        guard parts.count <= 2, let number = parts.first,
              number.range(of: #"^\+?[0-9\s().\-]+$"#, options: .regularExpression) != nil else { return nil }
        var digits = number.filter(\.isNumber)
        guard (7...15).contains(digits.count) else { return nil }
        if !number.hasPrefix("+") && digits.count == 11 && digits.hasPrefix("8") { digits = "7" + digits.dropFirst() }
        var key = number.hasPrefix("+") || (digits.count == 11 && digits.hasPrefix("7")) ? "+" + digits : digits
        if parts.count == 2 {
            guard parts[1].range(of: #"^ext=[0-9]+$"#, options: .regularExpression) != nil else { return nil }
            key += ";" + parts[1]
        }
        return key
    }

    private static func elements(_ run: SourceCollectionRun) -> [[String: Any]] {
        guard let data = run.rawResponse.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [] }
        return object["elements"] as? [[String: Any]] ?? []
    }
    private static func scalar(_ value: Any?) -> String? { (value as? String) ?? (value as? NSNumber)?.stringValue }
    private static func numericCompanyID(_ url: String) -> String? {
        guard let value = URL(string: url)?.path.split(separator: "/").last.map(String.init), !value.isEmpty, value.allSatisfy(\.isNumber) else { return nil }
        return value
    }
    private static func providerName(_ provider: SourceProvider) -> String {
        switch provider { case .serper: return "Google"; case .yandex: return "Яндекс"; case .harvest: return "LinkedIn"; case .targetHunter: return "VK" }
    }
    private static func officialDomain(_ url: String, _ website: String?) -> Bool {
        guard let website, let host = URL(string: canonicalURL(url))?.host, let companyHost = URL(string: canonicalURL(website))?.host else { return false }
        return host == companyHost || host.hasSuffix("." + companyHost)
    }
    private static func parseDate(_ value: String) -> Date? {
        let iso = ISO8601DateFormatter()
        if let date = iso.date(from: value) { return date }
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) { return date }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.timeZone = TimeZone(secondsFromGMT: 0)
        for format in ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd"] { formatter.dateFormat = format; if let date = formatter.date(from: value) { return date } }
        return nil
    }
    private static func classify(_ text: String, fallback: ResearchMaterialKind) -> ResearchMaterialKind {
        let lower = text.lowercased()
        if ["ваканси", "нам нужен", "ищем в команду", "hiring", "hh.ru/vacancy"].contains(where: lower.contains) { return .hiring }
        if ["реклама", "промокод", "по моей ссылке", "получите скидку", "скидка", "скидку"].contains(where: lower.contains) { return .promotion }
        return fallback
    }
    private static func readableTitle(_ text: String) -> String {
        let lines = text.split(separator: "\n").map { String($0).trimmingCharacters(in: .whitespaces) }
        return String((lines.first { $0.filter(\.isLetter).count >= 5 } ?? lines.first ?? "Публикация").prefix(110))
    }
    private static func merge(_ old: ResearchMaterial, _ new: ResearchMaterial) -> ResearchMaterial {
        func rank(_ value: ResearchMaterial) -> Int {
            if value.hasFullText { return 4 }
            if value.quality.contains("раскрытой") { return 3 }
            if value.isOfficialDomain && value.quality.contains("прочитана страница") { return 2 }
            return value.quality.contains("Фрагмент") ? 0 : 1
        }
        var value = rank(new) > rank(old) || (rank(new) == rank(old) && new.text.count > old.text.count) ? new : old
        value.providers = Array(Set(old.providers + new.providers)).sorted()
        var ids = Set<String>()
        value.evidence = (old.evidence + new.evidence).filter { ids.insert($0.id).inserted }
        value.retrievedAt = max(old.retrievedAt, new.retrievedAt)
        value.publishedAt = old.publishedAt ?? new.publishedAt
        if old.relationship.contains("вручную") { value.relationship = old.relationship }
        if new.relationship.contains("вручную") { value.relationship = new.relationship }
        return value
    }
    private static func extractAddresses(_ materials: [ResearchMaterial], includeProfiles: Bool = false, businessContextOnly: Bool = false, companyWebsite: String? = nil) -> [ResearchContact] {
        let patterns: [(ResearchContactKind, String)] = [(.email, #"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"#), (.phone, #"(?<!\d)(?:\+7|8(?=[\s(]))[\s(]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"#), (.telegram, #"https?://(?:t\.me|telegram\.me)/[A-Za-z][A-Za-z0-9_]{3,31}(?![A-Za-z0-9_])"#)]
        var contacts: [String: ResearchContact] = [:]
        for material in materials where includeProfiles || material.kind != .profile {
            for (kind, pattern) in patterns {
                guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
                // Keep each captured fragment separate so the quote is an exact excerpt,
                // and its URL is not borrowed from a different merged observation.
                var fragments = [(material.text, material.url)] + material.evidence.map { ($0.excerpt, $0.url ?? material.url) }
                if kind == .telegram, let url = material.url { fragments.append((url, url)) }
                for (text, sourceURL) in fragments {
                    for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                        guard let range = Range(match.range, in: text) else { continue }
                        let address = String(text[range])
                        let start = text.index(range.lowerBound, offsetBy: -100, limitedBy: text.startIndex) ?? text.startIndex
                        let end = text.index(range.upperBound, offsetBy: 100, limitedBy: text.endIndex) ?? text.endIndex
                        let quote = String(text[start..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !businessContextOnly || isBusinessContactCandidate(address: address, kind: kind, quote: quote, material: material, companyWebsite: companyWebsite) else { continue }
                        let normalized = kind == .phone ? "+7" + address.filter(\.isNumber).dropFirst() : address.lowercased()
                        let legacyID = kind.rawValue + ":" + normalized
                        let key = contactAddressKey(address)
                        if contacts[key] != nil {
                            if contacts[key]?.materialIDs.contains(material.id) == false { contacts[key]?.materialIDs.append(material.id) }
                            if let sourceURL, contacts[key]?.sourceURLs.contains(sourceURL) == false { contacts[key]?.sourceURLs.append(sourceURL) }
                        } else {
                            contacts[key] = ResearchContact(id: legacyID, name: address, role: kind == .email ? "Email из текста материала" : kind == .phone ? "Телефон из текста материала" : "Telegram-ссылка из материала", address: address,
                                kind: kind, affiliation: .extracted, explanation: "Адрес найден в сохранённом материале. Владелец и назначение проверяются по источнику; это не установленный личный контакт сотрудника.",
                                materialIDs: [material.id], sourceURLs: sourceURL.map { [$0] } ?? [], retrievedAt: material.retrievedAt, positions: [], query: material.evidence.first?.provenance?.query ?? "", sourceQuote: quote)
                        }
                    }
                }
            }
        }
        return Array(contacts.values)
    }

    private static func isBusinessContactCandidate(address: String, kind: ResearchContactKind, quote: String, material: ResearchMaterial, companyWebsite: String?) -> Bool {
        let context = (material.title + " " + quote.replacingOccurrences(of: address, with: "")).lowercased()
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        if kind == .email {
            let parts = address.lowercased().split(separator: "@")
            if let domain = parts.last.map(String.init), ["example.com", "example.org", "example.net", "localhost"].contains(where: { domain == $0 || domain.hasSuffix("." + $0) })
                || domain.hasSuffix(".example") || domain.hasSuffix(".test") { return false }
            if let mailbox = parts.first.map(String.init), ["example", "sample", "test", "dummy", "foo", "bar"].contains(mailbox) { return false }
            if let domain = parts.last.map(String.init) {
                let host = companyWebsite.flatMap { URL(string: $0)?.host?.lowercased() }.map { $0.hasPrefix("www.") ? String($0.dropFirst(4)) : $0 }
                let companyDomain = host.map { domain == $0 || domain.hasSuffix("." + $0) } ?? false
                let mailboxProviders = ["gmail.com", "googlemail.com", "yandex.ru", "yandex.com", "ya.ru", "mail.ru", "bk.ru", "list.ru", "inbox.ru", "rambler.ru", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "protonmail.com"]
                if !companyDomain && !mailboxProviders.contains(domain) {
                    let ownRouting = ["пишите нам", "напишите нам", "связаться с нами", "для связи с нами", "наша почта", "наши контакты", "наша электронная почта", "contact us", "email us"].contains(where: context.contains)
                    let officialSource = material.isOfficialDomain || material.relationship.contains("Связь с компанией проверена вручную")
                    guard ownRouting && officialSource else { return false }
                }
            }
        }
        let examples = ["учебный пример", "пример кода", "пример адреса", "для примера", "вымышлен", "тестовый адрес", "учебное задание", "домашнее задание", "пример студента", "почта студента", "адрес студента", "контакты студентов", "почта ученика", "контакты учеников", "student example", "student exercise", "student contact", "student email", "sample email", "example email", "test email", "dummy contact"]
        guard !examples.contains(where: context.contains) else { return false }
        let business = ["контакт", "связ", "пишите", "напишите", "почт", "email", "телефон", "звоните", "вопрос", "сотруднич", "партнёр", "партнер", "реклам", "отклик", "резюме", "ваканси", "ищем", "сотрудник", "отдел", "директор", "менеджер", "приёмн", "приемн", "поддержк", "запис", "заявк", "contact", "recruit", "hiring", "career", "apply", "inquiries", "partnership", "support"]
        let pattern = #"(?<![\p{L}\p{N}_])(?:"# + business.joined(separator: "|") + ")"
        return context.range(of: pattern, options: .regularExpression) != nil
    }
}
