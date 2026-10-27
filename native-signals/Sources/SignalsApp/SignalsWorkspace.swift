import Foundation
import SwiftUI
import SignalsCore

@MainActor
final class SignalsWorkspace: ObservableObject {
    let companies: [CompanyDossier]
    let catalogError: String?
    let catalogDuplicateNotes: [String]
    let discoveredCount: Int
    @Published var snapshot: WorkspaceSnapshot
    @Published var persistenceError: String?
    @Published var showAbout = false
    @Published var showTelemetr = false
    @Published var showSources = false
    @Published var savedAt: Date?
    @Published var publicResearch: [String: PublicResearchRecord] = [:]
    @Published var sourceHistory: [SourceHistoryArchive] = []
    @Published var researchErrors: [String] = []
    private let repository: WorkspaceRepository
    private var readFailure = false

    init() {
        do {
            let catalog = try CompanyCatalog.loadBundled()
            companies = catalog.companies
            catalogError = nil
            catalogDuplicateNotes = catalog.duplicateNotes
            discoveredCount = catalog.addedCount
        } catch {
            companies = SeedCatalog.companies
            catalogError = "Расширенный каталог не загружен. Показан только исходный пилот. \(error.localizedDescription)"
            catalogDuplicateNotes = []
            discoveredCount = 0
        }
        let directory: URL
        if let testDirectory = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"] {
            directory = URL(fileURLWithPath: testDirectory, isDirectory: true)
        } else {
            directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("TG Signals", isDirectory: true)
        }
        repository = WorkspaceRepository(fileURL: directory.appendingPathComponent("workspace.json"))
        do {
            snapshot = try repository.load()
        } catch {
            snapshot = WorkspaceSnapshot(version: 1, reviews: [:])
            persistenceError = "Не удалось прочитать сохранённые решения. Файл сохранён без изменений. \(error.localizedDescription)"
            readFailure = true
        }
        reloadPublicResearch()
    }

    func reloadPublicResearch() {
        let loaded = PublicResearchStore().load()
        publicResearch = loaded.records
        let history = SourceHistoryStore().load()
        sourceHistory = history.archives
        researchErrors = loaded.errors + history.errors
    }

    func review(for company: CompanyDossier) -> CompanyReview {
        snapshot.reviews[company.id] ?? .empty
    }

    func evidence(for company: CompanyDossier) -> [Evidence] {
        company.evidence + review(for: company).addedEvidence
    }

    func update(_ company: CompanyDossier, change: (inout CompanyReview) -> Void) {
        guard !readFailure else { return }
        var next = snapshot
        var review = next.reviews[company.id] ?? .empty
        change(&review)
        next.reviews[company.id] = review
        do {
            try repository.save(next)
            snapshot = next
            savedAt = Date()
            persistenceError = nil
        } catch {
            persistenceError = "Изменение не сохранено: \(error.localizedDescription)"
        }
    }

    var editingDisabled: Bool { readFailure }
}
