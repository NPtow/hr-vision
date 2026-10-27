import Foundation
import SignalsCore

@MainActor
final class TelemetrRPAModel: ObservableObject {
    struct Job: Decodable, Identifiable {
        let id: String
        let state: String
        let message: String
        let totalQueries: Int
        let finishedQueries: Int
        let posts: Int
        let pages: Int
        let companyName: String
        let partialQueries: Int
    }
    struct Status: Decodable { let version: Int; let browserOpen: Bool; let activeJob: String?; let jobs: [Job] }
    private struct Control: Decodable { let version: Int; let pid: Int; let port: Int; let token: String }
    @Published var jobs: [Job] = []
    @Published var browserJobs: [Job] = []
    @Published var available = false
    @Published var browserOpen = false
    @Published var busy = false
    @Published var message = "Отдельный сборщик остановлен. Основной сбор выполняется через открытый браузер Codex."
    @Published var error: String?
    private var process: Process?
    private var imported: [String: Date] = [:]
    private var refreshing = false
    private let directory: URL

    init() {
        let base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        directory = base.appendingPathComponent("rpa-telemetr", isDirectory: true)
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        let control = try JSONDecoder().decode(Control.self, from: Data(contentsOf: directory.appendingPathComponent("control.json")))
        guard control.version == 1, (1024...65535).contains(control.port), control.token.count == 64 else { throw CocoaError(.fileReadCorruptFile) }
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(control.port)\(path)")!)
        req.httpMethod = method; req.httpBody = body; req.timeoutInterval = 50
        req.setValue("Bearer \(control.token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: String])?["error"] ?? "Сборщик не завершил действие."
            throw NSError(domain: "TelemetrRPA", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return data
    }

    private func ensureServer() async throws {
        if (try? await request("/status")) != nil { return }
        let node = ["/opt/homebrew/bin/node", "/usr/local/bin/node"].first { FileManager.default.isExecutableFile(atPath: $0) }
        guard let node, let resource = Bundle.main.resourceURL?.appendingPathComponent("telemetr-rpa/server.mjs"), FileManager.default.fileExists(atPath: resource.path) else {
            throw NSError(domain: "TelemetrRPA", code: 2, userInfo: [NSLocalizedDescriptionKey: "В сборке отсутствует браузерный модуль или Node.js. Установите актуальную сборку приложения."])
        }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let logURL = directory.appendingPathComponent("worker.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        let log = try FileHandle(forWritingTo: logURL); try log.seekToEnd()
        let task = Process(); task.executableURL = URL(fileURLWithPath: node); task.arguments = [resource.path]
        var environment = ProcessInfo.processInfo.environment
        environment["TG_SIGNALS_RPA_DIR"] = directory.path
        environment.removeValue(forKey: "NODE_OPTIONS")
        task.environment = environment; task.standardOutput = log; task.standardError = log
        try task.run(); process = task
        for _ in 0..<20 {
            try await Task.sleep(for: .milliseconds(250))
            if (try? await request("/status")) != nil { return }
            if !task.isRunning { break }
        }
        throw NSError(domain: "TelemetrRPA", code: 3, userInfo: [NSLocalizedDescriptionKey: "Сборщик не запустился. Проверьте журнал в папке rpa-telemetr."])
    }

    func openBrowser() async {
        guard !busy else { return }; busy = true; error = nil
        defer { busy = false }
        do {
            try await ensureServer(); _ = try await request("/browser", method: "POST")
            message = "Браузер открыт. После входа можно запустить сбор; вход сохраняется в отдельном профиле на этом Mac."
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func start(queries: [[String: String]], from: Date, to: Date, maxPages: Int) async {
        guard !busy else { return }; busy = true; error = nil
        defer { busy = false }
        let format = DateFormatter(); format.dateFormat = "yyyy-MM-dd"; format.timeZone = TimeZone(identifier: "Europe/Moscow")
        do {
            try await ensureServer()
            let body = try JSONSerialization.data(withJSONObject: ["queries": queries, "dateFrom": format.string(from: from), "dateTo": format.string(from: to), "maxPages": maxPages])
            _ = try await request("/jobs", method: "POST", body: body); await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func action(_ action: String, job: Job) async {
        guard !busy else { return }; busy = true; error = nil; defer { busy = false }
        do { try await ensureServer(); _ = try await request("/jobs/\(job.id)/\(action)", method: "POST"); await refresh() }
        catch { self.error = error.localizedDescription }
    }

    func refresh(collection: TelemetrCollectionModel? = nil) async {
        guard !refreshing else { return }; refreshing = true; defer { refreshing = false }
        let browserDirectory = directory.appendingPathComponent("browser-jobs", isDirectory: true)
        if let files = try? FileManager.default.contentsOfDirectory(at: browserDirectory, includingPropertiesForKeys: nil) {
            browserJobs = files.filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent > $1.lastPathComponent }.compactMap { url in
                guard let data = try? Data(contentsOf: url), data.count < 100_000 else { return nil }
                return try? JSONDecoder().decode(Job.self, from: data)
            }
        }
        if let data = try? await request("/status"), let state = try? JSONDecoder().decode(Status.self, from: data) {
            available = true; browserOpen = state.browserOpen; jobs = state.jobs
        } else { available = false }
        // Import through the existing store so human relevance decisions survive re-runs.
        if let collection, !collection.busy, !collection.storageUnavailable {
            let exportDirectory = directory.appendingPathComponent("exports", isDirectory: true)
            guard let files = try? FileManager.default.contentsOfDirectory(at: exportDirectory, includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey]) else { return }
            for url in files where url.pathExtension == "json" {
                do {
                    let attr = try url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
                    guard let date = attr.contentModificationDate, imported[url.lastPathComponent] != date else { continue }
                    guard (attr.fileSize ?? Int.max) <= 32_000_000 else { throw CocoaError(.fileReadTooLarge) }
                    let data = try Data(contentsOf: url)
                    let run = try TelemetrCollectionStore.decode(data)
                    guard run.origin == .telemetrWebsite, run.id.hasPrefix("rpa-") else { throw CocoaError(.fileReadCorruptFile) }
                    if collection.importData(data) { imported[url.lastPathComponent] = date }
                    else { error = collection.errorMessage; break }
                } catch { self.error = "Не удалось импортировать страницу RPA. Исходный файл сохранён для проверки."; break }
            }
        }
    }
}
