import Foundation
import Security
import SignalsCore

struct SchoolQuery: Codable { let companyID: String; let companyName: String; let query: String }
struct Credential: Decodable { let apiKey: String }

@main struct SchoolSearch {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 3 else { print("school-search PLAN OUTPUT_DIR"); return }
        let entries = try JSONDecoder().decode([SchoolQuery].self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
        guard entries.count <= 130, entries.allSatisfy({ !$0.companyID.isEmpty && !$0.query.isEmpty && $0.query.count <= 400 }) else { return }
        let out = URL(fileURLWithPath: args[2]); try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let keyQuery: [String: Any] = [kSecClass as String:kSecClassGenericPassword,
            kSecAttrService as String:"ru.hrvision.tgsignals.sources", kSecAttrAccount as String:"serper",
            kSecReturnData as String:true, kSecMatchLimit as String:kSecMatchLimitOne,
            kSecUseAuthenticationUI as String:kSecUseAuthenticationUIFail]
        var result: CFTypeRef?; let status = SecItemCopyMatching(keyQuery as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data, let credential = try? JSONDecoder().decode(Credential.self, from: data) else {
            print("Saved Serper credential unavailable without UI; code \(status). No requests sent."); return
        }
        let client = SourceClient(provider: .serper, apiKey: credential.apiKey)
        let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TG Signals/source-runs")
        let store = SourceCollectionStore(directoryURL: base)
        let history = try store.loadRuns()
        var completed = 0
        for (i, entry) in entries.enumerated() {
            let receipt = out.appendingPathComponent("attempt-\(i).json")
            if FileManager.default.fileExists(atPath: receipt.path) { continue }
            if let run = history.first(where: { $0.provider == .serper && $0.request?.companyID == entry.companyID && $0.request?.query == entry.query }) {
                try SourceCollectionStore.encode(run).write(to: out.appendingPathComponent("result-\(i).json"), options: .atomic); continue
            }
            try JSONEncoder().encode(entry).write(to: receipt, options: .atomic)
            do {
                let run = try await client.search(.init(companyID: entry.companyID, companyName: entry.companyName, query: entry.query))
                try store.save(run)
                try SourceCollectionStore.encode(run).write(to: out.appendingPathComponent("result-\(i).json"), options: .atomic)
                completed += 1; print("\(i + 1)/\(entries.count): \(entry.companyName): \(run.results.count)")
            } catch { print("Request stopped: \(error.localizedDescription)"); break }
            try await Task.sleep(nanoseconds: 400_000_000)
        }
        print("New saved searches: \(completed)")
    }
}
