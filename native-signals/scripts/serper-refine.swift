import Foundation
import Security
import SignalsCore
import Darwin

// A reviewed, explicit allowlist only. Dry-run never touches Keychain or the network.
private let queryVersion = "serper-brand-domain-v1"
private struct Options {
    var planURL: URL?
    var execute = false
    var maxRequests = 409
    var delayMilliseconds: UInt64 = 500
    var dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("TG Signals", isDirectory: true)

    init(_ arguments: [String]) throws {
        var index = 0
        func value() throws -> String {
            index += 1
            guard index < arguments.count else { throw RefineError.usage }
            return arguments[index]
        }
        while index < arguments.count {
            switch arguments[index] {
            case "--execute": execute = true
            case "--plan": planURL = URL(fileURLWithPath: try value())
            case "--max-requests":
                guard let number = Int(try value()), (1...409).contains(number) else { throw RefineError.usage }
                maxRequests = number
            case "--delay-ms":
                guard let number = UInt64(try value()), (250...60_000).contains(number) else { throw RefineError.usage }
                delayMilliseconds = number
            case "--data-dir":
                let path = try value()
                guard path.hasPrefix("/") else { throw RefineError.usage }
                dataDirectory = URL(fileURLWithPath: path, isDirectory: true)
            default: throw RefineError.usage
            }
            index += 1
        }
        guard planURL != nil else { throw RefineError.usage }
    }
}

private enum RefineError: LocalizedError {
    case usage, plan, catalog, journal, alreadyRunning, credential(OSStatus), storage
    var errorDescription: String? {
        switch self {
        case .usage: return "Usage: serper-refine --plan PATH [--max-requests 1...409] [--delay-ms 250...60000] [--data-dir /absolute/path] [--execute]. Default: read-only preview; no Keychain or network."
        case .plan: return "План несовместим с каталогом или историей. Нужны известные компании, исходный Serper-прогон, домен каталога и один уточнённый запрос на компанию."
        case .catalog: return "Каталог не прошёл проверку уникальности и лимита 409 реальных компаний."
        case .journal: return "Журнал попыток повреждён или несовместим с планом. Исходные файлы сохранены; запросы не запущены."
        case .alreadyRunning: return "Другой сборщик уже удерживает .source-batch.lock."
        case .credential(let status): return "Не удалось получить сохранённый Serper-доступ из Связки ключей (код \(status))."
        case .storage: return "Не удалось безопасно сохранить состояние. Проход остановлен; автоматического повтора нет."
        }
    }
}

private struct Plan: Decodable {
    let queryVersion: String
    let provider: SourceProvider
    let status: String
    let plannedRequestCount: Int
    let entries: [Entry]
}
private struct Entry: Decodable {
    let companyID: String
    let companyName: String
    let groundingHostname: String
    let previousRunID: String
    let previousQuery: String
    let queryVersion: String
    let plannedQuery: String
    let limit: Int
    let executed: Bool
}
private func normalizedHost(_ website: String?) -> String? {
    guard let website, let url = URL(string: website), ["http", "https"].contains(url.scheme ?? ""),
          var host = url.host?.lowercased(), !host.isEmpty else { return nil }
    if host.hasPrefix("www.") { host.removeFirst(4) }
    while host.hasSuffix(".") { host.removeLast() }
    if host == "ru.hexlet.io" { host = "hexlet.io" }
    return host
}
private func validQuery(_ query: String) -> Bool {
    !query.isEmpty && query.count <= 400 && query == query.trimmingCharacters(in: .whitespacesAndNewlines)
        && !query.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
}
private func loadPlan(_ options: Options, companies: [String: CompanyDossier], history: [SourceCollectionRun]) throws -> Plan {
    guard let url = options.planURL, let data = try? Data(contentsOf: url), data.count <= 2_000_000,
          let plan = try? JSONDecoder().decode(Plan.self, from: data),
          plan.provider == .serper, plan.queryVersion == queryVersion,
          plan.status == "prepared_only_no_network", plan.entries.count <= 409,
          plan.plannedRequestCount == plan.entries.count,
          Set(plan.entries.map(\.companyID)).count == plan.entries.count else { throw RefineError.plan }
    for entry in plan.entries {
        guard let company = companies[entry.companyID], company.name == entry.companyName,
              normalizedHost(company.website) == entry.groundingHostname,
              entry.queryVersion == queryVersion, entry.limit == 10, !entry.executed,
              validQuery(entry.plannedQuery), entry.plannedQuery != entry.previousQuery,
              entry.plannedQuery.split(whereSeparator: { $0.isWhitespace }).contains(Substring(entry.groundingHostname)),
              history.contains(where: {
                  $0.id == entry.previousRunID && $0.provider == .serper && $0.request?.companyID == entry.companyID
                      && $0.request?.query == entry.previousQuery
              }) else { throw RefineError.plan }
    }
    return plan
}

private struct Credential: Decodable { let apiKey: String }
private func loadCredential() throws -> Credential {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ru.hrvision.tgsignals.sources", kSecAttrAccount as String: "serper",
        kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    guard status == errSecSuccess, let data = result as? Data,
          let credential = try? JSONDecoder().decode(Credential.self, from: data),
          !credential.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { throw RefineError.credential(status) }
    return credential
}

private enum AttemptState: String, Codable { case started, saved, failed }
private struct Attempt: Codable {
    let companyID: String
    let companyName: String
    let query: String
    let previousRunID: String
    let startedAt: Date
    var completedAt: Date?
    var state: AttemptState
    var runID: String?
    var resultCount: Int?
    var errorCode: String?
}
private struct Journal: Codable {
    var version = 1
    var provider: SourceProvider = .serper
    var queryVersion: String = "serper-brand-domain-v1"
    let createdAt: Date
    var updatedAt: Date
    var attempts: [Attempt]
}
private func requestKey(_ companyID: String, _ query: String) -> String { companyID + "\u{0}" + query }
private func journalDirectory(_ options: Options) -> URL {
    options.dataDirectory.appendingPathComponent("source-batch-journals", isDirectory: true)
}
private func journalURL(_ options: Options) -> URL {
    journalDirectory(options).appendingPathComponent("\(queryVersion).json")
}
private func loadJournal(_ options: Options, companies: [String: CompanyDossier], plan: Plan) throws -> Journal {
    let url = journalURL(options)
    guard FileManager.default.fileExists(atPath: url.path) else {
        return Journal(createdAt: Date(), updatedAt: Date(), attempts: [])
    }
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    guard let data = try? Data(contentsOf: url), data.count <= 2_000_000,
          let journal = try? decoder.decode(Journal.self, from: data), journal.version == 1,
          journal.provider == .serper, journal.queryVersion == queryVersion, journal.attempts.count <= 409,
          Set(journal.attempts.map(\.companyID)).count == journal.attempts.count else { throw RefineError.journal }
    let selected = Dictionary(uniqueKeysWithValues: plan.entries.map { ($0.companyID, $0) })
    for attempt in journal.attempts {
        guard companies[attempt.companyID]?.name == attempt.companyName, validQuery(attempt.query),
              attempt.completedAt.map({ $0 >= attempt.startedAt }) ?? true else { throw RefineError.journal }
        if let entry = selected[attempt.companyID] {
            guard entry.plannedQuery == attempt.query else { throw RefineError.journal }
        }
        if attempt.state == .saved {
            guard attempt.runID != nil, let count = attempt.resultCount, (0...10).contains(count), attempt.completedAt != nil
            else { throw RefineError.journal }
        }
    }
    return journal
}
// Attempted requests from earlier collectors also suppress exact repetitions, including crashes.
private func previousAttemptKeys(_ options: Options) throws -> Set<String> {
    struct Envelope: Decodable {
        struct Previous: Decodable { let companyID: String; let query: String }
        let version: Int
        let provider: SourceProvider
        let attempts: [Previous]
    }
    let directory = journalDirectory(options)
    guard FileManager.default.fileExists(atPath: directory.path) else { return [] }
    var keys = Set<String>()
    for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) where url.pathExtension == "json" {
        guard let data = try? Data(contentsOf: url), data.count <= 2_000_000,
              let envelope = try? JSONDecoder().decode(Envelope.self, from: data), envelope.version == 1,
              envelope.attempts.count <= 409 else { throw RefineError.journal }
        if envelope.provider == .serper {
            for attempt in envelope.attempts {
                guard !attempt.companyID.isEmpty, validQuery(attempt.query) else { throw RefineError.journal }
                keys.insert(requestKey(attempt.companyID, attempt.query))
            }
        }
    }
    return keys
}
private func saveJournal(_ journal: Journal, options: Options) throws {
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let url = journalURL(options)
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(journal).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        let descriptor = Darwin.open(url.path, O_RDONLY)
        guard descriptor >= 0 else { throw RefineError.storage }
        defer { Darwin.close(descriptor) }
        guard fsync(descriptor) == 0 else { throw RefineError.storage }
        // Commit the atomic rename as well as the file contents before sending a paid request.
        let directoryDescriptor = Darwin.open(url.deletingLastPathComponent().path, O_RDONLY)
        guard directoryDescriptor >= 0 else { throw RefineError.storage }
        defer { Darwin.close(directoryDescriptor) }
        guard fsync(directoryDescriptor) == 0 else { throw RefineError.storage }
    } catch { throw RefineError.storage }
}
private final class BatchLock {
    private let descriptor: Int32
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        descriptor = Darwin.open(directory.appendingPathComponent(".source-batch.lock").path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw RefineError.storage }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(descriptor); throw RefineError.alreadyRunning
        }
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
private func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line); fflush(stdout)
}

@main
private struct SerperRefine {
    static func main() async {
        do { try await run(Options(Array(CommandLine.arguments.dropFirst()))) }
        catch {
            let message: String
            if let safe = error as? RefineError { message = safe.localizedDescription }
            else if let safe = error as? SourceError { message = safe.localizedDescription }
            else if let safe = error as? SourceCollectionStore.StoreError { message = safe.localizedDescription }
            else { message = "Локальная ошибка; проход остановлен без автоматического повтора." }
            emit(["event": "stopped", "message": message]); Darwin.exit(1)
        }
    }
    static func run(_ options: Options) async throws {
        let all = try CompanyCatalog.loadBundled().companies.filter { !$0.isDemo }
        guard !all.isEmpty, all.count <= 409, Set(all.map(\.id)).count == all.count else { throw RefineError.catalog }
        let companies = Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0) })
        let lock = options.execute ? try BatchLock(directory: options.dataDirectory) : nil
        defer { withExtendedLifetime(lock) {} }
        let store = SourceCollectionStore(directoryURL: options.dataDirectory.appendingPathComponent("source-runs", isDirectory: true))
        let history = try store.loadRuns()
        let plan = try loadPlan(options, companies: companies, history: history)
        var journal = try loadJournal(options, companies: companies, plan: plan)
        let attempted = try previousAttemptKeys(options)
        let successful = Set(history.filter { $0.provider == .serper }.compactMap { run -> String? in
            guard let request = run.request else { return nil }; return requestKey(request.companyID, request.query)
        })
        let complete = plan.entries.filter { successful.contains(requestKey($0.companyID, $0.plannedQuery)) }
        let uncertain = plan.entries.filter {
            let key = requestKey($0.companyID, $0.plannedQuery)
            return attempted.contains(key) && !successful.contains(key)
        }
        let pending = plan.entries.filter {
            let key = requestKey($0.companyID, $0.plannedQuery)
            return !successful.contains(key) && !attempted.contains(key)
        }
        let limit = min(options.maxRequests, pending.count, 409 - journal.attempts.count)
        emit(["event": options.execute ? "plan_execute" : "plan_dry_run", "provider": "serper",
              "queryVersion": queryVersion, "selectedCompanies": plan.entries.count,
              "successfulAlready": complete.count, "uncertainOrFailedSkipped": uncertain.count,
              "pendingCompanies": pending.count, "requestsThisInvocation": limit, "resultsPerRequest": 10,
              "nextCompanyIDs": pending.prefix(limit).map(\.companyID), "networkStarted": false])
        guard options.execute, limit > 0 else { return }
        let credential = try loadCredential()
        let client = SourceClient(provider: .serper, apiKey: credential.apiKey)
        var requests = 0, results = 0
        for entry in pending.prefix(limit) {
            if requests > 0 { try await Task.sleep(nanoseconds: options.delayMilliseconds * 1_000_000) }
            // The app does not honor the CLI lock; avoid UI searches while a CLI is running.
            if try store.loadRuns().contains(where: {
                $0.provider == .serper && $0.request?.companyID == entry.companyID && $0.request?.query == entry.plannedQuery
            }) { continue }
            let request = SourceSearchRequest(companyID: entry.companyID, companyName: entry.companyName, query: entry.plannedQuery, limit: 10)
            let index = journal.attempts.count
            journal.attempts.append(Attempt(companyID: entry.companyID, companyName: entry.companyName,
                                            query: entry.plannedQuery, previousRunID: entry.previousRunID,
                                            startedAt: Date(), state: .started))
            journal.updatedAt = Date()
            try saveJournal(journal, options: options)
            requests += 1
            do {
                let run = try await client.search(request)
                try store.save(run)
                journal.attempts[index].state = .saved
                journal.attempts[index].runID = run.id
                journal.attempts[index].resultCount = run.results.count
                journal.attempts[index].completedAt = Date()
                journal.updatedAt = Date()
                try saveJournal(journal, options: options)
                results += run.results.count
                emit(["event": "saved", "companyID": entry.companyID, "runID": run.id,
                      "results": run.results.count, "requestsThisInvocation": requests])
            } catch {
                journal.attempts[index].state = .failed
                journal.attempts[index].errorCode = errorCode(error)
                journal.attempts[index].completedAt = Date()
                journal.updatedAt = Date()
                try? saveJournal(journal, options: options)
                emit(["event": "request_stopped", "companyID": entry.companyID, "errorCode": errorCode(error),
                      "requestsThisInvocation": requests, "retry": false])
                throw error
            }
        }
        emit(["event": "finished", "provider": "serper", "requestsThisInvocation": requests,
              "resultsThisInvocation": results, "uncertainOrFailedSkipped": uncertain.count,
              "message": "Уточнённые поисковые сниппеты сохранены отдельно от первого прохода; принадлежность и страницы требуют проверки."])
    }
}
