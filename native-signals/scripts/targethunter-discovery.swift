import Foundation
import Darwin

// Read-only MCP discovery. The only tools this program can call are the two below.
// It cannot create tasks, upload audiences, send messages, or delete anything.
private let permittedTools = Set(["vk_modules_list", "vk_projects_list"])
private let endpoint = URL(string: "https://vk-mcp.targethunter.ru/mcp")!

private struct Options {
    var execute = false
    var modules: [String] = []
    var schemaRun: URL?
    var outputDirectory: URL?

    init(_ args: [String]) throws {
        var i = 0
        func value() throws -> String { i += 1; guard i < args.count else { throw ProbeError.usage }; return args[i] }
        while i < args.count {
            switch args[i] {
            case "--execute": execute = true
            case "--modules": modules = try value().split(separator: ",").map(String.init)
            case "--schema-run":
                let path = try value(); guard path.hasPrefix("/") else { throw ProbeError.usage }
                schemaRun = URL(fileURLWithPath: path)
            case "--output-dir":
                let path = try value(); guard path.hasPrefix("/") else { throw ProbeError.usage }
                outputDirectory = URL(fileURLWithPath: path, isDirectory: true)
            default: throw ProbeError.usage
            }
            i += 1
        }
        guard schemaRun != nil, outputDirectory != nil, modules.count <= 50,
              Set(modules).count == modules.count,
              modules.allSatisfy({ !$0.isEmpty && $0.count <= 200 && !$0.contains(where: { $0.isWhitespace }) })
        else { throw ProbeError.usage }
    }

    var calls: [(String, [String: Any])] {
        modules.isEmpty
            ? [("vk_modules_list", [:]), ("vk_projects_list", [:])]
            : [("vk_modules_list", ["modules": modules])]
    }
}

private enum ProbeError: LocalizedError {
    case usage, schema, credential, invalidResponse, unsupportedProtocol, tooLarge, http(Int), redirect, network, rpc, tool, storage
    var errorDescription: String? {
        switch self {
        case .usage: return "Usage: targethunter-discovery --schema-run /absolute/source-run.json --output-dir /absolute/output [--modules vk/1,vk/2] [--execute]. Default: no network, no credential reads, no writes."
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

private func credential() throws -> String {
    guard let parsed = try? TargetHunterCredentialStore().load() else { throw ProbeError.credential }
    let key = parsed.apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty, key.count <= 4096, key.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }) else { throw ProbeError.credential }
    return key
}

private func validateSchema(_ url: URL, calls: [(String, [String: Any])]) throws {
    guard let data = try? Data(contentsOf: url), data.count <= 8_000_000,
          let run = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          run["provider"] as? String == "targetHunter",
          let tools = run["tools"] as? [[String: Any]] else { throw ProbeError.schema }
    for (name, _) in calls {
        guard permittedTools.contains(name), let tool = tools.first(where: { $0["name"] as? String == name }),
              let schemaText = tool["inputSchemaJSON"] as? String,
              let schema = try? JSONSerialization.jsonObject(with: Data(schemaText.utf8)) as? [String: Any],
              schema["type"] as? String == "object", (schema["required"] as? [String] ?? []).isEmpty,
              let properties = schema["properties"] as? [String: Any] else { throw ProbeError.schema }
        if name == "vk_projects_list" {
            guard properties.isEmpty else { throw ProbeError.schema }
        } else {
            guard Set(properties.keys) == Set(["modules"]), let modules = properties["modules"] as? [String: Any],
                  modules["type"] as? String == "array", (modules["items"] as? [String: Any])?["type"] as? String == "string"
            else { throw ProbeError.schema }
        }
    }
}

private final class RejectRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

private final class ReadOnlyMCP {
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

@main
private struct TargetHunterDiscovery {
    static func main() async {
        do {
            let options = try Options(Array(CommandLine.arguments.dropFirst()))
            try validateSchema(options.schemaRun!, calls: options.calls)
            emit(["event": options.execute ? "plan_execute" : "plan_dry_run", "endpoint": endpoint.absoluteString,
                  "calls": options.calls.map { ["tool": $0.0, "arguments": $0.1] },
                  "maximumHTTPRequests": 2 + options.calls.count, "readOnly": true, "networkStarted": false])
            guard options.execute else { return }
            let client = ReadOnlyMCP(key: try credential())
            try await client.start()
            for (index, operation) in options.calls.enumerated() {
                let result = try await client.call(operation.0, arguments: operation.1, id: index + 2)
                let record: [String: Any] = ["version": 1, "provider": "targetHunter", "operation": operation.0,
                                            "arguments": operation.1, "retrievedAt": ISO8601DateFormatter().string(from: Date()),
                                            "sourceSchemaRun": options.schemaRun!.lastPathComponent, "result": result]
                let file = options.outputDirectory!.appendingPathComponent("\(operation.0)-\(UUID().uuidString.lowercased()).json")
                do {
                    try FileManager.default.createDirectory(at: options.outputDirectory!, withIntermediateDirectories: true)
                    try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
                        .write(to: file, options: .atomic)
                    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
                } catch { throw ProbeError.storage }
                emit(["event": "saved", "tool": operation.0, "path": file.path, "isError": result["isError"] as? Bool ?? false])
                if result["isError"] as? Bool == true { throw ProbeError.tool }
            }
            emit(["event": "finished", "toolCalls": options.calls.count, "readOnly": true])
        } catch {
            emit(["event": "stopped", "message": (error as? ProbeError)?.localizedDescription ?? "Локальная ошибка; запросы остановлены."])
            Darwin.exit(1)
        }
    }
}
