import Foundation

public final class TelemetrCollectionStore {
    public let directoryURL: URL
    public init(directoryURL: URL) { self.directoryURL = directoryURL }

    public func loadRuns() throws -> [TelemetrCollectionRun] {
        guard FileManager.default.fileExists(atPath: directoryURL.path) else { return [] }
        let urls = try FileManager.default.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
        return try urls.map { try Self.decode(Data(contentsOf: $0)) }.sorted { $0.startedAt > $1.startedAt }
    }

    public func save(_ run: TelemetrCollectionRun) throws {
        try Self.validate(run)
        // Do not silently overwrite a malformed or future-format collection on save.
        let existing = try loadRuns()
        var normalized = run
        for index in normalized.queries.indices {
            var seen = Set<String>()
            normalized.queries[index].posts = normalized.queries[index].posts.filter {
                seen.insert($0.companyID + ":" + $0.id).inserted
            }
        }
        // A re-import must not erase a human's existing relevance decision.
        let reviews = existing.flatMap(\.posts).filter { $0.relevance != .unreviewed }
        for query in normalized.queries.indices {
            for post in normalized.queries[query].posts.indices {
                let item = normalized.queries[query].posts[post]
                if let saved = reviews.first(where: { $0.id == item.id && $0.companyID == item.companyID }) {
                    normalized.queries[query].posts[post].relevance = saved.relevance
                }
            }
        }
        try write(normalized)
    }

    @discardableResult
    public func importRun(data: Data) throws -> TelemetrCollectionRun {
        let run = try Self.decode(data)
        try save(run)
        return try loadRuns().first { $0.id == run.id }!
    }

    public func updateRelevance(postID: String, companyID: String, relevance: TelemetrRelevance) throws {
        let runs = try loadRuns()
        for var run in runs {
            var changed = false
            for query in run.queries.indices {
                for post in run.queries[query].posts.indices {
                    let item = run.queries[query].posts[post]
                    if item.id == postID && item.companyID == companyID {
                        run.queries[query].posts[post].relevance = relevance; changed = true
                    }
                }
            }
            if changed { try write(run) }
        }
    }

    public static func decode(_ data: Data) throws -> TelemetrCollectionRun {
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let run: TelemetrCollectionRun
        do { run = try decoder.decode(TelemetrCollectionRun.self, from: data) }
        catch { throw StoreError.invalidData }
        try validate(run)
        return run
    }

    public static func encode(_ run: TelemetrCollectionRun) throws -> Data {
        try validate(run)
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(run)
    }

    private func write(_ run: TelemetrCollectionRun) throws {
        let data = try Self.encode(run)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        try data.write(to: directoryURL.appendingPathComponent(run.id + ".json"), options: .atomic)
    }

    private static func validate(_ run: TelemetrCollectionRun) throws {
        guard run.version == 1 else { throw StoreError.unsupportedVersion }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        guard !run.id.isEmpty, run.id.count <= 100, run.id.unicodeScalars.allSatisfy(allowed.contains) else { throw StoreError.invalidData }
        for result in run.queries {
            guard !result.query.companyID.isEmpty, !result.query.query.isEmpty else { throw StoreError.invalidData }
            for post in result.posts {
                guard !post.channelID.isEmpty, !post.postID.isEmpty, post.platform == "telegram",
                      post.origin == run.origin, post.companyID == result.query.companyID,
                      post.query == result.query.query else { throw StoreError.invalidData }
            }
        }
        if run.origin == .telemetrWebsite, run.limitsBefore != nil || run.limitsAfter != nil { throw StoreError.invalidData }
    }

    public enum StoreError: Error, LocalizedError {
        case invalidData, unsupportedVersion
        public var errorDescription: String? {
            switch self {
            case .invalidData: return "Файл результатов Telemetr повреждён или имеет неподдерживаемые данные. Исходный файл сохранён."
            case .unsupportedVersion: return "Версия результатов Telemetr не поддерживается. Исходный файл сохранён."
            }
        }
    }
}
