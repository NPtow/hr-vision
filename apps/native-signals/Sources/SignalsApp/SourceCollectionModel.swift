import Foundation
import SwiftUI
import SignalsCore

@MainActor
final class SourceCollectionModel: ObservableObject {
    @Published private(set) var credentials: [SourceProvider: SourceCredentials] = [:]
    @Published private(set) var verified: Set<SourceProvider> = []
    @Published private(set) var busyProviders: Set<SourceProvider> = []
    @Published private(set) var errors: [SourceProvider: String] = [:]
    @Published private(set) var notices: [SourceProvider: String] = [:]
    @Published private(set) var runs: [SourceCollectionRun] = []
    @Published private(set) var storageError: String?
    @Published private(set) var targetHunterBatch: TargetHunterBatchProgress?
    @Published private(set) var batchReadError: String?
    @Published private(set) var refreshing = false
    private let dataDirectory: URL
    private var loadedRevision: String?
    private let store: SourceCollectionStore
    private let credentialStore = SourceCredentialStore()

    init() {
        let directory: URL
        if let override = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"] {
            directory = URL(fileURLWithPath: override, isDirectory: true)
        } else {
            directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("TG Signals", isDirectory: true)
        }
        dataDirectory = directory
        store = SourceCollectionStore(directoryURL: directory.appendingPathComponent("source-runs", isDirectory: true))
        reload()
        do {
            if let value = try credentialStore.load(.targetHunter) { credentials[.targetHunter] = value }
        } catch { errors[.targetHunter] = error.localizedDescription }
    }

    func reload() {
        do {
            runs = try store.loadRuns().sorted { $0.startedAt > $1.startedAt }
            storageError = nil
        } catch {
            storageError = "Не удалось прочитать историю источников. Существующие файлы сохранены без изменений."
        }
        loadBatchProgress()
        loadedRevision = nil
    }

    /// Called only while the source browser is visible. File reads stay off the main actor.
    func refreshFromDisk(force: Bool = false) async {
        guard !refreshing, busyProviders.isEmpty else { return }
        refreshing = true
        defer { refreshing = false }
        let directory = dataDirectory
        let previousRevision = force ? nil : loadedRevision
        let snapshot = await Task.detached(priority: .utility) {
            SourceDiskSnapshot.read(directory: directory, previousRevision: previousRevision)
        }.value
        guard !Task.isCancelled, busyProviders.isEmpty, let snapshot else { return }
        if let loadedRuns = snapshot.runs {
            runs = loadedRuns
            storageError = nil
        } else {
            storageError = "Не удалось прочитать историю источников. Существующие файлы сохранены без изменений."
        }
        targetHunterBatch = snapshot.batch
        batchReadError = snapshot.batchError
        if snapshot.runs != nil, snapshot.batchError == nil { loadedRevision = snapshot.revision }
    }

    private func loadBatchProgress() {
        let value = SourceDiskSnapshot.readBatch(directory: dataDirectory)
        targetHunterBatch = value.0
        batchReadError = value.1
    }

    func searchRuns(_ provider: SourceProvider, companyID: String? = nil) -> [SourceCollectionRun] {
        runs.filter { $0.provider == provider && $0.request != nil && (companyID == nil || $0.request?.companyID == companyID) }
    }


    func credential(_ provider: SourceProvider) -> SourceCredentials { credentials[provider] ?? SourceCredentials() }
    func setKey(_ value: String, provider: SourceProvider) {
        guard !busyProviders.contains(provider) else { return }
        var next = credential(provider); next.apiKey = value; credentials[provider] = next
        verified.remove(provider); errors[provider] = nil; notices[provider] = nil
    }
    func setFolder(_ value: String, provider: SourceProvider) {
        guard !busyProviders.contains(provider) else { return }
        var next = credential(provider); next.folderID = value; credentials[provider] = next
        verified.remove(provider); errors[provider] = nil; notices[provider] = nil
    }

    func hasCredentials(_ provider: SourceProvider) -> Bool {
        let value = credential(provider)
        return !value.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            (provider != .yandex || !value.folderID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
    func canRequest(_ provider: SourceProvider) -> Bool {
        hasCredentials(provider) && !busyProviders.contains(provider) && storageError == nil
    }
    func status(_ provider: SourceProvider) -> String {
        if busyProviders.contains(provider) { return "Запрос выполняется…" }
        if errors[provider] != nil { return "Не удалось проверить доступ" }
        if verified.contains(provider) { return "Доступ проверен запросом" }
        if !searchRuns(provider).isEmpty { return "Результаты сохранены на Mac" }
        if hasCredentials(provider) { return "Готов к проверке" }
        return provider == .yandex && !credential(provider).apiKey.isEmpty ? "Нужен ID каталога" : "Нужен ключ доступа"
    }

    func saveCredentials(_ provider: SourceProvider) {
        guard hasCredentials(provider), !busyProviders.contains(provider) else { return }
        do {
            let value = credential(provider)
            try credentialStore.save(SourceCredentials(apiKey: value.apiKey.trimmingCharacters(in: .whitespacesAndNewlines), folderID: value.folderID.trimmingCharacters(in: .whitespacesAndNewlines)), for: provider)
            notices[provider] = provider == .targetHunter
                ? "Доступ сохранён локально и будет загружаться автоматически."
                : "Доступ сохранён в Связке ключей этого Mac."
            errors[provider] = nil
        } catch { errors[provider] = error.localizedDescription }
    }
    func loadCredentials(_ provider: SourceProvider) {
        guard !busyProviders.contains(provider) else { return }
        do {
            if let value = try credentialStore.load(provider) {
                credentials[provider] = value; verified.remove(provider)
                notices[provider] = "Ключ загружен. Доступ будет проверен первым запросом."
            } else {
                notices[provider] = provider == .targetHunter
                    ? "Локальный доступ TargetHunter пока не сохранён."
                    : "В Связке ключей пока нет доступа к этому сервису."
            }
            errors[provider] = nil
        } catch { errors[provider] = error.localizedDescription }
    }
    func removeCredentials(_ provider: SourceProvider) {
        guard !busyProviders.contains(provider) else { return }
        do {
            try credentialStore.remove(provider)
            credentials[provider] = SourceCredentials(); verified.remove(provider)
            errors[provider] = nil; notices[provider] = "Сохранённый доступ удалён с этого Mac."
        } catch { errors[provider] = error.localizedDescription }
    }

    func perform(_ provider: SourceProvider, company: CompanyDossier?, query: String) async {
        guard canRequest(provider) else { return }
        let value = credential(provider)
        let cleanQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if provider != .targetHunter {
            guard let company, !company.isDemo, !cleanQuery.isEmpty else { return }
        }
        busyProviders.insert(provider); errors[provider] = nil; notices[provider] = nil
        defer { busyProviders.remove(provider) }
        let client = SourceClient(provider: provider, apiKey: value.apiKey.trimmingCharacters(in: .whitespacesAndNewlines), folderID: value.folderID.trimmingCharacters(in: .whitespacesAndNewlines))
        do {
            let run: SourceCollectionRun
            if provider == .targetHunter {
                run = try await client.discoverTools()
            } else if let company {
                run = try await client.search(SourceSearchRequest(companyID: company.id, companyName: company.name, query: cleanQuery, limit: 10))
            } else { return }
            verified.insert(provider)
            do {
                try store.save(run)
                runs = try store.loadRuns().sorted { $0.startedAt > $1.startedAt }
                notices[provider] = provider == .targetHunter
                    ? "Доступ проверен. Получено инструментов: \(run.tools.count). Результаты поиска компаний доступны в истории."
                    : "Запрос выполнен. Ссылок: \(run.results.count). Проверь связь каждой с выбранной компанией."
            } catch {
                runs.insert(run, at: 0)
                storageError = "Ответ получен, но не сохранён на диск. Оставь приложение открытым и проверь доступ к папке данных."
            }
        } catch {
            verified.remove(provider)
            errors[provider] = error.localizedDescription
        }
    }

    func performHarvest(company: CompanyDossier, operation: HarvestOperation, search: String, targetURL: String, jobTitles: String) async {
        let provider = SourceProvider.harvest
        guard !company.isDemo, canRequest(provider) else { return }
        busyProviders.insert(provider); errors[provider] = nil; notices[provider] = nil
        defer { busyProviders.remove(provider) }
        let query = operation == .companies ? search : "\(operation.title): \(targetURL)"
        do {
            let run = try await HarvestClient(apiKey: credential(provider).apiKey).collect(
                .init(companyID: company.id, companyName: company.name, query: query, limit: 100),
                operation: operation, targetURL: targetURL, jobTitles: jobTitles)
            verified.insert(provider)
            do {
                try store.save(run)
                runs = try store.loadRuns().sorted { $0.startedAt > $1.startedAt }
                notices[provider] = "Сохранено \(run.results.count) записей. Проверь связь с выбранной компанией."
            } catch {
                runs.insert(run, at: 0)
                storageError = "Ответ получен, но не сохранён. Оставь приложение открытым и проверь папку данных."
            }
        } catch {
            verified.remove(provider); errors[provider] = error.localizedDescription
        }
    }

    func attach(_ result: SourceSearchResult, run: SourceCollectionRun, workspace: SignalsWorkspace) {
        guard let company = workspace.companies.first(where: { $0.id == result.companyID && !$0.isDemo }), !workspace.editingDisabled else { return }
        let evidence = result.evidence(collectionID: run.id)
        guard !workspace.evidence(for: company).contains(where: { $0.id == evidence.id }) else { return }
        workspace.update(company) { $0.addedEvidence.append(evidence) }
    }

    func detach(_ result: SourceSearchResult, run: SourceCollectionRun, workspace: SignalsWorkspace) {
        guard let company = workspace.companies.first(where: { $0.id == result.companyID && !$0.isDemo }), !workspace.editingDisabled else { return }
        let identifier = result.evidence(collectionID: run.id).id
        workspace.update(company) {
            $0.addedEvidence.removeAll { $0.id == identifier }
            $0.checkedEvidenceIDs.removeAll { $0 == identifier }
        }
    }
}


struct TargetHunterBatchProgress: Decodable, Sendable {
    let version: Int
    let planID: String
    let updatedAt: Date
    let totalCompanies: Int
    let selectedCompanies: Int
    let created: Int
    let completed: Int
    let imported: Int
    let partial: Int
    let zeroResults: Int
    let pending: Int
    let planned: Int
    let failed: Int
    let uncertain: Int
    let running: Bool
    let receivedResults: Int
    let reportedResults: Int
    let companies: [Company]

    struct Company: Decodable, Sendable {
        let companyID: String
        let companyName: String
        let query: String
        let state: String
        let taskID: String?
        let receivedResultCount: Int?
        let reportedResultCount: Int?
        let resultExtent: String?
        let nativeRunID: String?
        let errorCode: String?
    }
}

private struct SourceDiskSnapshot: Sendable {
    let revision: String
    let runs: [SourceCollectionRun]?
    let batch: TargetHunterBatchProgress?
    let batchError: String?

    static func read(directory: URL, previousRevision: String?) -> SourceDiskSnapshot? {
        let runDirectory = directory.appendingPathComponent("source-runs", isDirectory: true)
        let progressURL = directory.appendingPathComponent("targethunter-batch/coverage.json")
        let revision = [runDirectory, progressURL].map { url -> String in
            guard let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]) else { return "absent" }
            return "\(values.contentModificationDate?.timeIntervalSince1970 ?? 0):\(values.fileSize ?? 0)"
        }.joined(separator: "|")
        guard revision != previousRevision else { return nil }
        let batch = readBatch(directory: directory)
        return SourceDiskSnapshot(revision: revision,
            runs: try? SourceCollectionStore(directoryURL: runDirectory).loadRuns(), batch: batch.0, batchError: batch.1)
    }

    static func readBatch(directory: URL) -> (TargetHunterBatchProgress?, String?) {
        let url = directory.appendingPathComponent("targethunter-batch/coverage.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return (nil, nil) }
        do {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            guard ((attributes[.size] as? NSNumber)?.intValue ?? 2_000_001) <= 2_000_000 else { throw CocoaError(.fileReadCorruptFile) }
            let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
            let batch = try decoder.decode(TargetHunterBatchProgress.self, from: Data(contentsOf: url))
            guard batch.version == 1, batch.totalCompanies >= 0, batch.totalCompanies <= 100_000,
                  batch.companies.count <= 100_000 else { throw CocoaError(.fileReadCorruptFile) }
            return (batch, nil)
        } catch {
            return (nil, "Не удалось прочитать ход сбора VK. Сохранённые результаты остаются доступны.")
        }
    }
}
