import Foundation
import SwiftUI
import SignalsCore

@MainActor
final class TelemetrCollectionModel: ObservableObject {
    @Published var runs: [TelemetrCollectionRun] = []
    @Published var token = "" {
        didSet { if token != oldValue { limits = nil } }
    }
    @Published var limits: TelemetrLimits?
    @Published var busy = false
    @Published var progress = ""
    @Published var errorMessage: String?
    @Published var connectionMessage: String?
    @Published var storageUnavailable = false
    let store: TelemetrCollectionStore
    private let tokenStore = TelemetrTokenStore()

    init() {
        let base: URL
        if let override = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"] {
            base = URL(fileURLWithPath: override, isDirectory: true)
        } else {
            base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("TG Signals", isDirectory: true)
        }
        store = TelemetrCollectionStore(directoryURL: base.appendingPathComponent("collection-runs", isDirectory: true))
        reload()
    }

    var hasToken: Bool { !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var canCollect: Bool { !busy && !storageUnavailable && hasToken && limits != nil && limits?.companySearchBlockReason == nil }

    func reload() {
        do {
            runs = try store.loadRuns().sorted { $0.startedAt > $1.startedAt }
            storageUnavailable = false
            errorMessage = nil
        } catch {
            storageUnavailable = true
            errorMessage = "Не удалось прочитать историю поиска. Сохранённые файлы не изменены. \(error.localizedDescription)"
        }
    }

    func importFile(_ url: URL) {
        guard !busy && !storageUnavailable else { return }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            importData(try Data(contentsOf: url))
        } catch {
            errorMessage = "Файл результатов не импортирован: \(error.localizedDescription)"
        }
    }

    @discardableResult
    func importData(_ data: Data) -> Bool {
        guard !busy && !storageUnavailable else { return false }
        guard data.count <= 32_000_000 else {
            errorMessage = "Файл слишком большой для этой выборки. Максимум — 32 МБ."
            return false
        }
        do {
            try store.importRun(data: data)
            reload()
            connectionMessage = "Результаты из файла добавлены в историю. Связь публикаций с компаниями ещё нужно проверить."
            return !storageUnavailable
        } catch {
            errorMessage = "Файл результатов не импортирован: \(error.localizedDescription)"
            return false
        }
    }

    func setRelevance(_ relevance: TelemetrRelevance, post: TelemetrPost) {
        do {
            try store.updateRelevance(postID: post.id, companyID: post.companyID, relevance: relevance)
            reload()
        } catch {
            errorMessage = "Проверка связи не сохранена: \(error.localizedDescription)"
        }
    }

    func checkConnection() async {
        guard hasToken, !busy else { return }
        busy = true; errorMessage = nil; progress = "Проверяем API и доступные квоты…"
        defer { busy = false; progress = "" }
        do {
            let result = try await TelemetrClient(token: token.trimmingCharacters(in: .whitespacesAndNewlines)).fetchLimits()
            limits = result
            connectionMessage = result.companySearchBlockReason ?? "API доступен для поиска компаний. Проверка квот бесплатна."
        } catch {
            limits = nil
            errorMessage = "Проверка API не выполнена: \(error.localizedDescription)"
        }
    }

    func collectPilot() async {
        guard canCollect else { return }
        busy = true; errorMessage = nil
        progress = "Ищем Бруноям, karpov.courses и SETTERS. До 20 публикаций…"
        defer { busy = false; progress = "" }
        do {
            let run = try await TelemetrClient(token: token.trimmingCharacters(in: .whitespacesAndNewlines)).collectPilot()
            try store.save(run)
            reload()
            limits = run.limitsAfter
            connectionMessage = "Получено \(run.posts.count) публикаций. Проверь связь каждого результата с компанией."
            if let issue = run.errorMessage { errorMessage = issue }
        } catch {
            errorMessage = "Поиск не завершён: \(error.localizedDescription)"
        }
    }

    func saveToken() {
        guard hasToken else { return }
        do {
            try tokenStore.save(token.trimmingCharacters(in: .whitespacesAndNewlines))
            connectionMessage = "Ключ сохранён в Связке ключей этого Mac."
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    func loadToken() {
        do {
            if let stored = try tokenStore.load() {
                token = stored
                connectionMessage = "Ключ загружен. Проверь доступ перед поиском."
            } else { connectionMessage = "В Связке ключей пока нет ключа Telemetr." }
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    func removeToken() {
        do {
            try tokenStore.remove()
            token = ""; limits = nil
            connectionMessage = "Сохранённый ключ удалён с этого Mac."
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    func attach(_ post: TelemetrPost, run: TelemetrCollectionRun, workspace: SignalsWorkspace) {
        guard post.relevance == .confirmed,
              let company = workspace.companies.first(where: { $0.id == post.companyID && !$0.isDemo }) else { return }
        let identifier = evidenceID(post)
        guard !workspace.evidence(for: company).contains(where: { $0.id == identifier }) else { return }
        let provider = post.origin == .telemetrWebsite ? "Telemetr · сайт" : "Telemetr · API"
        let evidence = Evidence(
            id: identifier,
            title: "Telegram · \(post.channelTitle ?? post.companyName)",
            excerpt: post.text,
            url: post.sourceURL ?? post.sourcePageURL,
            dateLabel: post.publishedAt.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? post.sourceDateLabel ?? "Дата публикации не установлена",
            kind: post.sourceDeleted == true ? "Архив удалённого поста Telemetr" : "Публикация Telegram", isDemo: false,
            provenance: EvidenceProvenance(provider: provider, query: post.query,
                retrievedAt: post.retrievedAt.formatted(date: .abbreviated, time: .shortened),
                channel: post.channelTitle, collectionID: run.id,
                contentType: post.sourceDeleted == true ? "Архивная карточка Telemetr; оригинал удалён" : post.textFromCard == true ? "Текст раскрытой карточки Telemetr; без медиа и комментариев" : post.origin == .telemetrWebsite ? "Текст из выдачи Telemetr; может быть сокращён" : "Текст публикации из API Telemetr"))
        workspace.update(company) { $0.addedEvidence.append(evidence) }
    }

    func evidenceID(_ post: TelemetrPost) -> String { "telemetr-\(post.companyID)-\(post.id)" }

    func detach(_ post: TelemetrPost, workspace: SignalsWorkspace) {
        guard let company = workspace.companies.first(where: { $0.id == post.companyID && !$0.isDemo }) else { return }
        let identifier = evidenceID(post)
        workspace.update(company) {
            $0.addedEvidence.removeAll { $0.id == identifier }
            $0.checkedEvidenceIDs.removeAll { $0 == identifier }
        }
    }
}
