import Foundation
import Security
import CryptoKit
import Darwin
import SignalsCore

// Read-only Harvest collection with a durable at-most-once journal. No credentials in files/arguments.
private struct Item: Codable {
    var companyID: String
    var companyName: String
    var operation: HarvestOperation
    var query: String
    var targetURL: String?
    var jobTitles: String?
    var costMilliUSD: Int { operation == .people ? 100 : 4 }
    var key: String {
        let text = [companyID, operation.rawValue, query, targetURL ?? "", jobTitles ?? ""].joined(separator: "\u{0}")
        return SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
private struct Attempt: Codable {
    let key: String
    let item: Item
    let startedAt: Date
    var state: String
    var completedAt: Date?
    var runID: String?
    var count: Int?
    var errorCode: String?
}
private struct Journal: Codable {
    var version = 1
    var capMilliUSD = 640
    var attempts: [Attempt] = []
}
private enum Failure: Error { case usage, credential, account, budget, integrity, locked, write }
private let fm = FileManager.default
private let decoder: JSONDecoder = { let v = JSONDecoder(); v.dateDecodingStrategy = .iso8601; return v }()
private let encoder: JSONEncoder = { let v = JSONEncoder(); v.dateEncodingStrategy = .iso8601; v.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]; return v }()

private func emit(_ data: [String: Any]) {
    if let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys, .withoutEscapingSlashes]) {
        print(String(decoding: bytes, as: UTF8.self)); fflush(stdout)
    }
}
private func persist<T: Encodable>(_ value: T, at url: URL) throws {
    try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try encoder.encode(value).write(to: url, options: .atomic)
    try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW)
    guard fd >= 0 else { throw Failure.write }
    defer { Darwin.close(fd) }
    guard fsync(fd) == 0 else { throw Failure.write }
    let directory = Darwin.open(url.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY)
    guard directory >= 0 else { throw Failure.write }
    defer { Darwin.close(directory) }
    guard fsync(directory) == 0 else { throw Failure.write }
}
private func credential() throws -> String {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ru.hrvision.tgsignals.sources", kSecAttrAccount as String: "harvest",
        kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
    var value: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &value) == errSecSuccess,
          let data = value as? Data, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let key = object["apiKey"] as? String, !key.isEmpty else { throw Failure.credential }
    return key
}
private struct Account: Codable {
    var checkedAt: Date
    var balanceUSD: Double
    var membershipTier: String
    var requestsConcurrency: Int
}
private final class RejectRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
private func account(_ key: String) async throws -> Account {
    let config = URLSessionConfiguration.ephemeral
    config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
    config.httpShouldSetCookies = false; config.timeoutIntervalForRequest = 40
    let session = URLSession(configuration: config); defer { session.invalidateAndCancel() }
    var request = URLRequest(url: URL(string: "https://api.harvestapi.io/users/my-api-user")!)
    request.setValue(key, forHTTPHeaderField: "X-API-Key")
    request.setValue("TGSignals/0.7.3", forHTTPHeaderField: "User-Agent")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    let (data, response) = try await session.data(for: request, delegate: RejectRedirects())
    guard (response as? HTTPURLResponse)?.statusCode == 200,
          let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let usage = value["usage"] as? [String: Any], let balance = usage["balance"] as? Double,
          balance >= 0 else { throw Failure.account }
    return Account(checkedAt: Date(), balanceUSD: balance, membershipTier: usage["membershipTier"] as? String ?? "unknown",
                   requestsConcurrency: usage["requestsConcurrency"] as? Int ?? 1)
}
private func safeError(_ error: Error) -> String {
    if let e = error as? Failure {
        switch e {
        case .usage: return "invalid_arguments"
        case .credential: return "saved_credential_unavailable"
        case .account: return "account_response_invalid_or_forbidden"
        case .budget: return "local_budget_guard"
        case .integrity: return "manifest_or_journal_integrity"
        case .locked: return "another_harvest_batch_active"
        case .write: return "durable_write_failed"
        }
    }
    if let e = error as? HarvestError { if case .insufficientCredits = e { return "http_402_insufficient_credits" }; return "harvest_validation" }
    if let e = error as? SourceError {
        switch e {
        case .unauthorized: return "http_401"
        case .forbidden: return "http_403"
        case .rateLimited: return "http_429"
        case .http(let n): return "http_\(n)"
        case .network: return "network_outcome_uncertain"
        case .invalidResponse: return "invalid_response_outcome_uncertain"
        default: return "source_error_no_retry"
        }
    }
    return "local_or_account_error"
}
@main private struct HarvestBatch {
    static func main() async {
        do { try await run() }
        catch { emit(["event": "stopped", "errorCode": safeError(error), "retry": false]); Darwin.exit(1) }
    }
    static func run() async throws {
        let args = Array(CommandLine.arguments.dropFirst())
        guard args.count >= 4, args[0] == "--plan", args[2] == "--output", args.count == 4 || args == Array(args.prefix(4)) + ["--execute"] else { throw Failure.usage }
        let execute = args.last == "--execute"
        let planURL = URL(fileURLWithPath: args[1]), output = URL(fileURLWithPath: args[3], isDirectory: true)
        let plan = try decoder.decode([Item].self, from: Data(contentsOf: planURL))
        guard !plan.isEmpty, plan.count <= 100, Set(plan.map(\.key)).count == plan.count,
              plan.allSatisfy({ !$0.companyID.isEmpty && !$0.companyName.isEmpty && !$0.query.isEmpty && $0.query.count <= 400 }) else { throw Failure.integrity }
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: output.deletingLastPathComponent().appendingPathComponent("audit/manifest-70.json"))) as? [[String: Any]] ?? []
        let accepted = Dictionary(uniqueKeysWithValues: manifest.compactMap { row -> (String, String)? in
            guard let id = row["id"] as? String, let name = row["name"] as? String else { return nil }; return (id, name)
        })
        guard accepted.count == 70, plan.allSatisfy({ accepted[$0.companyID] == $0.companyName }) else { throw Failure.integrity }
        let root = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals")
        let store = SourceCollectionStore(directoryURL: root.appendingPathComponent("source-runs"))
        let journalURL = output.appendingPathComponent("journal.json")
        try fm.createDirectory(at: output, withIntermediateDirectories: true)
        let lock = Darwin.open(output.appendingPathComponent(".harvest-batch.lock").path, O_CREAT | O_RDWR, 0o600)
        guard lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw Failure.locked }
        defer { flock(lock, LOCK_UN); Darwin.close(lock) }
        var journal = fm.fileExists(atPath: journalURL.path) ? try decoder.decode(Journal.self, from: Data(contentsOf: journalURL)) : Journal()
        guard journal.version == 1, journal.capMilliUSD == 640, journal.attempts.count <= 100,
              Set(journal.attempts.map(\.key)).count == journal.attempts.count,
              journal.attempts.allSatisfy({ $0.key == $0.item.key && ["started", "saved", "failed"].contains($0.state) }) else { throw Failure.integrity }
        let history = try store.loadRuns().filter { $0.provider == .harvest }
        let pending = plan.filter { item in
            !journal.attempts.contains(where: { $0.key == item.key }) && !history.contains(where: {
                $0.request?.companyID == item.companyID && $0.request?.query == item.query && $0.harvest?.operation == item.operation &&
                ($0.harvest?.targetURL ?? "") == (item.targetURL ?? "") && ($0.harvest?.jobTitles ?? "") == (item.jobTitles ?? "")
            })
        }
        let spent = journal.attempts.reduce(0) { $0 + $1.item.costMilliUSD }
        let nextCost = pending.reduce(0) { $0 + $1.costMilliUSD }
        emit(["event": execute ? "plan_execute" : "plan_dry_run", "planned": plan.count, "pending": pending.count,
              "reservedCostUSD": Double(spent) / 1000, "thisPlanCostUSD": Double(nextCost) / 1000,
              "ceilingUSD": 0.64, "automaticRetries": false])
        guard spent + nextCost <= journal.capMilliUSD, journal.attempts.count + pending.count <= 100 else { throw Failure.budget }
        guard execute, !pending.isEmpty else { return }
        let key = try credential()
        let before = try await account(key)
        try persist(before, at: output.appendingPathComponent("balance-before-\(planURL.deletingPathExtension().lastPathComponent).json"))
        guard before.balanceUSD + 0.0000001 >= Double(nextCost) / 1000 else { throw Failure.budget }
        emit(["event": "balance_checked", "balanceUSD": before.balanceUSD, "membershipTier": before.membershipTier])
        let client = HarvestClient(apiKey: key)
        for item in pending {
            let index = journal.attempts.count
            journal.attempts.append(Attempt(key: item.key, item: item, startedAt: Date(), state: "started"))
            try persist(journal, at: journalURL)
            do {
                let request = SourceSearchRequest(companyID: item.companyID, companyName: item.companyName, query: item.query, limit: 100)
                let run = try await client.collect(request, operation: item.operation, targetURL: item.targetURL ?? "", jobTitles: item.jobTitles ?? "")
                // Encode + validate before storing, then verify persisted bytes through the native decoder.
                try store.save(run)
                let savedURL = store.directoryURL.appendingPathComponent(run.id + ".json")
                let saved = try SourceCollectionStore.decode(Data(contentsOf: savedURL))
                guard saved.id == run.id, saved.results.count == run.results.count else { throw Failure.write }
                let runFD = Darwin.open(savedURL.path, O_RDONLY | O_NOFOLLOW)
                guard runFD >= 0 else { throw Failure.write }
                let syncStatus = fsync(runFD); Darwin.close(runFD)
                guard syncStatus == 0 else { throw Failure.write }
                journal.attempts[index].state = "saved"; journal.attempts[index].runID = run.id
                journal.attempts[index].count = run.results.count; journal.attempts[index].completedAt = Date()
                try persist(journal, at: journalURL)
                emit(["event": "saved", "companyID": item.companyID, "operation": item.operation.rawValue,
                      "results": run.results.count, "runID": run.id, "hasMore": run.hasMore])
            } catch {
                journal.attempts[index].state = "failed"; journal.attempts[index].errorCode = safeError(error)
                journal.attempts[index].completedAt = Date(); try? persist(journal, at: journalURL)
                emit(["event": "request_failed", "companyID": item.companyID, "errorCode": safeError(error), "retry": false])
                throw error
            }
            try await Task.sleep(nanoseconds: 600_000_000)
        }
        let after = try await account(key)
        try persist(after, at: output.appendingPathComponent("balance-after-\(planURL.deletingPathExtension().lastPathComponent).json"))
        emit(["event": "finished", "saved": pending.count, "balanceUSD": after.balanceUSD,
              "actualBalanceDeltaUSD": before.balanceUSD - after.balanceUSD])
    }
}
