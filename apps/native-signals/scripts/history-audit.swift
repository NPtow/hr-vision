import Foundation
import SignalsCore

@main struct HistoryAudit {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TG Signals")
        var archives: [SourceHistoryArchive] = []
        for tool in ["vk", "hiring"] {
            let loaded = SourceHistoryStore(directory: root.appendingPathComponent(tool + "/archives")).load()
            guard loaded.errors.isEmpty else { throw NSError(domain: "HistoryAudit", code: 1, userInfo: [NSLocalizedDescriptionKey: loaded.errors.joined(separator: "\n")]) }
            archives += loaded.archives
        }
        let catalog = try CompanyCatalog.loadBundled()
        let schools = catalog.companies.filter { !$0.isDemo && $0.catalogSegment == "Школьное образование и экзамены" }
        let schoolIDs = Set(schools.map(\.id))
        guard schools.count == 64, archives.allSatisfy({ schoolIDs.contains($0.companyID) && $0.isValid }) else { fatalError("Invalid school archive") }
        if CommandLine.arguments.contains("--publish-only") {
            let target = base.appendingPathComponent("source-history")
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            for archive in archives {
                guard archive.id.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { fatalError("Unsafe archive id") }
                try encoder.encode(archive).write(to: target.appendingPathComponent(archive.id + ".json"), options: .atomic)
            }
            print("Published \(archives.count) validated archives, \(archives.reduce(0) { $0 + $1.documents.count }) documents.")
            return
        }
        let source = try SourceCollectionStore(directoryURL: base.appendingPathComponent("source-runs")).loadRuns()
        let telegram = try TelemetrCollectionStore(directoryURL: base.appendingPathComponent("collection-runs")).loadRuns()
        let snapshot = try WorkspaceRepository(fileURL: base.appendingPathComponent("workspace.json")).load()
        let publicData = PublicResearchStore().load()
        let end = Date(), start = HistoryDate.parse("2026-04-12T00:00:00+03:00")!
        var rows: [[String: Any]] = [], inspection: [[String: Any]] = []
        for company in schools {
            fputs("Auditing \(company.name)\n", stderr)
            let research = CompanyResearch.build(company: company, evidence: company.evidence + (snapshot.reviews[company.id]?.addedEvidence ?? []), sourceRuns: source, telegramRuns: telegram, publicResearch: publicData.records[company.id])
            let materials = SourceHistoryMaterials.merge(companyID: company.id, materials: research.materials, archives: archives)
            let hiring = HiringResearch.build(companyID: company.id, materials: materials, archives: archives, start: start, end: end)
            let own = archives.filter { $0.companyID == company.id }
            rows.append(["id": company.id, "name": company.name, "archives": own.count,
                         "vkDocuments": own.filter { $0.provider == "VK" }.reduce(0) { $0 + $1.documents.count },
                         "datedHiring": hiring.publications.count, "undatedHiring": hiring.undatedPublications.count,
                         "uncertainHiring": hiring.uncertainPublications.count, "fullMaterials": materials.filter(\.hasFullText).count,
                         "coverageChecks": hiring.sourceCoverage.count])
            for item in hiring.publications + hiring.uncertainPublications + hiring.undatedPublications {
                inspection.append(["companyID": company.id, "company": company.name, "title": item.title,
                                   "url": item.url ?? "", "publishedAt": item.publishedAt.map { ISO8601DateFormatter().string(from: $0) } ?? "",
                                   "confirmed": item.isConfirmed, "uncertainty": item.uncertainty,
                                   "materialID": item.materialID, "text": item.text])
            }
        }
        let summary: [String: Any] = ["schools": schools.count, "archives": archives.count,
          "documents": archives.reduce(0) { $0 + $1.documents.count },
          "datedHiring": rows.reduce(0) { $0 + ($1["datedHiring"] as! Int) }, "companies": rows]
        try JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted,.sortedKeys]).write(to: root.appendingPathComponent("projection-audit.json"), options: .atomic)
        try JSONSerialization.data(withJSONObject: inspection, options: [.prettyPrinted,.sortedKeys]).write(to: root.appendingPathComponent("hiring-inspection.json"), options: .atomic)
        print("Validated \(schools.count) schools; \(archives.count) archives; \(summary["documents"]!) documents; \(summary["datedHiring"]!) dated hiring publications.")
        if CommandLine.arguments.contains("--publish") {
            let target = base.appendingPathComponent("source-history")
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted,.sortedKeys,.withoutEscapingSlashes]
            for archive in archives {
                guard archive.id.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { fatalError("Unsafe archive id") }
                try encoder.encode(archive).write(to: target.appendingPathComponent(archive.id + ".json"), options: .atomic)
            }
            print("Published validated archives to application.")
        }
    }
}
