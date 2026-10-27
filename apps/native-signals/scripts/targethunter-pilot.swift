import Foundation
import Darwin

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

private let projectID = "6acaf3c667052cdb2f0e9e2f"
private let taskModule = "vk/2"
private let planID = "vk-company-pilot-v1"
private let pilots = [
    (id: "brunoyam", name: "Бруноям"),
    (id: "karpov", name: "karpov.courses"),
    (id: "setters", name: "SETTERS Education")
]

private struct PilotOptions {
    var execute = false
    var pollOnce = false
    var resultOnce: String?
    var maxNew = 1
    var schemaRun: URL?
    var moduleRun: URL?
    var outputDirectory: URL?
    var recoverFrom: URL?
    var reconcileCreate: URL?
    var confirmVKReconnected = false
    init(_ args: [String]) throws {
        var i = 0
        func value() throws -> String { i += 1; guard i < args.count else { throw ProbeError.usage }; return args[i] }
        func path() throws -> URL { let item = try value(); guard item.hasPrefix("/") else { throw ProbeError.usage }; return URL(fileURLWithPath: item) }
        while i < args.count {
            switch args[i] {
            case "--execute": execute = true
            case "--poll-once": pollOnce = true
            case "--result-once": resultOnce = try value()
            case "--max-new": guard let n = Int(try value()), (1...3).contains(n) else { throw ProbeError.usage }; maxNew = n
            case "--schema-run": schemaRun = try path()
            case "--module-run": moduleRun = try path()
            case "--output-dir": outputDirectory = try path()
            case "--recover-from": recoverFrom = try path()
            case "--reconcile-create": reconcileCreate = try path()
            case "--confirm-vk-reconnected": confirmVKReconnected = true
            default: throw ProbeError.usage
            }
            i += 1
        }
        guard schemaRun != nil, moduleRun != nil, outputDirectory != nil else { throw ProbeError.usage }
        guard recoverFrom == nil || (!pollOnce && maxNew == 1) else { throw ProbeError.usage }
        guard !confirmVKReconnected || recoverFrom != nil else { throw ProbeError.usage }
        guard reconcileCreate == nil || (recoverFrom == nil && !pollOnce && maxNew == 1) else { throw ProbeError.usage }
        guard resultOnce == nil || (validID(resultOnce!) && !pollOnce && recoverFrom == nil && reconcileCreate == nil && maxNew == 1) else { throw ProbeError.usage }
    }
}

private struct PilotEntry: Codable {
    let companyID: String
    let companyName: String
    let query: String
    let customName: String
    let startedAt: Date
    var state: String
    var taskID: String?
    var createResponse: String?
    var lastStatus: String?
    var resultResponse: String?
    var failure: String?
    var recoveryHistory: [String]?
    var recoveryCheckResponse: String?
    var recoveryCount: Int?
    var recoveryReason: String?
    var reconciliationHistory: [String]?
    var reconciledCreateResponse: String?
    var receivedResultCount: Int?
    var reportedResultCount: Int?
    var reportedCountResponse: String?
    var resultExtent: String?
}
private struct PilotJournal: Codable {
    var version = 1
    let planID: String
    let projectID: String
    let taskModule: String
    var entries: [PilotEntry]
}
private enum PilotError: LocalizedError {
    case journal, lock, unrecognizedCreateResponse, upstreamTool, unrecognizedTaskList, unresolvedAttempt, recoveryCheck, reconciliationCheck
    var errorDescription: String? {
        switch self {
        case .journal: return "Журнал пилота повреждён или несовместим. Исходный файл не изменён."
        case .lock: return "Другой процесс уже использует этот пилот."
        case .unrecognizedCreateResponse: return "Ответ создания сохранён, но task ID не распознан однозначно. Повторного создания не будет; нужен разбор ответа."
        case .upstreamTool: return "TargetHunter отказал в операции. Ответ сохранён; дальнейшее создание остановлено. Оплата не выполнялась."
        case .unrecognizedTaskList: return "Не удалось однозначно разобрать статус задачи. Ответ сохранён; получить результат пока нельзя."
        case .unresolvedAttempt: return "Есть попытка с неизвестным исходом или отказом. Новые задачи не создаются до разбора; автоматических повторов нет."
        case .recoveryCheck: return "Восстановление не разрешено: нужны свежий полный список задач, отказ обновления VK и допустимый номер попытки. Второе восстановление требует явного подтверждения рабочего подключения; дальнейшие повторы запрещены."
        case .reconciliationCheck: return "Привязка не разрешена: нужен сохранённый успешный ответ именно этой попытки с совпадающим запросом. Сетевые запросы не выполнялись."
        }
    }
}

private func publicQuery(_ pilot: (id: String, name: String)) -> [String: Any] {
    ["project_id": projectID, "task_module": taskModule,
     "custom_name": "TG Signals · \(planID) · \(pilot.id)",
     "params": ["input-type-task": "none_none_array_query", "input-from-task": "hand",
                "none_none_array_query-task": pilot.name, "exact": true, "where_find": ["name"]]]
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

private func recoveryMatch(_ url: URL, entry: PilotEntry, directory: URL, confirmVKReconnected: Bool) throws -> String? {
    guard url.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
          url.lastPathComponent.hasPrefix("vk_tasks_list-"), (entry.recoveryCount ?? 0) == (confirmVKReconnected ? 1 : 0),
          entry.state == "failed", entry.taskID == nil, let originalFile = entry.createResponse else { throw PilotError.recoveryCheck }
    let original = try object(directory.appendingPathComponent(originalFile))
    guard let originalResult = original["result"] as? [String: Any], let originalBody = payload(originalResult),
          originalBody["status"] as? String == "msg", originalBody["type"] as? String == "error",
          (originalBody["description"] as? String)?.contains("обновить подключение с ВКонтакте") == true else { throw PilotError.recoveryCheck }
    let record = try object(url)
    guard record["provider"] as? String == "targetHunter", record["operation"] as? String == "vk_tasks_list",
          (record["arguments"] as? [String: Any])?["project_id"] as? String == projectID,
          let dateText = record["retrievedAt"] as? String, let date = ISO8601DateFormatter().date(from: dateText),
          date >= entry.startedAt, Date().timeIntervalSince(date) >= -60, Date().timeIntervalSince(date) <= 900,
          let result = record["result"] as? [String: Any] else { throw PilotError.recoveryCheck }
    let tasks = try taskList(result)
    // Never infer absence if a task's naming format cannot be read.
    for task in tasks {
        guard task["custom_name"] is String || task["name"] is String else { throw PilotError.recoveryCheck }
    }
    let matches = tasks.filter { $0["custom_name"] as? String == entry.customName || $0["name"] as? String == entry.customName }
    guard matches.count <= 1 else { throw PilotError.recoveryCheck }
    guard let task = matches.first else { return nil }
    guard let id = responseTaskID(task) else { throw PilotError.recoveryCheck }
    return id
}
private func validatePilotSchemas(_ options: PilotOptions) throws {
    let run = try object(options.schemaRun!)
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
    let moduleRun = try object(options.moduleRun!)
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

private func loadJournal(_ directory: URL) throws -> PilotJournal {
    let url = directory.appendingPathComponent("pilot-journal.json")
    guard FileManager.default.fileExists(atPath: url.path) else { return PilotJournal(planID: planID, projectID: projectID, taskModule: taskModule, entries: []) }
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    guard let data = try? Data(contentsOf: url), data.count <= 100_000,
          let journal = try? decoder.decode(PilotJournal.self, from: data), journal.version == 1,
          journal.planID == planID, journal.projectID == projectID, journal.taskModule == taskModule,
          journal.entries.count <= 3, Set(journal.entries.map(\.companyID)).count == journal.entries.count else { throw PilotError.journal }
    for entry in journal.entries {
        guard let pilot = pilots.first(where: { $0.id == entry.companyID }), entry.companyName == pilot.name, entry.query == pilot.name,
              entry.customName == publicQuery(pilot)["custom_name"] as? String,
              ["creation_started", "created", "result_received", "complete", "failed"].contains(entry.state),
              entry.taskID.map(validID) ?? true,
              !["created", "result_received", "complete"].contains(entry.state) || entry.taskID != nil,
              entry.state != "result_received" || (entry.resultResponse != nil && entry.receivedResultCount != nil && entry.resultExtent != nil)
        else { throw PilotError.journal }
    }
    return journal
}
private func saveJournal(_ journal: PilotJournal, directory: URL) throws {
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    do {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("pilot-journal.json")
        try encoder.encode(journal).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    } catch { throw ProbeError.storage }
}
private func saveResult(_ result: [String: Any], tool: String, arguments: [String: Any], directory: URL) throws -> String {
    let url = directory.appendingPathComponent("\(tool)-\(UUID().uuidString.lowercased()).json")
    do {
        let value: [String: Any] = ["version": 1, "provider": "targetHunter", "operation": tool, "arguments": arguments,
                                    "retrievedAt": ISO8601DateFormatter().string(from: Date()), "result": result]
        try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return url.lastPathComponent
    } catch { throw ProbeError.storage }
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

private final class PilotLock {
    private let fd: Int32
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        fd = Darwin.open(directory.appendingPathComponent(".targethunter-pilot.lock").path, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else { throw ProbeError.storage }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { Darwin.close(fd); throw PilotError.lock }
    }
    deinit { flock(fd, LOCK_UN); Darwin.close(fd) }
}

@main
private struct TargetHunterPilot {
    static func main() async {
        do { try await run(PilotOptions(Array(CommandLine.arguments.dropFirst()))) }
        catch {
            let message = (error as? ProbeError)?.localizedDescription ?? (error as? PilotError)?.localizedDescription ?? "Локальная ошибка. Проход остановлен."
            emit(["event": "stopped", "message": message, "retry": false])
            Darwin.exit(1)
        }
    }
    static func run(_ options: PilotOptions) async throws {
        try validatePilotSchemas(options)
        let directory = options.outputDirectory!
        let lock = options.execute ? try PilotLock(directory: directory) : nil
        defer { withExtendedLifetime(lock) {} }
        var journal = try loadJournal(directory)
        if options.reconcileCreate != nil {
            try reconcile(options, journal: &journal)
            return
        }
        if let taskID = options.resultOnce {
            try await fetchKnownResult(options, taskID: taskID, journal: &journal)
            return
        }
        if options.recoverFrom != nil {
            try await recover(options, journal: &journal)
            return
        }
        let pending = pilots.filter { pilot in !journal.entries.contains { $0.companyID == pilot.id } }
        let unresolved = journal.entries.filter { ["creation_started", "failed"].contains($0.state) }
        emit(["event": options.execute ? "plan_execute" : "plan_dry_run", "projectID": projectID, "module": taskModule,
              "mode": options.pollOnce ? "poll_once" : "create", "existingEntries": journal.entries.count,
              "unresolvedEntries": unresolved.count, "maximumNewTasks": options.pollOnce ? 0 : min(options.maxNew, pending.count),
              "maximumHTTPRequests": options.pollOnce ? 3 + journal.entries.filter { $0.state != "complete" && $0.taskID != nil && $0.resultResponse == nil }.count : 2 + min(options.maxNew, pending.count),
              "requests": options.pollOnce ? [] : pending.prefix(options.maxNew).map(publicQuery),
              "serverResultLimit": "not exposed in module schema", "automaticPayment": false, "networkStarted": false])
        guard options.execute else { return }
        if !options.pollOnce, !unresolved.isEmpty { throw PilotError.unresolvedAttempt }
        if options.pollOnce, journal.entries.isEmpty { return }
        if !options.pollOnce, pending.isEmpty { return }
        let client = PilotMCP(key: try credential())
        try await client.start()
        if options.pollOnce {
            let args: [String: Any] = ["project_id": projectID]
            let result = try await client.call("vk_tasks_list", arguments: args, id: 2)
            let responseFile = try saveResult(result, tool: "vk_tasks_list", arguments: args, directory: directory)
            emit(["event": "saved", "operation": "vk_tasks_list", "path": directory.appendingPathComponent(responseFile).path])
            let tasks = try taskList(result)
            emit(["event": "task_list_checked", "taskCount": tasks.count,
                  "exactPilotMatches": journal.entries.map { entry in
                      ["companyID": entry.companyID, "customName": entry.customName,
                       "count": tasks.filter { $0["custom_name"] as? String == entry.customName || $0["name"] as? String == entry.customName }.count] as [String: Any]
                  }])
            var rpcID = 3
            for index in journal.entries.indices {
                guard journal.entries[index].state != "complete", let taskID = journal.entries[index].taskID else { continue }
                let matches = tasks.filter { responseTaskID($0) == taskID }
                guard matches.count == 1 else { throw PilotError.unrecognizedTaskList }
                let rawStatus = matches[0]["status"]
                let status: String
                if let text = rawStatus as? String { status = text }
                else if let number = rawStatus as? Int { status = "numeric:\(number)" }
                else { throw PilotError.unrecognizedTaskList }
                journal.entries[index].lastStatus = status
                try saveJournal(journal, directory: directory)
                emit(["event": "task_status", "companyID": journal.entries[index].companyID, "taskID": taskID, "status": status])
                // Numeric statuses are preserved without guessing their meaning or auto-fetching.
                guard rawStatus as? String == "SUCCESS", journal.entries[index].resultResponse == nil else { continue }
                let args: [String: Any] = ["project_id": projectID, "task_id": taskID]
                let result = try await client.call("vk_task_result_get", arguments: args, id: rpcID); rpcID += 1
                let file = try saveResult(result, tool: "vk_task_result_get", arguments: args, directory: directory)
                journal.entries[index].resultResponse = file
                try saveJournal(journal, directory: directory)
                emit(["event": "saved", "operation": "vk_task_result_get", "companyID": journal.entries[index].companyID,
                      "taskID": taskID, "path": directory.appendingPathComponent(file).path])
                guard result["isError"] as? Bool != true, let body = payload(result), body["status"] as? Bool == true else { throw PilotError.upstreamTool }
                journal.entries[index].state = "complete"
                try saveJournal(journal, directory: directory)
            }
            return
        }
        for (offset, pilot) in pending.prefix(options.maxNew).enumerated() {
            let args = publicQuery(pilot)
            let index = journal.entries.count
            journal.entries.append(PilotEntry(companyID: pilot.id, companyName: pilot.name, query: pilot.name,
                                               customName: args["custom_name"] as! String, startedAt: Date(), state: "creation_started"))
            try saveJournal(journal, directory: directory)
            do {
                let result = try await client.call("vk_task_create", arguments: args, id: offset + 2)
                let file = try saveResult(result, tool: "vk_task_create", arguments: args, directory: directory)
                journal.entries[index].createResponse = file
                try saveJournal(journal, directory: directory)
                emit(["event": "saved", "operation": "vk_task_create", "companyID": pilot.id, "path": directory.appendingPathComponent(file).path])
                let taskID = try createdTaskID(result, customName: journal.entries[index].customName)
                journal.entries[index].taskID = taskID
                journal.entries[index].state = "created"
                try saveJournal(journal, directory: directory)
                emit(["event": "task_created", "companyID": pilot.id, "taskID": taskID, "pollAutomatically": false])
            } catch {
                journal.entries[index].failure = "creation outcome requires review; no automatic retry"
                // Preserve creation_started when a network/storage error leaves the result uncertain.
                if error is PilotError { journal.entries[index].state = "failed" }
                try? saveJournal(journal, directory: directory)
                throw error
            }
        }
    }

    static func recover(_ options: PilotOptions, journal: inout PilotJournal) async throws {
        let directory = options.outputDirectory!, check = options.recoverFrom!
        guard let index = journal.entries.firstIndex(where: { $0.companyID == "brunoyam" }),
              journal.entries.filter({ ["creation_started", "failed"].contains($0.state) }).count == 1 else { throw PilotError.recoveryCheck }
        let previous = journal.entries[index]
        let existingID = try recoveryMatch(check, entry: previous, directory: directory, confirmVKReconnected: options.confirmVKReconnected)
        let recoveryNumber = (previous.recoveryCount ?? 0) + 1
        let recoveryReason = options.confirmVKReconnected
            ? "Operator confirmed fresh TargetHunter UI: Подключение работает; Лимит сбора постов: Доступен. One additional recovery after fresh task-list check."
            : "Operator confirmed VK reconnection and reviewed fresh task-list absence or exact existing task."
        emit(["event": options.execute ? "recovery_execute" : "recovery_dry_run", "companyID": "brunoyam",
              "checkedResponse": check.path, "action": existingID == nil ? "create_one_after_explicit_absence" : "attach_existing_task",
              "existingTaskID": existingID as Any? ?? NSNull(), "maximumNewTasks": existingID == nil ? 1 : 0,
              "maximumHTTPRequests": existingID == nil ? 3 : 0, "recoveryNumber": recoveryNumber,
              "explicitVKWorkingConfirmation": options.confirmVKReconnected, "networkStarted": false])
        guard options.execute else { return }
        let backup = directory.appendingPathComponent("pilot-journal-before-recovery-\(UUID().uuidString.lowercased()).json")
        do {
            let oldData = try Data(contentsOf: directory.appendingPathComponent("pilot-journal.json"))
            try oldData.write(to: backup, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.path)
        } catch { throw ProbeError.storage }
        if let existingID {
            journal.entries[index].taskID = existingID
            journal.entries[index].state = "created"
            journal.entries[index].recoveryHistory = (previous.recoveryHistory ?? []) + [backup.lastPathComponent]
            journal.entries[index].recoveryCheckResponse = check.lastPathComponent
            journal.entries[index].recoveryCount = recoveryNumber
            journal.entries[index].recoveryReason = recoveryReason
            try saveJournal(journal, directory: directory)
            emit(["event": "existing_task_attached", "companyID": "brunoyam", "taskID": existingID, "created": false])
            return
        }
        let client = PilotMCP(key: try credential())
        try await client.start()
        let pilot = pilots[0], args = publicQuery(pilot)
        journal.entries[index] = PilotEntry(companyID: pilot.id, companyName: pilot.name, query: pilot.name,
                                           customName: previous.customName, startedAt: Date(), state: "creation_started",
                                           recoveryHistory: (previous.recoveryHistory ?? []) + [backup.lastPathComponent],
                                           recoveryCheckResponse: check.lastPathComponent, recoveryCount: recoveryNumber,
                                           recoveryReason: recoveryReason)
        // Durable recovered intent before the only allowed creation request.
        try saveJournal(journal, directory: directory)
        do {
            let result = try await client.call("vk_task_create", arguments: args, id: 2)
            let file = try saveResult(result, tool: "vk_task_create", arguments: args, directory: directory)
            journal.entries[index].createResponse = file
            try saveJournal(journal, directory: directory)
            emit(["event": "saved", "operation": "vk_task_create", "companyID": pilot.id, "path": directory.appendingPathComponent(file).path])
            let id = try createdTaskID(result, customName: journal.entries[index].customName)
            journal.entries[index].state = "created"
            journal.entries[index].taskID = id
            try saveJournal(journal, directory: directory)
            emit(["event": "task_created", "companyID": pilot.id, "taskID": id, "recovery": true, "pollAutomatically": false])
        } catch {
            journal.entries[index].failure = "recovery outcome requires review; no automatic retry"
            if error is PilotError { journal.entries[index].state = "failed" }
            try? saveJournal(journal, directory: directory)
            throw error
        }
    }

    static func reconcile(_ options: PilotOptions, journal: inout PilotJournal) throws {
        let directory = options.outputDirectory!, receipt = options.reconcileCreate!
        guard receipt.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              receipt.lastPathComponent.hasPrefix("vk_task_create-"),
              let index = journal.entries.firstIndex(where: { $0.createResponse == receipt.lastPathComponent }),
              ["creation_started", "failed"].contains(journal.entries[index].state), journal.entries[index].taskID == nil,
              let pilot = pilots.first(where: { $0.id == journal.entries[index].companyID }) else { throw PilotError.reconciliationCheck }
        let record = try object(receipt), previous = journal.entries[index]
        guard record["provider"] as? String == "targetHunter", record["operation"] as? String == "vk_task_create",
              let arguments = record["arguments"] as? [String: Any], NSDictionary(dictionary: arguments).isEqual(to: publicQuery(pilot)),
              let stamp = record["retrievedAt"] as? String, let date = ISO8601DateFormatter().date(from: stamp),
              date >= previous.startedAt, date.timeIntervalSinceNow <= 60,
              let result = record["result"] as? [String: Any] else { throw PilotError.reconciliationCheck }
        let taskID = try createdTaskID(result, customName: previous.customName)
        emit(["event": options.execute ? "reconcile_execute" : "reconcile_dry_run", "companyID": previous.companyID,
              "taskID": taskID, "maximumNewTasks": 0, "maximumHTTPRequests": 0, "networkStarted": false])
        guard options.execute else { return }
        let backup = directory.appendingPathComponent("pilot-journal-before-reconcile-\(UUID().uuidString.lowercased()).json")
        do {
            let oldData = try Data(contentsOf: directory.appendingPathComponent("pilot-journal.json"))
            try oldData.write(to: backup, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.path)
        } catch { throw ProbeError.storage }
        journal.entries[index].taskID = taskID
        journal.entries[index].state = "created"
        journal.entries[index].failure = nil
        journal.entries[index].reconciliationHistory = (previous.reconciliationHistory ?? []) + [backup.lastPathComponent]
        journal.entries[index].reconciledCreateResponse = receipt.lastPathComponent
        try saveJournal(journal, directory: directory)
        emit(["event": "created_task_reconciled", "companyID": previous.companyID, "taskID": taskID, "created": false,
              "snapshot": backup.lastPathComponent, "networkStarted": false])
    }

    static func fetchKnownResult(_ options: PilotOptions, taskID: String, journal: inout PilotJournal) async throws {
        guard let index = journal.entries.firstIndex(where: { $0.taskID == taskID }),
              journal.entries[index].state == "created", journal.entries[index].resultResponse == nil else { throw PilotError.unresolvedAttempt }
        emit(["event": options.execute ? "result_execute" : "result_dry_run", "companyID": journal.entries[index].companyID,
              "taskID": taskID, "maximumNewTasks": 0, "maximumHTTPRequests": 3, "networkStarted": false])
        guard options.execute else { return }
        let client = PilotMCP(key: try credential())
        try await client.start()
        let args: [String: Any] = ["project_id": projectID, "task_id": taskID]
        let result = try await client.call("vk_task_result_get", arguments: args, id: 2)
        let file = try saveResult(result, tool: "vk_task_result_get", arguments: args, directory: options.outputDirectory!)
        journal.entries[index].resultResponse = file
        try saveJournal(journal, directory: options.outputDirectory!)
        // This explicit read-only probe saves the verified task's raw response for review.
        // No completion claim is made until its actual result envelope has been inspected.
        emit(["event": "result_saved_for_review", "companyID": journal.entries[index].companyID, "taskID": taskID,
              "path": options.outputDirectory!.appendingPathComponent(file).path, "completionVerified": false])
    }
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

private final class PilotMCP {
    private let key: String
    private let session: URLSession
    private let redirect = RejectRedirect()
    private var sessionID: String?
    private var protocolVersion: String?
    init(key: String) {
        self.key = key
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
                       "clientInfo": ["name": "tg-signals-readonly-discovery", "version": "1.0"]]
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
