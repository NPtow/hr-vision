import Foundation
import Darwin
import SignalsCore

// Only the approved public-community pilot. No upload, payment, deletion, or messaging tools.
private let permittedTools = Set(["vk_task_create", "vk_tasks_list", "vk_task_result_get"])
private let endpoint = URL(string: "https://vk-mcp.targethunter.ru/mcp")!
private enum ProbeError: LocalizedError {
    case usage, schema, credential, invalidResponse, unsupportedProtocol, tooLarge, http(Int), redirect, network, rpc, tool, storage
    var errorDescription: String? {
        switch self {
        case .usage: return "Invalid targethunter-pilot arguments; no request started."
        case .schema: return "Сохранённый tools/list не подтверждает требуемую схему. Запросы не запущены."
        case .credential: return "Локальный ключ TargetHunter недоступен. Сохраните доступ в приложении."
        case .invalidResponse: return "Неподдерживаемый ответ MCP."
        case .unsupportedProtocol: return "Неподдерживаемая версия MCP."
        case .tooLarge: return "Ответ превышает 4 MB."
        case .http(let code): return "HTTP \(code); автоматического повтора нет."
        case .redirect: return "Redirect отклонён; ключ по новому адресу не отправлен."
        case .network: return "Сетевая ошибка; автоматического повтора нет."
        case .rpc: return "MCP вернул RPC-ошибку; текст сервера не выводится."
        case .tool: return "Инструмент вернул isError=true; санитизированный ответ сохранён для разбора."
        case .storage: return "Не удалось безопасно записать ответ; дальнейшие вызовы остановлены."
        }
    }
}


private func object(_ url: URL) throws -> [String: Any] {
    guard let data = try? Data(contentsOf: url), data.count <= 8_000_000,
          let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw ProbeError.schema }
    return result
}
private func payload(_ result: [String: Any]) -> [String: Any]? {
    if let structured = result["structuredContent"] as? [String: Any] { return structured }
    let entries = result["content"] as? [[String: Any]] ?? []
    let parsed = entries.compactMap { item -> [String: Any]? in
        guard item["type"] as? String == "text", let text = item["text"] as? String,
              let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { return nil }
        return parsed
    }
    return parsed.count == 1 ? parsed[0] : nil
}
private func taskList(_ result: [String: Any]) throws -> [[String: Any]] {
    // Confirmed live on 2026-10-11: {"status":"done","tasks":[]}.
    guard result["isError"] as? Bool != true, let body = payload(result),
          body["status"] as? String == "done", let tasks = body["tasks"] as? [[String: Any]],
          body["hasMore"] as? Bool != true, body["nextCursor"] == nil,
          (body["total"] as? Int ?? tasks.count) <= tasks.count else { throw PilotError.unrecognizedTaskList }
    return tasks
}


private func validID(_ value: String) -> Bool { value.range(of: "^[0-9a-fA-F]{24}$", options: .regularExpression) != nil }
private func responseTaskID(_ value: Any?) -> String? {
    // Accept only unambiguous IDs under the returned data, never the known project ID.
    var ids = Set<String>()
    if let string = value as? String, validID(string), string != projectID { ids.insert(string) }
    if let data = value as? [String: Any] {
        for key in ["id", "_id", "task_id"] {
            if let id = data[key] as? String, validID(id), id != projectID { ids.insert(id) }
        }
        if let task = data["task"], let id = responseTaskID(task) { ids.insert(id) }
    }
    return ids.count == 1 ? ids.first : nil
}

private func createdTaskID(_ result: [String: Any], customName: String) throws -> String {
    guard result["isError"] as? Bool != true, let body = payload(result),
          body["type"] as? String != "error" else { throw PilotError.upstreamTool }
    // Confirmed live: status="msg" is an envelope, not an error; type="success" carries task.id.
    guard body["status"] as? Bool == true ||
            (body["status"] as? String == "msg" && body["type"] as? String == "success"),
          let id = responseTaskID(body["data"] ?? body) else { throw PilotError.unrecognizedCreateResponse }
    if let task = body["task"] as? [String: Any] {
        guard task["project_id"].map({ $0 as? String == projectID }) ?? true,
              task["module"].map({ $0 as? String == taskModule }) ?? true,
              task["name"].map({ $0 as? String == customName }) ?? true else { throw PilotError.unrecognizedCreateResponse }
    }
    return id
}


private func credential() throws -> String {
    guard let parsed = try? TargetHunterCredentialStore().load() else { throw ProbeError.credential }
    let key = parsed.apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty, key.count <= 4096, key.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }) else { throw ProbeError.credential }
    return key
}

private final class RejectRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

private final class BatchMCP {
    private let key: String
    private let session: URLSession
    private let redirect = RejectRedirect()
    private var sessionID: String?
    private var protocolVersion: String?
    private var lastRequestStarted: TimeInterval?
    private let minimumInterval: TimeInterval
    init(key: String, minimumIntervalMS: UInt64 = 3100) {
        self.key = key
        self.minimumInterval = Double(minimumIntervalMS) / 1000
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.httpShouldSetCookies = false; config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 45
        session = URLSession(configuration: config)
    }
    deinit { session.invalidateAndCancel() }

    func start() async throws {
        let (data, response) = try await send([
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": ["protocolVersion": "2025-06-18", "capabilities": [:],
                       "clientInfo": ["name": "tg-signals-company-batch", "version": "1.0"]]
        ], id: 1)
        let result = try rpcResult(data, id: 1)
        guard let version = result["protocolVersion"] as? String,
              ["2025-03-26", "2025-06-18", "2025-11-25"].contains(version) else { throw ProbeError.unsupportedProtocol }
        guard (result["capabilities"] as? [String: Any])?["tools"] != nil else { throw ProbeError.invalidResponse }
        let identifier = response.value(forHTTPHeaderField: "Mcp-Session-Id")
        if let identifier, identifier.isEmpty || !identifier.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }) { throw ProbeError.invalidResponse }
        sessionID = identifier; protocolVersion = version
        _ = try await send(["jsonrpc": "2.0", "method": "notifications/initialized"], id: nil)
    }

    func call(_ name: String, arguments: [String: Any], id: Int) async throws -> [String: Any] {
        guard permittedTools.contains(name), protocolVersion != nil else { throw ProbeError.schema }
        let (data, _) = try await send(["jsonrpc": "2.0", "id": id, "method": "tools/call",
                                       "params": ["name": name, "arguments": arguments]], id: id)
        return try rpcResult(data, id: id)
    }

    private func send(_ body: [String: Any], id: Int?) async throws -> (Data, HTTPURLResponse) {
        // All HTTP calls share one monotonic clock, including initialization, notifications,
        // task creation, task lists and exports. A read retry cannot bypass this pacing.
        if let last = lastRequestStarted {
            let wait = max(0, minimumInterval - (ProcessInfo.processInfo.systemUptime - last))
            if wait > 0 { try await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000)) }
        }
        lastRequestStarted = ProcessInfo.processInfo.systemUptime
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "POST"; request.httpShouldHandleCookies = false
        request.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        if let sessionID { request.setValue(sessionID, forHTTPHeaderField: "Mcp-Session-Id") }
        if let protocolVersion { request.setValue(protocolVersion, forHTTPHeaderField: "MCP-Protocol-Version") }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        do {
            let (bytes, response) = try await session.bytes(for: request, delegate: redirect)
            guard let http = response as? HTTPURLResponse else { throw ProbeError.invalidResponse }
            if (300..<400).contains(http.statusCode) { throw ProbeError.redirect }
            guard (200..<300).contains(http.statusCode) else { throw ProbeError.http(http.statusCode) }
            guard let id else { return (Data(), http) }
            let isSSE = http.value(forHTTPHeaderField: "Content-Type")?.lowercased().contains("text/event-stream") == true
            var data = Data(), line = Data(), event = ""
            var count = 0
            for try await byte in bytes {
                count += 1; guard count <= 4_000_000 else { throw ProbeError.tooLarge }
                if !isSSE { data.append(byte); continue }
                if byte == 10 {
                    if let message = try appendSSE(line, event: &event, id: id) { return (message, http) }
                    line.removeAll(keepingCapacity: true)
                } else { line.append(byte) }
            }
            if isSSE {
                if !line.isEmpty, let message = try appendSSE(line, event: &event, id: id) { return (message, http) }
                if let message = matchingEvent(event, id: id) { return (message, http) }
                throw ProbeError.invalidResponse
            }
            return (data, http)
        } catch let error as ProbeError { throw error }
        catch { throw ProbeError.network }
    }

    private func appendSSE(_ line: Data, event: inout String, id: Int) throws -> Data? {
        guard var text = String(data: line, encoding: .utf8) else { throw ProbeError.invalidResponse }
        if text.hasSuffix("\r") { text.removeLast() }
        if text.isEmpty { let value = matchingEvent(event, id: id); event = ""; return value }
        if text.hasPrefix("data:") {
            var value = String(text.dropFirst(5)); if value.hasPrefix(" ") { value.removeFirst() }
            event += value + "\n"
        }
        return nil
    }
    private func matchingEvent(_ event: String, id: Int) -> Data? {
        let data = Data(event.utf8)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], object["id"] as? Int == id else { return nil }
        return data
    }
    private func rpcResult(_ data: Data, id: Int) throws -> [String: Any] {
        guard let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              parsed["jsonrpc"] as? String == "2.0", parsed["id"] as? Int == id else { throw ProbeError.invalidResponse }
        if parsed["error"] != nil { throw ProbeError.rpc }
        guard let raw = parsed["result"] as? [String: Any],
              let result = sanitize(raw) as? [String: Any] else { throw ProbeError.invalidResponse }
        return result
    }
    private func sanitize(_ value: Any) -> Any {
        if let string = value as? String { return string.replacingOccurrences(of: key, with: "[REDACTED]") }
        if let array = value as? [Any] { return array.map(sanitize) }
        if let object = value as? [String: Any] {
            let sensitive = Set(["authorization", "x-api-key", "api-key", "apikey", "api_key", "access_token", "refresh_token", "token", "mcp-session-id"])
            return object.reduce(into: [String: Any]()) { output, item in
                guard !(sensitive.contains(item.key.lowercased()) && item.value is String) else { return }
                output[item.key.replacingOccurrences(of: key, with: "[REDACTED]")] = sanitize(item.value)
            }
        }
        return value
    }
}

private func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes]),
       let text = String(data: data, encoding: .utf8) { print(text); fflush(stdout) }
}


private func validateSchemas(_ options: Options) throws {
    let run = try object(options.schemaRun)
    guard run["provider"] as? String == "targetHunter", let tools = run["tools"] as? [[String: Any]] else { throw ProbeError.schema }
    let expected: [String: Set<String>] = [
        "vk_task_create": ["project_id", "task_module"],
        "vk_tasks_list": ["project_id"],
        "vk_task_result_get": ["project_id", "task_id"]
    ]
    for name in permittedTools {
        guard let item = tools.first(where: { $0["name"] as? String == name }), let text = item["inputSchemaJSON"] as? String,
              let schema = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
              schema["type"] as? String == "object", Set(schema["required"] as? [String] ?? []) == expected[name]
        else { throw ProbeError.schema }
    }
    let moduleRun = try object(options.moduleRun)
    guard let result = moduleRun["result"] as? [String: Any], let envelope = payload(result), envelope["status"] as? Bool == true,
          let modules = envelope["data"] as? [[String: Any]], let module = modules.first(where: { $0["module"] as? String == taskModule }),
          let params = (module["params"] as? [String: Any])?["properties"] as? [String: Any],
          (params["exact"] as? [String: Any])?["type"] as? String == "boolean",
          let whereFind = params["where_find"] as? [String: Any],
          ((whereFind["items"] as? [String: Any])?["enum"] as? [String])?.contains("name") == true,
          let inputs = module["input"] as? [[String: Any]], let input = inputs.first(where: { $0["slot"] as? String == "task" }),
          input["type_field"] as? String == "input-type-task", input["source_field"] as? String == "input-from-task",
          let sources = input["sources"] as? [String: Any], let queryType = sources["none_none_array_query"] as? [String: Any],
          (queryType["hand"] as? [String: Any])?["field"] as? String == "none_none_array_query-task"
    else { throw ProbeError.schema }
}


private let projectID = "6acaf3c667052cdb2f0e9e2f"
private let taskModule = "vk/2"
private let planID = "vk-company-batch-v1"
private let pilotIDs = ["brunoyam", "karpov", "setters"]
private enum PilotError: Error { case upstreamTool, unrecognizedCreateResponse, unrecognizedTaskList }
private enum BatchError: LocalizedError {
    case usage, catalog, journal, locked, unresolved, receipt, importFailure, incomplete, failedCompanies, rateLimitRecovery, selfTest
    var errorDescription: String? {
        switch self {
        case .usage: return "Usage: targethunter-batch --schema-run /path --module-run /path --pilot-dir /path [--scope pilot|all] [--max-new 0...409] [--window 1...4] [--delay-ms 250...10000] [--poll-ms 1000...60000] [--max-polls 1...1200] [--data-dir /path] [--export-mode mcp] [--refresh-partials] [--retry-rate-limited] [--http-interval-ms 3000...60000] [--execute]. Dry run never accesses credential/network/writes."
        case .catalog: return "Каталог компаний некорректен; запросы не запускались."
        case .journal: return "Журнал batch повреждён или не соответствует каталогу; сохранён без изменений."
        case .locked: return "Другой batch уже использует TargetHunter."
        case .unresolved: return "Неопределённый исход создания. Новые задачи остановлены; повторного создания не будет."
        case .receipt: return "Не удалось подтвердить сохранённый ответ TargetHunter."
        case .importFailure: return "Ответ получен и сохранён, импорт остановлен. Следующий запуск повторит только локальный импорт."
        case .incomplete: return "Достигнут предел ожидания; проход НЕ ЗАВЕРШЁН. Полученные данные сохранены, оставшиеся task ID продолжатся без повторного создания."
        case .failedCompanies: return "Проход содержит компании с отказом; полный успех не подтверждён. Результаты и причины отказов сохранены."
        case .rateLimitRecovery: return "Повтор создания запрещён: не подтверждён точный отказ по лимиту API без task ID и отсутствие задачи в свежем списке."
        case .selfTest: return "Offline self test failed."
        }
    }
}
private struct Options {
    var execute = false
    var selfTest = false
    var refreshPartials = false
    var retryRateLimited = false
    var httpIntervalMS: UInt64 = 3100
    var scope = "all"
    var maxNew = 409
    var window = 2
    var delayMS: UInt64 = 250
    var pollMS: UInt64 = 4000
    var maxPolls = 300
    var exportMode = "mcp"
    var dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
    var schemaRun = URL(fileURLWithPath: "/unselected")
    var moduleRun = URL(fileURLWithPath: "/unselected")
    var pilotDirectory = URL(fileURLWithPath: "/unselected")
    var directory: URL { dataDirectory.appendingPathComponent("targethunter-batch", isDirectory: true) }
    init(_ args: [String]) throws {
        var i = 0
        func next() throws -> String { i += 1; guard i < args.count else { throw BatchError.usage }; return args[i] }
        func path() throws -> URL { let value = try next(); guard value.hasPrefix("/") else { throw BatchError.usage }; return URL(fileURLWithPath: value) }
        while i < args.count {
            switch args[i] {
            case "--execute": execute = true
            case "--self-test": selfTest = true
            case "--refresh-partials": refreshPartials = true
            case "--retry-rate-limited": retryRateLimited = true
            case "--http-interval-ms": guard let n = UInt64(try next()), (3000...60000).contains(n) else { throw BatchError.usage }; httpIntervalMS = n
            case "--scope": scope = try next()
            case "--max-new": guard let n = Int(try next()), (0...409).contains(n) else { throw BatchError.usage }; maxNew = n
            case "--window": guard let n = Int(try next()), (1...4).contains(n) else { throw BatchError.usage }; window = n
            case "--max-polls": guard let n = Int(try next()), (1...1200).contains(n) else { throw BatchError.usage }; maxPolls = n
            case "--delay-ms": guard let n = UInt64(try next()), (250...10000).contains(n) else { throw BatchError.usage }; delayMS = n
            case "--poll-ms": guard let n = UInt64(try next()), (1000...60000).contains(n) else { throw BatchError.usage }; pollMS = n
            case "--export-mode": exportMode = try next()
            case "--data-dir": dataDirectory = try path()
            case "--schema-run": schemaRun = try path()
            case "--module-run": moduleRun = try path()
            case "--pilot-dir": pilotDirectory = try path()
            default: throw BatchError.usage
            }
            i += 1
        }
        guard ["pilot", "all"].contains(scope), exportMode == "mcp",
              selfTest || [schemaRun, moduleRun, pilotDirectory].allSatisfy({ $0.path != "/unselected" }) else { throw BatchError.usage }
    }
}
private struct Entry: Codable {
    var companyID: String
    var companyName: String
    var query: String
    var customName: String
    var state = "planned"
    var startedAt: Date?
    var completedAt: Date?
    var taskID: String?
    var createResponse: String?
    var resultResponse: String?
    var reportedCountResponse: String?
    var receivedResultCount: Int?
    var reportedResultCount: Int?
    var resultExtent: String?
    var nativeRunID: String?
    var lastStatus: String?
    var errorCode: String?
    var reusedPilot = false
    var lastResultReadError: String?
    var resultHistory: [String]?
    var refreshCount: Int?
    var rateLimitRecoveryHistory: [String]?
    var rateLimitRecoveryCount: Int?
}
private struct Journal: Codable {
    var version = 1
    var planID = "vk-company-batch-v1"
    var projectID = "6acaf3c667052cdb2f0e9e2f"
    var taskModule = "vk/2"
    var createdAt = Date()
    var updatedAt = Date()
    var entries: [Entry]
}
private func publicQuery(_ entry: Entry) -> [String: Any] {
    ["project_id": projectID, "task_module": taskModule, "custom_name": entry.customName,
     "params": ["input-type-task": "none_none_array_query", "input-from-task": "hand",
                "none_none_array_query-task": entry.query, "exact": true, "where_find": ["name"]]]
}
private func durableWrite(_ data: Data, to url: URL) throws {
    let directory = url.deletingLastPathComponent()
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let temporary = directory.appendingPathComponent(".write-\(UUID().uuidString).tmp")
    let fd = Darwin.open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
    guard fd >= 0 else { throw ProbeError.storage }
    defer { Darwin.close(fd); Darwin.unlink(temporary.path) }
    try data.withUnsafeBytes { buffer in
        var offset = 0
        while offset < buffer.count {
            let written = Darwin.write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
            if written < 0 && errno == EINTR { continue }
            guard written > 0 else { throw ProbeError.storage }; offset += written
        }
    }
    guard fsync(fd) == 0, Darwin.rename(temporary.path, url.path) == 0 else { throw ProbeError.storage }
    let parent = Darwin.open(directory.path, O_RDONLY | O_DIRECTORY)
    guard parent >= 0 else { throw ProbeError.storage }
    defer { Darwin.close(parent) }
    guard fsync(parent) == 0 else { throw ProbeError.storage }
}
private func saveJournal(_ journal: Journal, _ options: Options) throws {
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    try durableWrite(encoder.encode(journal), to: options.directory.appendingPathComponent("journal.json"))
}
private func loadJournal(_ options: Options, companies: [CompanyDossier]) throws -> Journal {
    let file = options.directory.appendingPathComponent("journal.json")
    guard FileManager.default.fileExists(atPath: file.path) else {
        return Journal(entries: companies.map { Entry(companyID: $0.id, companyName: $0.name, query: $0.name,
                                                      customName: "TG Signals · \(planID) · \($0.id)") })
    }
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    guard let bytes = try? Data(contentsOf: file), bytes.count <= 2_000_000,
          let journal = try? decoder.decode(Journal.self, from: bytes), journal.version == 1,
          journal.planID == planID, journal.projectID == projectID, journal.taskModule == taskModule,
          journal.entries.count == companies.count,
          Set(journal.entries.map(\.companyID)).count == companies.count,
          Set(journal.entries.map(\.companyID)) == Set(companies.map(\.id)) else { throw BatchError.journal }
    let names = Dictionary(uniqueKeysWithValues: companies.map { ($0.id, $0.name) })
    for entry in journal.entries {
        guard entry.companyName == names[entry.companyID], entry.query == entry.companyName,
              entry.customName == "TG Signals · \(planID) · \(entry.companyID)" ||
                (entry.reusedPilot && entry.customName == "TG Signals · vk-company-pilot-v1 · \(entry.companyID)"),
              ["planned", "creation_started", "created", "result_received", "imported", "failed"].contains(entry.state),
              entry.taskID.map(validID) ?? true,
              !["created", "result_received", "imported"].contains(entry.state) || entry.taskID != nil,
              !["result_received", "imported"].contains(entry.state) || entry.resultResponse != nil,
              entry.state != "imported" || entry.nativeRunID != nil,
              entry.resultResponse.map({ $0.hasPrefix("/") }) ?? true,
              entry.createResponse.map({ $0.hasPrefix("/") }) ?? true else { throw BatchError.journal }
    }
    let ids = journal.entries.compactMap(\.taskID)
    guard Set(ids).count == ids.count else { throw BatchError.journal }
    return journal
}
private func saveReceipt(_ result: [String: Any], tool: String, args: [String: Any], options: Options) throws -> URL {
    let file = options.directory.appendingPathComponent("receipts/\(tool)-\(UUID().uuidString.lowercased()).json")
    let receipt: [String: Any] = ["version": 1, "provider": "targetHunter", "operation": tool, "arguments": args,
                                "retrievedAt": ISO8601DateFormatter().string(from: Date()), "result": result]
    try durableWrite(JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]), to: file)
    return file
}
private func receiptBody(_ path: String, operation: String, taskID: String? = nil) throws -> ([String: Any], Date) {
    let value = try object(URL(fileURLWithPath: path))
    guard value["provider"] as? String == "targetHunter", value["operation"] as? String == operation,
          let args = value["arguments"] as? [String: Any], args["project_id"] as? String == projectID,
          taskID == nil || args["task_id"] as? String == taskID,
          let dateText = value["retrievedAt"] as? String, let date = ISO8601DateFormatter().date(from: dateText),
          let result = value["result"] as? [String: Any] else { throw BatchError.receipt }
    return (result, date)
}
private func adoptPilots(_ journal: inout Journal, options: Options) throws {
    let file = options.pilotDirectory.appendingPathComponent("pilot-journal.json")
    guard FileManager.default.fileExists(atPath: file.path) else { return }
    let pilot = try object(file)
    guard pilot["planID"] as? String == "vk-company-pilot-v1", pilot["projectID"] as? String == projectID,
          pilot["taskModule"] as? String == taskModule, let entries = pilot["entries"] as? [[String: Any]] else { throw BatchError.receipt }
    for value in entries {
        guard let companyID = value["companyID"] as? String, let index = journal.entries.firstIndex(where: { $0.companyID == companyID }),
              value["query"] as? String == journal.entries[index].query else { throw BatchError.receipt }
        guard journal.entries[index].state == "planned" else { continue }
        guard let id = value["taskID"] as? String, validID(id), let create = value["createResponse"] as? String,
              create == URL(fileURLWithPath: create).lastPathComponent else { throw BatchError.unresolved }
        let customName = "TG Signals · vk-company-pilot-v1 · \(companyID)"
        let createPath = options.pilotDirectory.appendingPathComponent(create).path
        let receipt = try object(URL(fileURLWithPath: createPath))
        guard let args = receipt["arguments"] as? [String: Any], args["custom_name"] as? String == customName,
              args["project_id"] as? String == projectID, args["task_module"] as? String == taskModule,
              (args["params"] as? [String: Any])?["none_none_array_query-task"] as? String == journal.entries[index].query,
              let result = receipt["result"] as? [String: Any], try createdTaskID(result, customName: customName) == id else { throw BatchError.receipt }
        journal.entries[index].taskID = id; journal.entries[index].customName = customName
        journal.entries[index].createResponse = createPath; journal.entries[index].reusedPilot = true
        journal.entries[index].state = "created"
        if let name = value["resultResponse"] as? String {
            guard name == URL(fileURLWithPath: name).lastPathComponent else { throw BatchError.receipt }
            let path = options.pilotDirectory.appendingPathComponent(name).path
            _ = try receiptBody(path, operation: "vk_task_result_get", taskID: id)
            journal.entries[index].resultResponse = path
            journal.entries[index].state = "result_received"
            journal.entries[index].reportedResultCount = value["reportedResultCount"] as? Int
        }
    }
}
private func coverage(_ journal: Journal, selected: Set<String>, running: Bool) -> [String: Any] {
    let entries = journal.entries.filter { selected.contains($0.companyID) }
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    let objects = entries.map { entry -> [String: Any] in
        var object = (try? JSONSerialization.jsonObject(with: encoder.encode(entry)) as? [String: Any]) ?? [:]
        object.removeValue(forKey: "createResponse"); object.removeValue(forKey: "resultResponse")
        return object
    }
    return ["version": 1, "planID": planID, "updatedAt": ISO8601DateFormatter().string(from: Date()),
            "totalCompanies": journal.entries.count, "selectedCompanies": entries.count,
            "created": entries.filter { $0.taskID != nil }.count,
            "completed": entries.filter { ["result_received", "imported"].contains($0.state) }.count,
            "imported": entries.filter { $0.state == "imported" }.count,
            "partial": entries.filter { $0.resultExtent == "partial" }.count,
            "zeroResults": entries.filter { $0.receivedResultCount == 0 }.count,
            "pending": entries.filter { $0.state == "created" }.count,
            "planned": entries.filter { $0.state == "planned" }.count,
            "failed": entries.filter { $0.state == "failed" }.count,
            "uncertain": entries.filter { $0.state == "creation_started" }.count,
            "receivedResults": entries.compactMap(\.receivedResultCount).reduce(0, +),
            "reportedResults": entries.compactMap(\.reportedResultCount).reduce(0, +),
            "running": running, "companies": objects]
}
private func saveCoverage(_ journal: Journal, options: Options, selected: Set<String>, running: Bool) throws {
    let value = coverage(journal, selected: selected, running: running)
    try durableWrite(JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]),
                     to: options.directory.appendingPathComponent("coverage.json"))
    var compact = value; compact.removeValue(forKey: "companies"); compact["event"] = "progress"
    emit(compact)
}
private final class BatchLock {
    let fd: Int32
    init(_ directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        fd = Darwin.open(directory.appendingPathComponent(".targethunter-batch.lock").path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw ProbeError.storage }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { Darwin.close(fd); throw BatchError.locked }
    }
    deinit { flock(fd, LOCK_UN); Darwin.close(fd) }
}
private func importReceived(_ journal: inout Journal, options: Options, selected: Set<String>) throws {
    let store = SourceCollectionStore(directoryURL: options.dataDirectory.appendingPathComponent("source-runs", isDirectory: true))
    for index in journal.entries.indices where selected.contains(journal.entries[index].companyID) && journal.entries[index].state == "result_received" {
        let entry = journal.entries[index]
        guard let path = entry.resultResponse, let taskID = entry.taskID else { throw BatchError.journal }
        let (_, date) = try receiptBody(path, operation: "vk_task_result_get", taskID: taskID)
        do {
            let request = SourceSearchRequest(companyID: entry.companyID, companyName: entry.companyName, query: entry.query, limit: 1000)
            let run = try TargetHunterResultImporter.makeRun(request: request, taskID: taskID, moduleID: taskModule,
                        reportedCount: entry.reportedResultCount, responseData: Data(contentsOf: URL(fileURLWithPath: path)), retrievedAt: date)
            try store.save(run)
            // Store.save unions repeated exports by community ID. Report the retained union,
            // not the size of the latest response, so a smaller repeat cannot regress coverage.
            let retained = try SourceCollectionStore.decode(Data(contentsOf: store.directoryURL.appendingPathComponent(run.id + ".json")))
            journal.entries[index].receivedResultCount = retained.results.count
            journal.entries[index].reportedResultCount = retained.targetHunterTask?.reportedCount
            journal.entries[index].resultExtent = retained.targetHunterTask?.completeness.rawValue ?? "unknown"
            journal.entries[index].nativeRunID = run.id
            journal.entries[index].completedAt = Date()
            journal.entries[index].state = "imported"
            journal.updatedAt = Date(); try saveJournal(journal, options)
            emit(["event": "imported", "companyID": entry.companyID, "taskID": taskID, "runID": run.id,
                  "received": retained.results.count, "reported": journal.entries[index].reportedResultCount as Any? ?? NSNull(),
                  "extent": journal.entries[index].resultExtent!])
        } catch { throw BatchError.importFailure }
    }
}
private func retryableRead(_ error: Error) -> Bool {
    switch error {
    case ProbeError.network: return true
    case ProbeError.http(let status): return status == 429 || (500...599).contains(status)
    default: return false
    }
}
private func readCall(_ client: BatchMCP, name: String, args: [String: Any], rpcID: inout Int) async throws -> [String: Any] {
    guard ["vk_tasks_list", "vk_task_result_get"].contains(name) else { throw ProbeError.schema }
    for attempt in 0...2 {
        rpcID += 1
        do { return try await client.call(name, arguments: args, id: rpcID) }
        catch {
            guard attempt < 2, retryableRead(error) else { throw error }
            emit(["event": "read_backoff", "operation": name, "seconds": 2 * (attempt + 1)])
            try await Task.sleep(nanoseconds: UInt64(2 * (attempt + 1)) * 1_000_000_000)
        }
    }
    throw ProbeError.network
}
private func statusText(_ task: [String: Any]) -> String? {
    if let value = task["status"] as? String { return value }
    if let value = task["status"] as? Int { return "numeric:\(value)" }
    return nil
}
private func readyForRead(_ task: [String: Any]) -> Bool {
    // Numeric 10 was observed with an end timestamp and a separately successful export.
    // It is a read-eligibility check, not a claim that the export succeeded.
    if task["status"] as? String == "SUCCESS" { return true }
    return task["status"] as? Int == 10 && (task["end_at"] as? Int ?? 0) > 0
}
private let exactRateLimitMessage = "Превышен лимит запросов к API. Подождите минуту и повторите."
private func exactRateLimitRejection(_ result: [String: Any]) -> Bool {
    guard result["isError"] as? Bool == true,
          Set(result.keys).isSubset(of: ["content", "isError"]),
          let content = result["content"] as? [[String: Any]], content.count == 1,
          content[0]["type"] as? String == "text",
          let text = content[0]["text"] as? String else { return false }
    return text.trimmingCharacters(in: .whitespacesAndNewlines) == exactRateLimitMessage
}
private func rateLimitReceipt(_ entry: Entry) throws -> Date {
    guard entry.state == "failed", entry.taskID == nil,
          ["provider_rejected_create", "provider_rate_limited_create"].contains(entry.errorCode ?? ""),
          let path = entry.createResponse else { throw BatchError.rateLimitRecovery }
    let receipt = try object(URL(fileURLWithPath: path))
    guard receipt["provider"] as? String == "targetHunter", receipt["operation"] as? String == "vk_task_create",
          let arguments = receipt["arguments"] as? [String: Any], NSDictionary(dictionary: arguments).isEqual(to: publicQuery(entry)),
          let result = receipt["result"] as? [String: Any], exactRateLimitRejection(result),
          let stamp = receipt["retrievedAt"] as? String, let date = ISO8601DateFormatter().date(from: stamp),
          date.timeIntervalSinceNow <= 60 else { throw BatchError.rateLimitRecovery }
    return date
}
private func rateLimitedIndices(_ journal: Journal, selected: Set<String>) -> [Int] {
    journal.entries.indices.filter { index in
        selected.contains(journal.entries[index].companyID) && (try? rateLimitReceipt(journal.entries[index])) != nil
    }
}
private func reconcileRateLimited(_ journal: inout Journal, indices: [Int], tasks: [[String: Any]]) throws {
    // The caller must obtain this complete list AFTER the rejection and before any retry.
    // Every task name must be readable before absence is treated as evidence.
    guard tasks.allSatisfy({ $0["name"] is String || $0["custom_name"] is String }) else { throw BatchError.rateLimitRecovery }
    for index in indices {
        let entry = journal.entries[index]
        _ = try rateLimitReceipt(entry)
        let matches = tasks.filter { $0["name"] as? String == entry.customName || $0["custom_name"] as? String == entry.customName }
        guard matches.count <= 1 else { throw BatchError.rateLimitRecovery }
        var history = entry.rateLimitRecoveryHistory ?? []
        if let receipt = entry.createResponse, !history.contains(receipt) { history.append(receipt) }
        journal.entries[index].rateLimitRecoveryHistory = history
        journal.entries[index].rateLimitRecoveryCount = (entry.rateLimitRecoveryCount ?? 0) + 1
        journal.entries[index].errorCode = nil
        if let task = matches.first {
            guard let id = responseTaskID(task), task["project_id"] as? String == projectID,
                  task["module"] as? String == taskModule,
                  !journal.entries.enumerated().contains(where: { $0.offset != index && $0.element.taskID == id }) else { throw BatchError.rateLimitRecovery }
            journal.entries[index].taskID = id
            journal.entries[index].state = "created"
            if let failure = terminalFailureCode(task) {
                journal.entries[index].state = "failed"; journal.entries[index].errorCode = failure
            }
            emit(["event": "rate_limit_reconciled_existing", "companyID": entry.companyID, "taskID": id, "newCreate": false])
        } else {
            journal.entries[index].state = "planned"
            emit(["event": "rate_limit_retry_authorized", "companyID": entry.companyID,
                  "reason": "exact_provider_rejection_and_fresh_task_absence", "previousReceipt": entry.createResponse!])
        }
    }
}
private func finishSummary(_ journal: Journal, selected: Set<String>) throws {
    let entries = journal.entries.filter { selected.contains($0.companyID) }
    let failed = entries.filter { $0.state == "failed" }.count
    let imported = entries.filter { $0.state == "imported" }.count
    let complete = imported == entries.count && failed == 0
    emit(["event": "finished", "complete": complete, "selectedCompanies": entries.count,
          "imported": imported, "failed": failed, "notStarted": entries.filter { $0.state == "planned" }.count,
          "status": complete ? "complete" : (failed > 0 ? "failed_companies" : "bounded_pass_incomplete")])
    if failed > 0 { throw BatchError.failedCompanies }
}
private func terminalFailureCode(_ task: [String: Any]) -> String? {
    if task["status"] as? Int == 9 || task["status"] as? String == "FAIL" { return "remote_task_failed" }
    if (task["end_at"] as? Int ?? 0) > 0 && !readyForRead(task) { return "remote_terminal_status_unrecognized" }
    return nil
}
private func schedulePartialRefresh(_ journal: inout Journal, selected: Set<String>) throws -> Int {
    var count = 0
    for index in journal.entries.indices where selected.contains(journal.entries[index].companyID) &&
            journal.entries[index].state == "imported" && journal.entries[index].resultExtent == "partial" {
        guard journal.entries[index].taskID != nil, let previous = journal.entries[index].resultResponse,
              journal.entries[index].nativeRunID != nil else { throw BatchError.journal }
        var history = journal.entries[index].resultHistory ?? []
        if !history.contains(previous) { history.append(previous) }
        journal.entries[index].resultHistory = history
        journal.entries[index].refreshCount = (journal.entries[index].refreshCount ?? 0) + 1
        journal.entries[index].state = "created" // read existing task only, never a new create
        journal.entries[index].errorCode = nil
        count += 1
    }
    return count
}
private func enforcePollLimit(pollCount: Int, maximum: Int, pending: Int, remaining: Int, newCount: Int) throws {
    guard pollCount >= maximum, pending > 0 else { return }
    emit(["event": "incomplete", "reason": "poll_limit", "pending": pending, "notStarted": remaining,
          "newThisRun": newCount, "complete": false, "resumeWithoutCreateRetry": true])
    throw BatchError.incomplete
}
@main
private struct TargetHunterBatch {
    static func main() async {
        do { let options = try Options(Array(CommandLine.arguments.dropFirst())); if options.selfTest { try offlineTests(); return }; try await run(options) }
        catch {
            let message = (error as? ProbeError)?.localizedDescription ?? (error as? BatchError)?.localizedDescription ?? "TargetHunter operation failed. Saved state retained; no create retry."
            emit(["event": "stopped", "message": message, "automaticCreateRetry": false]); Darwin.exit(1)
        }
    }
    static func run(_ options: Options) async throws {
        try validateSchemas(options)
        let all = try CompanyCatalog.loadBundled().companies.filter { !$0.isDemo }
        guard all.count == 409, Set(all.map(\.id)).count == all.count, all.allSatisfy({ !$0.name.isEmpty && $0.name.count <= 400 }),
              pilotIDs.allSatisfy({ id in all.contains { $0.id == id } }) else { throw BatchError.catalog }
        let ordered = pilotIDs.compactMap { id in all.first { $0.id == id } } + all.filter { !pilotIDs.contains($0.id) }
        let selected = Set((options.scope == "pilot" ? ordered.filter { pilotIDs.contains($0.id) } : ordered).map(\.id))
        let lock = options.execute ? try BatchLock(options.directory) : nil
        defer { withExtendedLifetime(lock) {} }
        var journal = try loadJournal(options, companies: ordered)
        try adoptPilots(&journal, options: options)
        let planned = journal.entries.filter { selected.contains($0.companyID) && $0.state == "planned" }
        emit(["event": options.execute ? "plan_execute" : "plan_dry_run", "catalogCompanies": all.count, "selectedCompanies": selected.count,
              "reusedPilotTasks": journal.entries.filter(\.reusedPilot).count, "maximumNewTasks": min(options.maxNew, planned.count + (options.retryRateLimited ? rateLimitedIndices(journal, selected: selected).count : 0)),
              "inFlightTaskWindow": options.window, "readRetries": 2, "createRetries": 0, "automaticPayment": false,
              "exportMode": options.exportMode, "importIntoApp": true, "networkStarted": false,
              "refreshPartialExports": options.refreshPartials, "retryRateLimited": options.retryRateLimited,
              "httpMinimumIntervalMS": options.httpIntervalMS,
              "explicitRateLimitRecoveries": options.retryRateLimited ? rateLimitedIndices(journal, selected: selected).count : 0,
              "refreshableTasks": journal.entries.filter { selected.contains($0.companyID) && ($0.resultExtent == "partial" || ($0.reusedPilot && $0.state == "result_received")) }.count,
              "preview": planned.prefix(5).map { ["companyID": $0.companyID, "query": $0.query] }])
        guard options.execute else { return }
        try saveJournal(journal, options)
        defer { try? saveCoverage(journal, options: options, selected: selected, running: false) }
        try importReceived(&journal, options: options, selected: selected)
        if options.refreshPartials {
            let count = try schedulePartialRefresh(&journal, selected: selected)
            journal.updatedAt = Date(); try saveJournal(journal, options)
            emit(["event": "partial_refresh_scheduled", "existingTasks": count, "newTasksCreated": 0])
        }
        try saveCoverage(journal, options: options, selected: selected, running: true)
        let recoverable = options.retryRateLimited ? rateLimitedIndices(journal, selected: selected) : []
        let hasWork = !recoverable.isEmpty || journal.entries.contains { selected.contains($0.companyID) && (["created", "creation_started"].contains($0.state) || ($0.state == "planned" && options.maxNew > 0)) }
        guard hasWork else { try finishSummary(journal, selected: selected); return }
        // The service explicitly asks for a minute. This delay applies only to a verified,
        // rejected create and never turns a timeout/unknown outcome into a retry.
        if let latest = try recoverable.map({ try rateLimitReceipt(journal.entries[$0]) }).max() {
            let wait = max(0, 60 - Date().timeIntervalSince(latest))
            if wait > 0 {
                emit(["event": "rate_limit_cooldown", "seconds": Int(ceil(wait))])
                try await Task.sleep(nanoseconds: UInt64(min(60, wait) * 1_000_000_000))
            }
        }
        let client = BatchMCP(key: try credential(), minimumIntervalMS: options.httpIntervalMS); try await client.start()
        var rpcID = 1, newCount = 0, pollCount = 0
        // Always inspect existing remote tasks before any create. An interrupted create can be
        // reconciled only to exactly one matching task; absence never permits an automatic retry.
        var tasks = try await refreshTasks(client, options: options, journal: &journal, rpcID: &rpcID)
        if !recoverable.isEmpty { try reconcileRateLimited(&journal, indices: recoverable, tasks: tasks) }
        try reconcileExisting(&journal, tasks: tasks, selected: selected)
        try saveJournal(journal, options)
        if journal.entries.contains(where: { selected.contains($0.companyID) && $0.state == "creation_started" }) { throw BatchError.unresolved }
        while true {
            try await exportReady(client, options: options, journal: &journal, selected: selected, tasks: tasks, rpcID: &rpcID)
            try importReceived(&journal, options: options, selected: selected)
            try saveCoverage(journal, options: options, selected: selected, running: true)
            var inFlight = journal.entries.filter { selected.contains($0.companyID) && $0.state == "created" }.count
            let canCreate = max(0, min(options.window - inFlight, options.maxNew - newCount))
            let indices = journal.entries.indices.filter { selected.contains(journal.entries[$0].companyID) && journal.entries[$0].state == "planned" }.prefix(canCreate)
            for index in indices {
                journal.entries[index].state = "creation_started"; journal.entries[index].startedAt = Date()
                journal.updatedAt = Date(); try saveJournal(journal, options) // fsync BEFORE create
                try saveCoverage(journal, options: options, selected: selected, running: true)
                let args = publicQuery(journal.entries[index]); rpcID += 1
                do {
                    let result = try await client.call("vk_task_create", arguments: args, id: rpcID) // Never retried.
                    let receipt = try saveReceipt(result, tool: "vk_task_create", args: args, options: options)
                    journal.entries[index].createResponse = receipt.path; try saveJournal(journal, options)
                    do {
                        let id = try createdTaskID(result, customName: journal.entries[index].customName)
                        guard !journal.entries.contains(where: { $0.taskID == id }) else { throw BatchError.unresolved }
                        journal.entries[index].taskID = id; journal.entries[index].state = "created"
                        journal.updatedAt = Date(); try saveJournal(journal, options) // persist task ID before next create
                        newCount += 1; inFlight += 1
                        emit(["event": "created", "companyID": journal.entries[index].companyID, "taskID": id, "newThisRun": newCount])
                    } catch PilotError.upstreamTool {
                        journal.entries[index].state = "failed"
                        journal.entries[index].errorCode = exactRateLimitRejection(result) ? "provider_rate_limited_create" : "provider_rejected_create"
                        try saveJournal(journal, options); throw PilotError.upstreamTool
                    }
                } catch {
                    if journal.entries[index].state == "creation_started" { journal.entries[index].errorCode = "create_outcome_uncertain"; try? saveJournal(journal, options) }
                    throw error
                }
                try await Task.sleep(nanoseconds: options.delayMS * 1_000_000)
            }
            let remaining = journal.entries.filter { selected.contains($0.companyID) && $0.state == "planned" }.count
            if inFlight == 0 && (remaining == 0 || newCount >= options.maxNew) { break }
            try enforcePollLimit(pollCount: pollCount, maximum: options.maxPolls, pending: inFlight, remaining: remaining, newCount: newCount)
            try await Task.sleep(nanoseconds: options.pollMS * 1_000_000)
            tasks = try await refreshTasks(client, options: options, journal: &journal, rpcID: &rpcID)
            pollCount += 1
        }
        try finishSummary(journal, selected: selected)
    }
    static func refreshTasks(_ client: BatchMCP, options: Options, journal: inout Journal, rpcID: inout Int) async throws -> [[String: Any]] {
        let args: [String: Any] = ["project_id": projectID]
        let result = try await readCall(client, name: "vk_tasks_list", args: args, rpcID: &rpcID)
        let file = try saveReceipt(result, tool: "vk_tasks_list", args: args, options: options)
        let tasks = try taskList(result)
        for index in journal.entries.indices {
            guard let id = journal.entries[index].taskID else { continue }
            let found = tasks.filter { responseTaskID($0) == id }
            guard found.count <= 1 else { throw BatchError.receipt }
            guard let task = found.first else { continue }
            guard task["project_id"] as? String == projectID, task["module"] as? String == taskModule,
                  task["name"] as? String == journal.entries[index].customName else { throw BatchError.receipt }
            journal.entries[index].lastStatus = statusText(task)
            // Provider task_item.js maps numeric 9 to FAIL (verified by the operator).
            // A failed remote task is an observed failure, never a zero-result export.
            if journal.entries[index].state == "created", let failure = terminalFailureCode(task) {
                journal.entries[index].state = "failed"
                journal.entries[index].errorCode = failure
                journal.entries[index].completedAt = Date()
                emit(["event": "remote_task_failed", "companyID": journal.entries[index].companyID, "taskID": id, "reason": failure])
            }
            if let count = task["result_count"] as? Int, count >= 0 {
                journal.entries[index].reportedResultCount = count; journal.entries[index].reportedCountResponse = file.path
            }
        }
        journal.updatedAt = Date(); try saveJournal(journal, options)
        return tasks
    }
    static func reconcileExisting(_ journal: inout Journal, tasks: [[String: Any]], selected: Set<String>) throws {
        for index in journal.entries.indices where selected.contains(journal.entries[index].companyID) && ["planned", "creation_started"].contains(journal.entries[index].state) {
            let entry = journal.entries[index]
            let found = tasks.filter { $0["name"] as? String == entry.customName || $0["custom_name"] as? String == entry.customName }
            guard found.count <= 1 else { throw BatchError.unresolved }
            guard let task = found.first else { continue }
            guard let id = responseTaskID(task), task["project_id"] as? String == projectID,
                  task["module"] as? String == taskModule else { throw BatchError.unresolved }
            journal.entries[index].taskID = id; journal.entries[index].state = "created"
            journal.entries[index].errorCode = nil; journal.entries[index].lastStatus = statusText(task)
            // Provider task_item.js maps numeric 9 to FAIL (verified by the operator).
            // A failed remote task is an observed failure, never a zero-result export.
            if journal.entries[index].state == "created", let failure = terminalFailureCode(task) {
                journal.entries[index].state = "failed"
                journal.entries[index].errorCode = failure
                journal.entries[index].completedAt = Date()
                emit(["event": "remote_task_failed", "companyID": journal.entries[index].companyID, "taskID": id, "reason": failure])
            }
            if let count = task["result_count"] as? Int, count >= 0 { journal.entries[index].reportedResultCount = count }
            emit(["event": "reconciled_existing", "companyID": entry.companyID, "taskID": id, "newCreate": false])
        }
    }
    static func exportReady(_ client: BatchMCP, options: Options, journal: inout Journal, selected: Set<String>, tasks: [[String: Any]], rpcID: inout Int) async throws {
        for index in journal.entries.indices where selected.contains(journal.entries[index].companyID) && journal.entries[index].state == "created" {
            guard let id = journal.entries[index].taskID, let task = tasks.first(where: { responseTaskID($0) == id }), readyForRead(task) else { continue }
            let args: [String: Any] = ["project_id": projectID, "task_id": id]
            let result = try await readCall(client, name: "vk_task_result_get", args: args, rpcID: &rpcID)
            let file = try saveReceipt(result, tool: "vk_task_result_get", args: args, options: options)
            // Save even a provider error; never silently label it as a zero-result company.
            guard result["isError"] as? Bool != true, let body = payload(result), body["status"] as? Bool == true,
                  body["type"] as? String == "vk_groups_array_object", let values = body["data"] as? [[String: Any]] else {
                journal.entries[index].lastResultReadError = file.path; journal.entries[index].errorCode = "export_rejected_or_unsupported"
                journal.entries[index].state = "failed"; journal.entries[index].completedAt = Date()
                try saveJournal(journal, options)
                emit(["event": "export_failed", "companyID": journal.entries[index].companyID, "taskID": id, "response": file.path])
                continue
            }
            journal.entries[index].resultResponse = file.path; journal.entries[index].receivedResultCount = values.count
            journal.entries[index].state = "result_received"; journal.entries[index].errorCode = nil
            journal.updatedAt = Date(); try saveJournal(journal, options)
            emit(["event": "export_received", "companyID": journal.entries[index].companyID, "taskID": id, "received": values.count])
        }
    }
    static func offlineTests() throws {
        let id = "6acb09bc42b2d3532942b7a2", custom = "TG Signals · vk-company-batch-v1 · test"
        let success: [String: Any] = ["structuredContent": ["status": "msg", "type": "success", "task": ["id": id, "project_id": projectID, "module": taskModule, "name": custom]]]
        guard try createdTaskID(success, customName: custom) == id else { throw BatchError.selfTest }
        do { _ = try createdTaskID(success, customName: "wrong"); throw BatchError.selfTest } catch PilotError.unrecognizedCreateResponse {}
        guard !readyForRead(["status": 10]), readyForRead(["status": 10, "end_at": 1]),
              !readyForRead(["status": 0, "end_at": 1]), retryableRead(ProbeError.http(429)), !retryableRead(ProbeError.http(403)) else { throw BatchError.selfTest }
        var journal = Journal(entries: [Entry(companyID: "test", companyName: "Test", query: "Test", customName: custom, state: "creation_started")])
        try reconcileExisting(&journal, tasks: [], selected: ["test"])
        guard journal.entries[0].state == "creation_started" else { throw BatchError.selfTest }
        let task: [String: Any] = ["id": id, "name": custom, "project_id": projectID, "module": taskModule]
        try reconcileExisting(&journal, tasks: [task], selected: ["test"])
        guard journal.entries[0].state == "created", journal.entries[0].taskID == id else { throw BatchError.selfTest }
        var dup = Journal(entries: [Entry(companyID: "test", companyName: "Test", query: "Test", customName: custom)])
        do { try reconcileExisting(&dup, tasks: [task, task], selected: ["test"]); throw BatchError.selfTest } catch BatchError.unresolved {}
        let value = coverage(journal, selected: ["test"], running: false)
        guard value["pending"] as? Int == 1, value["completed"] as? Int == 0 else { throw BatchError.selfTest }
        guard terminalFailureCode(["status": 7, "end_at": 1]) == "remote_terminal_status_unrecognized",
              terminalFailureCode(["status": 9]) == "remote_task_failed",
              terminalFailureCode(["status": 10, "end_at": 1]) == nil,
              terminalFailureCode(["status": 1]) == nil else { throw BatchError.selfTest }
        do { try enforcePollLimit(pollCount: 1, maximum: 1, pending: 2, remaining: 10, newCount: 2); throw BatchError.selfTest }
        catch BatchError.incomplete {}
        try enforcePollLimit(pollCount: 1, maximum: 1, pending: 0, remaining: 0, newCount: 2)
        var refresh = Journal(entries: [Entry(companyID: "test", companyName: "Test", query: "Test", customName: custom,
                    state: "imported", taskID: id, resultResponse: "/old-receipt.json", receivedResultCount: 5,
                    reportedResultCount: 15, resultExtent: "partial", nativeRunID: "existing-run")])
        guard try schedulePartialRefresh(&refresh, selected: ["test"]) == 1,
              refresh.entries[0].taskID == id, refresh.entries[0].state == "created",
              refresh.entries[0].resultHistory == ["/old-receipt.json"], refresh.entries[0].nativeRunID == "existing-run",
              try schedulePartialRefresh(&refresh, selected: ["test"]) == 0 else { throw BatchError.selfTest }
        let rejected: [String: Any] = ["isError": true, "content": [["type": "text", "text": exactRateLimitMessage]]]
        guard exactRateLimitRejection(rejected),
              !exactRateLimitRejection(["isError": true, "content": [["type": "text", "text": "timeout"]]]),
              !exactRateLimitRejection(["isError": false, "content": [["type": "text", "text": exactRateLimitMessage]]]),
              !exactRateLimitRejection(["isError": true, "structuredContent": ["task_id": id], "content": [["type": "text", "text": exactRateLimitMessage]]]) else { throw BatchError.selfTest }
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent("th-recovery-tests-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: temp) }
        let receiptURL = temp.appendingPathComponent("rejected.json")
        var rejectedEntry = Entry(companyID: "test", companyName: "Test", query: "Test", customName: custom,
                                  state: "failed", createResponse: receiptURL.path, errorCode: "provider_rejected_create")
        let receipt: [String: Any] = ["provider": "targetHunter", "operation": "vk_task_create", "arguments": publicQuery(rejectedEntry),
                                     "retrievedAt": ISO8601DateFormatter().string(from: Date()), "result": rejected]
        try durableWrite(JSONSerialization.data(withJSONObject: receipt), to: receiptURL)
        var retry = Journal(entries: [rejectedEntry])
        guard rateLimitedIndices(retry, selected: ["test"]) == [0] else { throw BatchError.selfTest }
        try reconcileRateLimited(&retry, indices: [0], tasks: [])
        guard retry.entries[0].state == "planned", retry.entries[0].taskID == nil,
              retry.entries[0].rateLimitRecoveryHistory == [receiptURL.path], retry.entries[0].rateLimitRecoveryCount == 1 else { throw BatchError.selfTest }
        retry = Journal(entries: [rejectedEntry])
        try reconcileRateLimited(&retry, indices: [0], tasks: [task])
        guard retry.entries[0].state == "created", retry.entries[0].taskID == id else { throw BatchError.selfTest }
        retry = Journal(entries: [rejectedEntry])
        do { try reconcileRateLimited(&retry, indices: [0], tasks: [task, task]); throw BatchError.selfTest } catch BatchError.rateLimitRecovery {}
        rejectedEntry.state = "creation_started"
        guard rateLimitedIndices(Journal(entries: [rejectedEntry]), selected: ["test"]).isEmpty else { throw BatchError.selfTest }
        let failedJournal = Journal(entries: [Entry(companyID: "test", companyName: "Test", query: "Test", customName: custom, state: "failed")])
        do { try finishSummary(failedJournal, selected: ["test"]); throw BatchError.selfTest } catch BatchError.failedCompanies {}
        emit(["event": "offline_tests_passed", "assertions": 36, "networkStarted": false, "credentialsRead": false])
    }
}
