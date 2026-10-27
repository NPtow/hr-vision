import Foundation

public final class WorkspaceRepository {
    public let fileURL: URL

    public init(fileURL: URL) {
        self.fileURL = fileURL
    }

    public func load() throws -> WorkspaceSnapshot {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return WorkspaceSnapshot()
        }
        let snapshot = try JSONDecoder().decode(WorkspaceSnapshot.self, from: Data(contentsOf: fileURL))
        guard snapshot.version == 1 else {
            throw RepositoryError.unsupportedVersion(snapshot.version)
        }
        return snapshot
    }

    public func save(_ snapshot: WorkspaceSnapshot) throws {
        guard snapshot.version == 1 else {
            throw RepositoryError.unsupportedVersion(snapshot.version)
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(snapshot)
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: fileURL, options: .atomic)
    }

    public enum RepositoryError: Error, LocalizedError {
        case unsupportedVersion(Int)

        public var errorDescription: String? {
            switch self {
            case .unsupportedVersion(let version):
                return "Версия данных \(version) не поддерживается. Сохранённый файл оставлен без изменений."
            }
        }
    }
}
