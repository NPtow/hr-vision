import Foundation
import CryptoKit

/// Imports an already obtained public-community export. No credentials, network, or dossier writes.
public enum TargetHunterResultImporter {
    public static let maximumResults = 1_000
    public static let maximumResponseBytes = 4_000_000

    public enum ImportError: Error, LocalizedError {
        case invalidResponse, failedOperation, wrongTask, unsupportedModule, excessiveResults, invalidRequest
        public var errorDescription: String? {
            switch self {
            case .invalidResponse: return "Экспорт TargetHunter имеет неподдерживаемый или повреждённый формат."
            case .failedOperation: return "TargetHunter сообщил об ошибке. Она не записана как пустой результат поиска."
            case .wrongTask: return "Идентификатор задачи экспорта не совпадает с импортируемой задачей."
            case .unsupportedModule: return "Импорт поддерживает только поиск сообществ vk/2."
            case .excessiveResults: return "Экспорт превышает предел импорта: 1 000 сообществ или 4 MB."
            case .invalidRequest: return "Для импорта нужны компания, запрос и корректные метаданные задачи."
            }
        }
    }

    private struct Export: Decodable {
        let status: Bool
        let type: String?
        let data: [Group]?
    }
    private struct Group: Decodable {
        let id: Int64
        let name: String
        let screen_name: String?
        let members_count: Int?
        let is_closed: Int?
        let type: String?
    }

    /// Accepts a saved helper receipt, an MCP tool result, or its JSON payload.
    /// Pass limit: 1000 in request to retain the supported export size (web searches keep limit: 10).
    public static func makeRun(request: SourceSearchRequest, taskID: String, moduleID: String = "vk/2",
                               reportedCount: Int?, responseData: Data, retrievedAt: Date? = nil) throws -> SourceCollectionRun {
        guard moduleID == "vk/2" else { throw ImportError.unsupportedModule }
        guard validTaskID(taskID), !request.companyID.isEmpty, !request.companyName.isEmpty,
              !request.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, request.query.count <= 400,
              (1...maximumResults).contains(request.limit), reportedCount.map({ $0 >= 0 }) ?? true
        else { throw ImportError.invalidRequest }
        guard responseData.count <= maximumResponseBytes else { throw ImportError.excessiveResults }
        guard let root = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any] else { throw ImportError.invalidResponse }
        var body = root
        var receiptDate: Date?
        if root["operation"] != nil {
            guard root["provider"] as? String == "targetHunter", root["operation"] as? String == "vk_task_result_get",
                  let arguments = root["arguments"] as? [String: Any], let actualTask = arguments["task_id"] as? String
            else { throw ImportError.invalidResponse }
            guard actualTask == taskID else { throw ImportError.wrongTask }
            if let value = root["retrievedAt"] as? String {
                receiptDate = ISO8601DateFormatter().date(from: value)
                guard receiptDate != nil else { throw ImportError.invalidResponse }
            }
            guard let result = root["result"] as? [String: Any] else { throw ImportError.invalidResponse }
            body = result
        } else if root["jsonrpc"] != nil {
            guard root["jsonrpc"] as? String == "2.0", root["error"] == nil,
                  let result = root["result"] as? [String: Any] else { throw ImportError.failedOperation }
            body = result
        }
        if body["isError"] as? Bool == true { throw ImportError.failedOperation }
        let payload: Data
        if body["structuredContent"] != nil {
            guard let structured = body["structuredContent"] as? [String: Any],
                  let encoded = try? JSONSerialization.data(withJSONObject: structured) else { throw ImportError.invalidResponse }
            payload = encoded
        } else if let content = body["content"] as? [[String: Any]] {
            let texts = content.filter { $0["type"] as? String == "text" }.compactMap { $0["text"] as? String }
            // Never guess which of multiple JSON objects is the authoritative export.
            guard texts.count == 1, texts[0].utf8.count <= maximumResponseBytes else { throw ImportError.invalidResponse }
            payload = Data(texts[0].utf8)
        } else {
            guard let encoded = try? JSONSerialization.data(withJSONObject: body) else { throw ImportError.invalidResponse }
            payload = encoded
        }
        guard let export = try? JSONDecoder().decode(Export.self, from: payload) else { throw ImportError.invalidResponse }
        guard export.status else { throw ImportError.failedOperation }
        guard export.type == "vk_groups_array_object", let groups = export.data else { throw ImportError.invalidResponse }
        guard groups.count <= maximumResults, groups.count <= request.limit else { throw ImportError.excessiveResults }
        guard Set(groups.map(\.id)).count == groups.count else { throw ImportError.invalidResponse }
        let date = retrievedAt ?? receiptDate ?? Date()
        let results = groups.map { group in
            SourceSearchResult(request: request,
                               community: VKCommunityMetadata(id: group.id, screenName: group.screen_name,
                                                              membersCount: group.members_count, isClosed: group.is_closed,
                                                              groupType: group.type),
                               title: decodeEntities(group.name), retrievedAt: date)
        }
        var run = SourceCollectionRun(provider: .targetHunter, request: request, results: results,
                                      rawResponse: try normalizedResponse(results), startedAt: date, completedAt: date)
        run.version = 2
        run.id = runID(companyID: request.companyID, taskID: taskID)
        run.targetHunterTask = TargetHunterTaskMetadata(taskID: taskID, moduleID: moduleID,
                                                       reportedCount: reportedCount, receivedCount: results.count)
        run.hasMore = run.targetHunterTask?.completeness != .complete
        // Apply the same checks used when reading native history before returning anything importable.
        do { _ = try SourceCollectionStore.encode(run) }
        catch { throw ImportError.invalidResponse }
        return run
    }

    public static func runID(companyID: String, taskID: String) -> String {
        "source-th-" + SHA256.hash(data: Data((companyID + "\u{0}" + taskID).utf8)).map { String(format: "%02x", $0) }.joined()
    }

    static func validTaskID(_ value: String) -> Bool {
        !value.isEmpty && value.count <= 128 && value.unicodeScalars.allSatisfy {
            CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_").contains($0)
        }
    }

    /// The retained raw representation is allowlisted public data, never the whole MCP envelope.
    static func normalizedResponse(_ results: [SourceSearchResult]) throws -> String {
        let groups: [[String: Any]] = results.compactMap { result in
            guard let group = result.vkCommunity else { return nil }
            var value: [String: Any] = ["id": group.id, "name": result.title]
            if let value_ = group.screenName { value["screen_name"] = value_ }
            if let value_ = group.membersCount { value["members_count"] = value_ }
            if let value_ = group.isClosed { value["is_closed"] = value_ }
            if let value_ = group.groupType { value["type"] = value_ }
            return value
        }
        let data = try JSONSerialization.data(withJSONObject: ["status": true, "type": "vk_groups_array_object", "data": groups],
                                              options: [.sortedKeys, .withoutEscapingSlashes])
        guard data.count <= maximumResponseBytes, let string = String(data: data, encoding: .utf8) else { throw ImportError.excessiveResults }
        return string
    }

    private static func decodeEntities(_ value: String) -> String {
        value.replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&apos;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
    }
}
