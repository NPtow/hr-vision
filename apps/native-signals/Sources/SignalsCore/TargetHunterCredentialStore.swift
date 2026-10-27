import Foundation
import Darwin

public struct SourceCredentials: Codable, Equatable, Sendable {
    public var apiKey: String
    public var folderID: String

    public init(apiKey: String = "", folderID: String = "") {
        self.apiKey = apiKey
        self.folderID = folderID
    }
}

/// Shared by the desktop app and local collectors. This store never uses Keychain.
public struct TargetHunterCredentialStore: Sendable {
    public let directoryURL: URL
    public var fileURL: URL { directoryURL.appendingPathComponent("targethunter.json") }
    private let filename = "targethunter.json"
    private let maximumBytes = 65_536

    public init(directoryURL: URL? = nil) {
        self.directoryURL = directoryURL ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("TG Signals", isDirectory: true)
            .appendingPathComponent("credentials", isDirectory: true)
    }

    public func load() throws -> SourceCredentials? {
        guard let directory = try openDirectory(create: false) else { return nil }
        defer { close(directory) }
        let descriptor = openat(directory, filename, O_RDONLY | O_NOFOLLOW)
        guard descriptor >= 0 else {
            if errno == ENOENT { return nil }
            throw StorageError.read
        }
        defer { close(descriptor) }
        var attributes = stat()
        guard fstat(descriptor, &attributes) == 0,
              attributes.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              attributes.st_size <= maximumBytes,
              fchmod(descriptor, 0o600) == 0 else { throw StorageError.read }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw StorageError.read }
            if count == 0 { break }
            data.append(contentsOf: buffer.prefix(count))
            guard data.count <= maximumBytes else { throw StorageError.read }
        }
        guard let credentials = try? JSONDecoder().decode(SourceCredentials.self, from: data) else { throw StorageError.read }
        return credentials
    }

    public func save(_ credentials: SourceCredentials) throws {
        guard let data = try? JSONEncoder().encode(credentials), data.count <= maximumBytes,
              let directory = try openDirectory(create: true) else { throw StorageError.write }
        defer { close(directory) }
        try validateExistingFile(in: directory)
        let temporaryName = ".targethunter-\(UUID().uuidString).tmp"
        let descriptor = openat(directory, temporaryName, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw StorageError.write }
        defer {
            close(descriptor)
            unlinkat(directory, temporaryName, 0)
        }
        guard fchmod(descriptor, 0o600) == 0 else { throw StorageError.write }
        try data.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let count = Darwin.write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw StorageError.write }
                offset += count
            }
        }
        guard fsync(descriptor) == 0,
              renameat(directory, temporaryName, directory, filename) == 0,
              fsync(directory) == 0 else { throw StorageError.write }
    }

    public func remove() throws {
        guard let directory = try openDirectory(create: false) else { return }
        defer { close(directory) }
        try validateExistingFile(in: directory)
        if unlinkat(directory, filename, 0) != 0 {
            if errno == ENOENT { return }
            throw StorageError.remove
        }
        guard fsync(directory) == 0 else { throw StorageError.remove }
    }

    private func openDirectory(create: Bool) throws -> Int32? {
        if create {
            do {
                try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true,
                                                        attributes: [.posixPermissions: 0o700])
            } catch { throw StorageError.directory }
        }
        let descriptor = open(directoryURL.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard descriptor >= 0 else {
            if !create && errno == ENOENT { return nil }
            throw StorageError.directory
        }
        guard fchmod(descriptor, 0o700) == 0 else {
            close(descriptor)
            throw StorageError.directory
        }
        return descriptor
    }

    private func validateExistingFile(in directory: Int32) throws {
        var attributes = stat()
        if fstatat(directory, filename, &attributes, AT_SYMLINK_NOFOLLOW) != 0 {
            if errno == ENOENT { return }
            throw StorageError.write
        }
        guard attributes.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) else { throw StorageError.write }
    }

    private enum StorageError: LocalizedError {
        case directory, read, write, remove
        var errorDescription: String? {
            switch self {
            case .directory: return "Не удалось открыть локальную папку доступа TargetHunter."
            case .read: return "Не удалось прочитать локальный доступ TargetHunter. Сохранённый файл не изменён."
            case .write: return "Не удалось сохранить локальный доступ TargetHunter."
            case .remove: return "Не удалось удалить локальный доступ TargetHunter."
            }
        }
    }
}
