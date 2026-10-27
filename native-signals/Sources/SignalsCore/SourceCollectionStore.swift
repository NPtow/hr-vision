import Foundation
import Darwin

public final class SourceCollectionStore {
    public let directoryURL: URL
    public init(directoryURL: URL) { self.directoryURL = directoryURL }

    public func loadRuns() throws -> [SourceCollectionRun] {
        guard FileManager.default.fileExists(atPath: directoryURL.path) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try Self.decode(Data(contentsOf: $0)) }
            .sorted { $0.startedAt > $1.startedAt }
    }

    public func save(_ run: SourceCollectionRun) throws {
        _ = try Self.encode(run)
        let url = directoryURL.appendingPathComponent(run.id + ".json")
        // A corrupt or newer collection is a visible error, never an empty replacement.
        let previous = try loadRuns()
        var retained = run
        if run.version == 2, let existing = previous.first(where: { $0.id == run.id }) {
            retained = try Self.mergeTargetHunter(existing: existing, incoming: run)
            if try Self.encode(retained) == Self.encode(existing) {
                try Self.syncTaskFile(url)
                return
            }
        }
        let data = try Self.encode(retained)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        // The batch journal may mark this task imported immediately after save returns.
        if retained.version == 2 { try Self.syncTaskFile(url) }
    }

    public static func decode(_ data: Data) throws -> SourceCollectionRun {
        guard data.count <= 8_000_000 else { throw StoreError.invalidData }
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let run: SourceCollectionRun
        do { run = try decoder.decode(SourceCollectionRun.self, from: data) }
        catch { throw StoreError.invalidData }
        try validate(run)
        return run
    }

    public static func encode(_ run: SourceCollectionRun) throws -> Data {
        try validate(run)
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(run)
    }

    private static func validate(_ run: SourceCollectionRun) throws {
        guard [1, 2, 3].contains(run.version) else { throw StoreError.unsupportedVersion }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        guard !run.id.isEmpty, run.id.count <= 100, run.id.unicodeScalars.allSatisfy(allowed.contains),
              run.rawResponse.utf8.count <= 4_000_000, ["json", "xml"].contains(run.rawFormat),
              run.completedAt >= run.startedAt else { throw StoreError.invalidData }
        if run.version == 3 {
            try validateHarvestResult(run)
            return
        }
        guard run.harvest == nil, run.results.allSatisfy({ $0.linkedIn == nil }) else { throw StoreError.invalidData }
        if run.version == 2 {
            try validateTargetHunterResult(run)
            return
        }
        guard run.targetHunterTask == nil, run.provider != .harvest else { throw StoreError.invalidData }
        if run.provider == .targetHunter {
            guard run.request == nil, run.results.isEmpty, run.tools.count <= 100, run.rawFormat == "json",
                  Set(run.tools.map(\.name)).count == run.tools.count else { throw StoreError.invalidData }
            for tool in run.tools {
                guard !tool.name.isEmpty, tool.name.count <= 256,
                      let data = tool.inputSchemaJSON.data(using: .utf8),
                      (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else { throw StoreError.invalidData }
            }
        } else {
            guard let request = run.request, !request.companyID.isEmpty, !request.companyName.isEmpty,
                  !request.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, request.query.count <= 400,
                  (1...10).contains(request.limit), run.results.count <= request.limit, run.tools.isEmpty,
                  Set(run.results.map(\.id)).count == run.results.count,
                  run.rawFormat == (run.provider == .yandex ? "xml" : "json") else { throw StoreError.invalidData }
            for result in run.results {
                guard result.provider == run.provider, result.companyID == request.companyID,
                      result.companyName == request.companyName, result.query == request.query,
                      SourceClient.safeURL(result.url) != nil, !result.id.isEmpty,
                      result.contentType == .searchSnippet, result.vkCommunity == nil else { throw StoreError.invalidData }
            }
        }
    }

    private static func validateHarvestResult(_ run: SourceCollectionRun) throws {
        guard run.provider == .harvest, run.rawFormat == "json", run.tools.isEmpty, run.targetHunterTask == nil,
              let request = run.request, !request.companyID.isEmpty, !request.companyName.isEmpty,
              !request.query.isEmpty, request.query.count <= 400, (1...100).contains(request.limit),
              run.results.count <= request.limit, Set(run.results.map(\.url)).count == run.results.count,
              let harvest = run.harvest, harvest.page == 1, harvest.receivedCount >= run.results.count,
              harvest.reportedCount.map({ $0 >= 0 }) ?? true, harvest.totalPages.map({ $0 >= 0 }) ?? true,
              harvest.estimatedCostUSD == harvest.operation.estimatedCostUSD,
              (try? JSONSerialization.jsonObject(with: Data(run.rawResponse.utf8))) is [String: Any]
        else { throw StoreError.invalidData }
        if harvest.operation != .companies {
            guard HarvestClient.linkedInURL(harvest.targetURL, kind: harvest.operation == .people ? .companies : .people) == harvest.targetURL,
                  harvest.targetURL != nil else { throw StoreError.invalidData }
        }
        for result in run.results {
            guard result.provider == .harvest, result.companyID == request.companyID, result.companyName == request.companyName,
                  result.query == request.query, result.vkCommunity == nil,
                  let metadata = result.linkedIn, metadata.operation == harvest.operation, metadata.affiliation == "unverified",
                  result.contentType == harvest.operation.contentType,
                  HarvestClient.linkedInURL(result.url, kind: harvest.operation) == result.url,
                  result.retrievedAt >= run.startedAt, result.retrievedAt <= run.completedAt,
                  !result.title.isEmpty, result.title.count <= 4096, result.snippet.count <= 100_000,
                  result.id == SourceSearchResult(request: request, url: result.url, title: result.title, text: result.snippet,
                                                 metadata: metadata, retrievedAt: result.retrievedAt).id
            else { throw StoreError.invalidData }
        }
    }

    private static func validateTargetHunterResult(_ run: SourceCollectionRun) throws {
        guard run.provider == .targetHunter, run.rawFormat == "json", run.tools.isEmpty,
              let request = run.request, !request.companyID.isEmpty, !request.companyName.isEmpty,
              !request.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, request.query.count <= 400,
              (1...TargetHunterResultImporter.maximumResults).contains(request.limit), run.results.count <= request.limit,
              let task = run.targetHunterTask, task.moduleID == "vk/2", TargetHunterResultImporter.validTaskID(task.taskID),
              run.id == TargetHunterResultImporter.runID(companyID: request.companyID, taskID: task.taskID),
              task.reportedCount.map({ $0 >= 0 }) ?? true, task.receivedCount == run.results.count,
              task.completeness == .assess(reportedCount: task.reportedCount, receivedCount: task.receivedCount),
              run.hasMore == (task.completeness != .complete),
              Set(run.results.map(\.id)).count == run.results.count,
              Set(run.results.compactMap { $0.vkCommunity?.id }).count == run.results.count
        else { throw StoreError.invalidData }
        for result in run.results {
            guard result.provider == .targetHunter, result.companyID == request.companyID,
                  result.companyName == request.companyName, result.query == request.query,
                  result.contentType == .vkCommunityCandidate, let group = result.vkCommunity,
                  group.id > 0, group.affiliation == .unverified,
                  group.membersCount.map({ $0 >= 0 }) ?? true,
                  group.isClosed.map({ (0...2).contains($0) }) ?? true,
                  group.groupType.map({ ["group", "page", "event"].contains($0) }) ?? true,
                  group.screenName.map({ $0.range(of: #"^[A-Za-z0-9_.]{1,100}$"#, options: .regularExpression) != nil }) ?? true,
                  !result.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, result.title.count <= 4_096,
                  result.retrievedAt >= run.startedAt, result.retrievedAt <= run.completedAt,
                  result.url == "https://vk.com/club\(group.id)",
                  result.snippet == SourceSearchResult(request: request, community: group, title: result.title, retrievedAt: result.retrievedAt).snippet,
                  result.id == SourceSearchResult(request: request, community: group, title: result.title, retrievedAt: result.retrievedAt).id
            else { throw StoreError.invalidData }
        }
        guard run.rawResponse == (try TargetHunterResultImporter.normalizedResponse(run.results)) else { throw StoreError.invalidData }
    }

    /// Re-importing a task keeps one native run. Additional export objects extend that run;
    /// a smaller repeat can never erase previously received communities or legacy history.
    private static func mergeTargetHunter(existing: SourceCollectionRun, incoming: SourceCollectionRun) throws -> SourceCollectionRun {
        guard existing.version == 2, let oldTask = existing.targetHunterTask, let newTask = incoming.targetHunterTask,
              oldTask.taskID == newTask.taskID, oldTask.moduleID == newTask.moduleID,
              let oldRequest = existing.request, let newRequest = incoming.request,
              oldRequest.companyID == newRequest.companyID, oldRequest.companyName == newRequest.companyName,
              oldRequest.query == newRequest.query else { throw StoreError.invalidData }
        var merged = existing
        for result in incoming.results {
            guard let groupID = result.vkCommunity?.id else { throw StoreError.invalidData }
            if let index = merged.results.firstIndex(where: { $0.vkCommunity?.id == groupID }) {
                if merged.results[index].id != result.id, result.retrievedAt >= merged.results[index].retrievedAt {
                    merged.results[index] = result
                }
            } else { merged.results.append(result) }
        }
        let reportedCount = [oldTask.reportedCount, newTask.reportedCount].compactMap { $0 }.max()
        let metadata = TargetHunterTaskMetadata(taskID: oldTask.taskID, moduleID: oldTask.moduleID,
                                               reportedCount: reportedCount, receivedCount: merged.results.count)
        if merged.results.map(\.id) == existing.results.map(\.id), metadata == oldTask { return existing }
        merged.targetHunterTask = metadata
        merged.request?.limit = max(oldRequest.limit, newRequest.limit)
        merged.startedAt = min(existing.startedAt, incoming.startedAt)
        merged.completedAt = max(existing.completedAt, incoming.completedAt)
        merged.hasMore = metadata.completeness != .complete
        merged.rawResponse = try TargetHunterResultImporter.normalizedResponse(merged.results)
        try validate(merged)
        return merged
    }

    private static func syncTaskFile(_ url: URL) throws {
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW)
        guard descriptor >= 0 else { throw StoreError.writeFailure }
        defer { Darwin.close(descriptor) }
        guard fsync(descriptor) == 0 else { throw StoreError.writeFailure }
        let directory = Darwin.open(url.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY)
        guard directory >= 0 else { throw StoreError.writeFailure }
        defer { Darwin.close(directory) }
        guard fsync(directory) == 0 else { throw StoreError.writeFailure }
    }

    public enum StoreError: Error, LocalizedError {
        case invalidData, unsupportedVersion, writeFailure
        public var errorDescription: String? {
            switch self {
            case .invalidData: return "Файл результатов источников повреждён или имеет неподдерживаемые данные. Исходный файл сохранён."
            case .unsupportedVersion: return "Версия результатов источников не поддерживается. Исходный файл сохранён."
            case .writeFailure: return "Не удалось подтвердить сохранение результатов на диск. Импорт задачи не завершён."
            }
        }
    }
}
