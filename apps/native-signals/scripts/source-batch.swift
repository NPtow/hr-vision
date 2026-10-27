import Foundation
import Security
import SignalsCore
import Darwin

// A dry run reads the catalog/history only. Network, Keychain, and writes require --execute.
private struct Options {
    var provider: SourceProvider = .serper
    var scope = "pilot"
    var execute = false
    var maxRequests = 409
    var requestCap = 409
    var delayMilliseconds: UInt64 = 500
    var companyIDsURL: URL?
    var batchID: String?
    var phase = "primary"
    var receiptURL: URL?
    var dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("TG Signals", isDirectory: true)

    init(_ arguments: [String]) throws {
        var index = 0
        var explicitCap = false
        func value() throws -> String {
            index += 1
            guard index < arguments.count else { throw BatchError.usage }
            return arguments[index]
        }
        func path(_ text: String) throws -> URL {
            guard text.hasPrefix("/") else { throw BatchError.usage }
            return URL(fileURLWithPath: text)
        }
        while index < arguments.count {
            switch arguments[index] {
            case "--execute": execute = true
            case "--provider":
                guard let candidate = SourceProvider(rawValue: try value()), [.serper, .yandex].contains(candidate) else { throw BatchError.usage }
                provider = candidate
            case "--scope": scope = try value()
            case "--max-requests":
                guard let parsed = Int(try value()), (1...409).contains(parsed) else { throw BatchError.usage }
                maxRequests = parsed
            case "--request-cap":
                guard let parsed = Int(try value()), (1...409).contains(parsed) else { throw BatchError.usage }
                requestCap = parsed; explicitCap = true
            case "--delay-ms":
                guard let parsed = UInt64(try value()), (250...60_000).contains(parsed) else { throw BatchError.usage }
                delayMilliseconds = parsed
            case "--data-dir": dataDirectory = try path(value())
            case "--company-ids": companyIDsURL = try path(value())
            case "--batch-id":
                let parsed = try value()
                guard parsed.range(of: #"^[a-z0-9][a-z0-9_-]{0,63}$"#, options: .regularExpression) != nil else { throw BatchError.usage }
                batchID = parsed
            case "--phase": phase = try value()
            case "--receipt": receiptURL = try path(value())
            default: throw BatchError.usage
            }
            index += 1
        }
        guard ["pilot", "all"].contains(scope), ["primary", "disambiguation"].contains(phase),
              (companyIDsURL == nil) == (batchID == nil),
              batchID == nil || (explicitCap && scope == "pilot"),
              phase == "primary" || batchID != nil,
              !explicitCap || batchID != nil else { throw BatchError.usage }
    }
}

private enum BatchError: LocalizedError {
    case usage, catalog, selection, journal, alreadyRunning, keychain(OSStatus), missingCredential, storage
    var errorDescription: String? {
        switch self {
        case .usage:
            return "Usage: source-batch [--provider serper|yandex] [--scope pilot|all] [--max-requests 1...409] [--delay-ms 250...60000] [--data-dir /path] [--company-ids /ids.json --batch-id name --request-cap 1...409 [--phase primary|disambiguation]] [--receipt /path.json] [--execute]. Default: read-only preview, no Keychain/network/writes. Disambiguation: at most 20 site queries, only after a primary result without the official host."
        case .catalog: return "Каталог должен содержать уникальные реальные компании и все три пилотные школы."
        case .selection: return "Список ID должен содержать 1...409 уникальных ID реальных компаний из текущего каталога."
        case .journal: return "Журнал прохода повреждён или несовместим с выбранными ID/лимитом. Он сохранён без изменений; запросы не запущены."
        case .alreadyRunning: return "Другой web batch-проход уже использует эту папку."
        case .keychain(let status): return "Связка ключей недоступна (код \(status)); запросы не запущены."
        case .missingCredential: return "Сохранённые реквизиты сервиса отсутствуют. Для Яндекса нужен также ID каталога."
        case .storage: return "Не удалось безопасно сохранить состояние. Проход остановлен; незавершённая попытка не будет автоматически повторена."
        }
    }
}

private struct Credential: Decodable { let apiKey: String; let folderID: String }
private func loadCredential(_ provider: SourceProvider) throws -> Credential {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ru.hrvision.tgsignals.sources", kSecAttrAccount as String: provider.rawValue,
        kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { throw BatchError.missingCredential }
    guard status == errSecSuccess, let data = result as? Data else { throw BatchError.keychain(status) }
    guard let credential = try? JSONDecoder().decode(Credential.self, from: data),
          !credential.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          provider != .yandex || !credential.folderID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { throw BatchError.missingCredential }
    return credential
}

private enum AttemptState: String, Codable { case started, saved, failed }
private struct Attempt: Codable {
    let companyID: String
    let companyName: String
    let query: String
    let startedAt: Date
    var phase: String?
    var completedAt: Date?
    var state: AttemptState
    var runID: String?
    var resultCount: Int?
    var errorCode: String?
}
private struct Journal: Codable {
    var version = 1
    let provider: SourceProvider
    let queryVersion: String
    let createdAt: Date
    var updatedAt: Date
    var attempts: [Attempt]
    var batchID: String?
    var selectedCompanyIDs: [String]?
    var requestCap: Int?
}
private let pilotIDs = ["brunoyam", "karpov", "setters"]
private func queryVersion(_ options: Options) -> String {
    options.batchID == nil ? (options.provider == .serper ? "brand-v2" : "quoted-brand-v1") : "company-context-v1"
}
private func query(_ company: CompanyDossier, options: Options, phase: String = "primary") -> String {
    if phase == "disambiguation", let host = officialHost(company) { return "site:\(host)" }
    if options.batchID != nil {
        // Catalog labels may contain explanatory aliases, dashes and parentheses. Do not exact-quote
        // a full editorial label that no indexed page uses. Short brands receive the cohort context.
        var text = company.name.components(separatedBy: " / ").first ?? company.name
        text = text.replacingOccurrences(of: #"\s*\([^)]*\)"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: " — ", with: " ").replacingOccurrences(of: " · ", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if text.split(whereSeparator: { $0.isWhitespace }).count <= 2 {
            text += company.subtitle.contains("Школьное") ? " ЕГЭ ОГЭ" : " бизнес школа"
        }
        return text
    }
    if options.provider == .serper {
        if company.id == "karpov" { return "karpov courses" }
        if company.name.range(of: #"[\p{L}\p{N}][\p{L}\p{N}-]*\.[\p{L}\p{N}-]{2,}"#, options: .regularExpression) != nil { return company.name }
    }
    return "\"\(company.name)\""
}
private func officialHost(_ company: CompanyDossier) -> String? {
    guard let website = company.website, let host = URL(string: website)?.host?.lowercased(), !host.isEmpty else { return nil }
    return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
}
private func isOfficialResult(_ result: SourceSearchResult, company: CompanyDossier) -> Bool {
    guard let official = officialHost(company), let host = URL(string: result.url)?.host?.lowercased() else { return false }
    return host == official || host == "www." + official || host.hasSuffix("." + official)
}
private func requestKey(companyID: String, query: String) -> String { companyID + "\u{0}" + query }
private func journalURL(_ options: Options) -> URL {
    let name = options.batchID.map { "\(options.provider.rawValue)-\($0)" } ?? "\(options.provider.rawValue)-\(queryVersion(options))"
    return options.dataDirectory.appendingPathComponent("source-batch-journals", isDirectory: true).appendingPathComponent(name + ".json")
}
private func loadJournal(_ options: Options, all: [CompanyDossier], selected: [CompanyDossier]) throws -> Journal {
    let url = journalURL(options)
    guard FileManager.default.fileExists(atPath: url.path) else {
        return Journal(version: options.batchID == nil ? 1 : 2, provider: options.provider, queryVersion: queryVersion(options),
                       createdAt: Date(), updatedAt: Date(), attempts: [], batchID: options.batchID,
                       selectedCompanyIDs: options.batchID == nil ? nil : selected.map(\.id),
                       requestCap: options.batchID == nil ? nil : options.requestCap)
    }
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    guard let data = try? Data(contentsOf: url), data.count <= 2_000_000,
          let journal = try? decoder.decode(Journal.self, from: data),
          journal.version == (options.batchID == nil ? 1 : 2), journal.provider == options.provider,
          journal.queryVersion == queryVersion(options), journal.batchID == options.batchID,
          journal.attempts.count <= options.requestCap,
          Set(journal.attempts.map { requestKey(companyID: $0.companyID, query: $0.query) }).count == journal.attempts.count
    else { throw BatchError.journal }
    if options.batchID != nil {
        guard journal.selectedCompanyIDs == selected.map(\.id), journal.requestCap == options.requestCap,
              journal.attempts.filter({ $0.phase == "disambiguation" }).count <= 20 else { throw BatchError.journal }
    }
    let byID = Dictionary(uniqueKeysWithValues: (options.batchID == nil ? all : selected).map { ($0.id, $0) })
    for attempt in journal.attempts {
        let phase = attempt.phase ?? "primary"
        guard ["primary", "disambiguation"].contains(phase),
              options.batchID != nil || phase == "primary",
              let company = byID[attempt.companyID], attempt.companyName == company.name,
              attempt.query == query(company, options: options, phase: phase),
              attempt.completedAt.map({ $0 >= attempt.startedAt }) ?? true else { throw BatchError.journal }
        if attempt.state == .saved {
            guard attempt.runID != nil, let count = attempt.resultCount, (0...10).contains(count), attempt.completedAt != nil else { throw BatchError.journal }
        }
    }
    return journal
}
private func writeProtected(_ data: Data, to url: URL) throws {
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { throw BatchError.storage }
        defer { Darwin.close(fd) }
        guard fsync(fd) == 0 else { throw BatchError.storage }
        let directoryFD = Darwin.open(url.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY)
        guard directoryFD >= 0 else { throw BatchError.storage }
        defer { Darwin.close(directoryFD) }
        guard fsync(directoryFD) == 0 else { throw BatchError.storage }
    } catch { throw BatchError.storage }
}
private func saveJournal(_ journal: Journal, options: Options) throws {
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    try writeProtected(encoder.encode(journal), to: journalURL(options))
}
private final class BatchLock {
    private let descriptor: Int32
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        descriptor = Darwin.open(directory.appendingPathComponent(".source-batch.lock").path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw BatchError.storage }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { Darwin.close(descriptor); throw BatchError.alreadyRunning }
    }
    deinit { flock(descriptor, LOCK_UN); Darwin.close(descriptor) }
}
private func errorCode(_ error: Error) -> String {
    guard let source = error as? SourceError else { return "storage_or_local_error" }
    switch source {
    case .unauthorized: return "http_401"
    case .forbidden: return "http_403"
    case .rateLimited: return "http_429"
    case .http(let status): return "http_\(status)"
    case .network: return "network_outcome_uncertain"
    case .cancelled: return "cancelled_outcome_uncertain"
    case .invalidResponse: return "invalid_response_outcome_uncertain"
    default: return "source_error_no_retry"
    }
}
private func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes]),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line); fflush(stdout)
}
private func saveReceipt(_ options: Options, selected: [CompanyDossier], journal: Journal,
                         store: SourceCollectionStore, status: String) throws {
    guard let url = options.receiptURL else { return }
    let runs = try store.loadRuns().filter { $0.provider == options.provider }
    var rows: [[String: Any]] = []
    for company in selected {
        let matching = runs.filter { run in
            guard run.request?.companyID == company.id, let text = run.request?.query else { return false }
            return [query(company, options: options), query(company, options: options, phase: "disambiguation")].contains(text)
        }
        let attempts = journal.attempts.filter { $0.companyID == company.id }
        let primary = matching.filter { $0.request?.query == query(company, options: options) }
        rows.append(["companyID": company.id, "companyName": company.name,
                     "primaryQuery": query(company, options: options), "primarySaved": !primary.isEmpty,
                     "primaryResultCount": primary.first?.results.count ?? NSNull(),
                     "resultCount": matching.reduce(0) { $0 + $1.results.count }, "runIDs": matching.map(\.id),
                     "officialHostInResults": matching.flatMap(\.results).contains { isOfficialResult($0, company: company) },
                     "errorCodes": attempts.compactMap(\.errorCode),
                     "uncertain": attempts.contains { $0.state == .started },
                     "disambiguationRequested": attempts.contains { $0.phase == "disambiguation" }])
    }
    let object: [String: Any] = ["version": 1, "provider": options.provider.rawValue,
        "batchID": options.batchID ?? "legacy", "status": status, "updatedAt": ISO8601DateFormatter().string(from: Date()),
        "queryVersion": queryVersion(options), "requestCap": options.requestCap, "journalAttemptCount": journal.attempts.count,
        "primaryAttemptCount": journal.attempts.filter { ($0.phase ?? "primary") == "primary" }.count,
        "disambiguationAttemptCount": journal.attempts.filter { $0.phase == "disambiguation" }.count,
        "selectedCompanyIDs": selected.map(\.id), "journalPath": journalURL(options).path,
        "sourceRunsDirectory": store.directoryURL.path, "companies": rows,
        "affiliation": "unverified", "contentType": "searchSnippet", "automaticInference": false]
    try writeProtected(JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]), to: url)
}

@main private struct SourceBatch {
    static func main() async {
        do { try await run(Options(Array(CommandLine.arguments.dropFirst()))) }
        catch {
            let description: String
            if let error = error as? BatchError { description = error.localizedDescription }
            else if let error = error as? SourceError { description = error.localizedDescription }
            else if let error = error as? SourceCollectionStore.StoreError { description = error.localizedDescription }
            else { description = "Локальная ошибка. Проход остановлен; автоматического повтора нет." }
            emit(["event": "stopped", "message": description]); Darwin.exit(1)
        }
    }
    static func run(_ options: Options) async throws {
        let all = try CompanyCatalog.loadBundled().companies.filter { !$0.isDemo }
        guard !all.isEmpty, Set(all.map(\.id)).count == all.count,
              pilotIDs.allSatisfy({ id in all.contains { $0.id == id } }) else { throw BatchError.catalog }
        let pilot = pilotIDs.compactMap { id in all.first { $0.id == id } }
        let selected: [CompanyDossier]
        if let url = options.companyIDsURL {
            guard let data = try? Data(contentsOf: url), data.count <= 100_000,
                  let ids = try? JSONDecoder().decode([String].self, from: data), (1...409).contains(ids.count),
                  Set(ids).count == ids.count else { throw BatchError.selection }
            let byID = Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0) })
            selected = ids.compactMap { byID[$0] }
            guard selected.count == ids.count else { throw BatchError.selection }
        } else { selected = options.scope == "pilot" ? pilot : pilot + all.filter { !pilotIDs.contains($0.id) } }
        let store = SourceCollectionStore(directoryURL: options.dataDirectory.appendingPathComponent("source-runs", isDirectory: true))
        let lock = options.execute ? try BatchLock(directory: options.dataDirectory) : nil
        defer { withExtendedLifetime(lock) {} }
        let previous = try store.loadRuns().filter { $0.provider == options.provider && $0.request != nil }
        var successful = Set(previous.compactMap { run -> String? in
            guard let request = run.request else { return nil }
            return requestKey(companyID: request.companyID, query: request.query)
        })
        var journal = try loadJournal(options, all: all, selected: selected)
        let attempted = Set(journal.attempts.map { requestKey(companyID: $0.companyID, query: $0.query) })
        var eligible = selected
        if options.phase == "disambiguation" {
            eligible = selected.filter { company in
                guard officialHost(company) != nil,
                      let primary = previous.first(where: { $0.request?.companyID == company.id && $0.request?.query == query(company, options: options) }) else { return false }
                return !primary.results.contains { isOfficialResult($0, company: company) }
            }.sorted { lhs, rhs in
                let countL = previous.first { $0.request?.companyID == lhs.id && $0.request?.query == query(lhs, options: options) }?.results.count ?? 0
                let countR = previous.first { $0.request?.companyID == rhs.id && $0.request?.query == query(rhs, options: options) }?.results.count ?? 0
                return countL == countR ? lhs.id < rhs.id : countL < countR
            }
        }
        let completedCount = eligible.filter { successful.contains(requestKey(companyID: $0.id, query: query($0, options: options, phase: options.phase))) }.count
        let uncertain = eligible.filter {
            let key = requestKey(companyID: $0.id, query: query($0, options: options, phase: options.phase))
            return attempted.contains(key) && !successful.contains(key)
        }
        let pending = eligible.filter {
            let key = requestKey(companyID: $0.id, query: query($0, options: options, phase: options.phase))
            return !successful.contains(key) && !attempted.contains(key)
        }
        let phaseRemaining = options.phase == "primary" ? selected.count : 20 - journal.attempts.filter { $0.phase == "disambiguation" }.count
        let limit = max(0, min(options.maxRequests, pending.count, options.requestCap - journal.attempts.count, phaseRemaining))
        emit(["event": options.execute ? "plan_execute" : "plan_dry_run", "provider": options.provider.rawValue,
              "scope": options.companyIDsURL == nil ? options.scope : "explicit", "phase": options.phase,
              "batchID": options.batchID ?? "legacy", "requestCap": options.requestCap, "journalAttemptCount": journal.attempts.count,
              "catalogCompanies": all.count, "selectedCompanies": selected.count, "eligibleCompanies": eligible.count,
              "successfulAlready": completedCount, "uncertainOrFailedSkipped": uncertain.count,
              "pendingCompanies": pending.count, "requestsThisInvocation": limit,
              "resultsPerRequest": 10, "queryVersion": queryVersion(options),
              "pilot": pilot.map { ["id": $0.id, "name": $0.name, "query": query($0, options: options)] },
              "selectedCompanyIDs": selected.map(\.id), "nextCompanyIDs": pending.prefix(limit).map(\.id),
              "nextQueries": pending.prefix(limit).map { ["companyID": $0.id, "query": query($0, options: options, phase: options.phase)] },
              "uncertainCompanyIDs": uncertain.map(\.id),
              "queryExceptions": all.filter { query($0, options: options) != "\"\($0.name)\"" }
                .map { ["id": $0.id, "name": $0.name, "query": query($0, options: options)] }, "networkStarted": false])
        guard options.execute else { return }
        if limit == 0 { try saveReceipt(options, selected: selected, journal: journal, store: store, status: "no_pending_requests"); return }
        // An access/quota/billing failure blocks the entire provider batch on all later invocations.
        guard !journal.attempts.contains(where: { ["http_401", "http_402", "http_403", "http_429"].contains($0.errorCode ?? "") }) else {
            try saveReceipt(options, selected: selected, journal: journal, store: store, status: "provider_blocked_no_retry")
            emit(["event": "provider_blocked", "provider": options.provider.rawValue, "retry": false]); return
        }
        let credential = try loadCredential(options.provider)
        let client = SourceClient(provider: options.provider, apiKey: credential.apiKey, folderID: credential.folderID)
        var requests = 0; var results = 0
        for company in pending.prefix(limit) {
            if requests > 0 { try await Task.sleep(nanoseconds: options.delayMilliseconds * 1_000_000) }
            let request = SourceSearchRequest(companyID: company.id, companyName: company.name,
                                              query: query(company, options: options, phase: options.phase), limit: 10)
            let current = try store.loadRuns()
            if current.contains(where: { $0.provider == options.provider && $0.request?.companyID == request.companyID && $0.request?.query == request.query }) {
                successful.insert(requestKey(companyID: company.id, query: request.query)); continue
            }
            let index = journal.attempts.count
            journal.attempts.append(Attempt(companyID: company.id, companyName: company.name, query: request.query,
                                            startedAt: Date(), phase: options.batchID == nil ? nil : options.phase, state: .started))
            journal.updatedAt = Date(); try saveJournal(journal, options: options)
            requests += 1
            do {
                let run = try await client.search(request)
                try store.save(run)
                journal.attempts[index].state = .saved; journal.attempts[index].runID = run.id
                journal.attempts[index].resultCount = run.results.count; journal.attempts[index].completedAt = Date()
                journal.updatedAt = Date(); try saveJournal(journal, options: options)
                successful.insert(requestKey(companyID: company.id, query: request.query)); results += run.results.count
                try saveReceipt(options, selected: selected, journal: journal, store: store, status: "running")
                emit(["event": "saved", "provider": options.provider.rawValue, "phase": options.phase, "companyID": company.id,
                      "companyName": company.name, "runID": run.id, "results": run.results.count, "requestsThisInvocation": requests])
            } catch {
                journal.attempts[index].state = .failed; journal.attempts[index].errorCode = errorCode(error)
                journal.attempts[index].completedAt = Date(); journal.updatedAt = Date()
                try? saveJournal(journal, options: options)
                try? saveReceipt(options, selected: selected, journal: journal, store: store, status: "stopped_" + errorCode(error))
                emit(["event": "request_stopped", "provider": options.provider.rawValue, "companyID": company.id,
                      "errorCode": errorCode(error), "requestsThisInvocation": requests, "retry": false])
                throw error
            }
        }
        try saveReceipt(options, selected: selected, journal: journal, store: store, status: "phase_finished")
        emit(["event": "finished", "provider": options.provider.rawValue, "phase": options.phase,
              "requestsThisInvocation": requests, "resultsThisInvocation": results,
              "successfulSelected": eligible.filter { successful.contains(requestKey(companyID: $0.id, query: query($0, options: options, phase: options.phase))) }.count,
              "uncertainOrFailedSkipped": uncertain.count, "message": "Сниппеты сохранены в истории источников; связь с компанией и содержание страниц требуют проверки."])
    }
}
