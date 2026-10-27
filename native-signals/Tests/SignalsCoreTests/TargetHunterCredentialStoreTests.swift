import Foundation
import XCTest
@testable import SignalsCore

final class TargetHunterCredentialStoreTests: XCTestCase {
    private func withStore(_ action: (TargetHunterCredentialStore, URL) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try action(TargetHunterCredentialStore(directoryURL: root.appendingPathComponent("credentials", isDirectory: true)), root)
    }

    func testMissingFileIsSilentAndDoesNotCreateDirectories() throws {
        try withStore { store, _ in
            XCTAssertNil(try store.load())
            try store.remove()
            XCTAssertFalse(FileManager.default.fileExists(atPath: store.directoryURL.path))
        }
    }

    func testPrivateRoundTripReplacementAndRemovalLeaveOtherFilesAlone() throws {
        try withStore { store, root in
            let unrelated = root.appendingPathComponent("workspace.json")
            let preserved = Data("preserve existing user data".utf8)
            try preserved.write(to: unrelated)
            try store.save(SourceCredentials(apiKey: "fixture-first"))
            XCTAssertEqual(try store.load(), SourceCredentials(apiKey: "fixture-first"))
            XCTAssertEqual(try mode(store.directoryURL), 0o700)
            XCTAssertEqual(try mode(store.fileURL), 0o600)
            try store.save(SourceCredentials(apiKey: "fixture-replacement", folderID: ""))
            XCTAssertEqual(try store.load()?.apiKey, "fixture-replacement")
            XCTAssertEqual(try mode(store.fileURL), 0o600)
            XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: store.directoryURL.path), ["targethunter.json"])
            try store.remove()
            try store.remove()
            XCTAssertNil(try store.load())
            XCTAssertEqual(try Data(contentsOf: unrelated), preserved)
        }
    }

    func testMalformedOrOversizedFileReportsNoCredentialContentAndPreservesBytes() throws {
        try withStore { store, _ in
            try store.save(SourceCredentials(apiKey: "fixture"))
            for bytes in [Data("fixture-secret-corrupted-json".utf8), Data(repeating: 65, count: 65_537)] {
                try bytes.write(to: store.fileURL)
                XCTAssertThrowsError(try store.load()) { error in
                    XCTAssertFalse(error.localizedDescription.contains("fixture-secret"))
                }
                XCTAssertEqual(try Data(contentsOf: store.fileURL), bytes)
            }
        }
    }

    func testSymlinkCannotRedirectCredentialReadWriteOrRemoval() throws {
        try withStore { store, root in
            let unrelated = root.appendingPathComponent("unrelated.json")
            let bytes = Data("untouched fixture".utf8)
            try bytes.write(to: unrelated)
            try FileManager.default.createDirectory(at: store.directoryURL, withIntermediateDirectories: false)
            try FileManager.default.createSymbolicLink(at: store.fileURL, withDestinationURL: unrelated)
            XCTAssertThrowsError(try store.load())
            XCTAssertThrowsError(try store.save(SourceCredentials(apiKey: "replacement")))
            XCTAssertThrowsError(try store.remove())
            XCTAssertEqual(try Data(contentsOf: unrelated), bytes)
            try FileManager.default.removeItem(at: store.directoryURL)
            try FileManager.default.createSymbolicLink(at: store.directoryURL, withDestinationURL: root)
            XCTAssertThrowsError(try store.load())
            XCTAssertThrowsError(try store.save(SourceCredentials(apiKey: "replacement")))
        }
    }

    private func mode(_ url: URL) throws -> Int {
        (try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber)?.intValue ?? -1
    }
}
