import Foundation
import SignalsCore

@main struct SchoolAudit {
 static func main() throws {
  let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
  let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TG Signals", isDirectory: true)
  let loaded = PublicResearchStore(directory: root.appendingPathComponent("companies")).load()
  guard loaded.errors.isEmpty, loaded.records.count == 64 else { fatalError("Archive validation failed: \(loaded.errors)") }
  let catalog = try CompanyCatalog.loadBundled()
  let source = try SourceCollectionStore(directoryURL: base.appendingPathComponent("source-runs")).loadRuns()
  let telemetrStore = TelemetrCollectionStore(directoryURL: base.appendingPathComponent("collection-runs"))
  let expandedData = try Data(contentsOf: root.appendingPathComponent("unilogia-full-collection.json"))
  let expanded = try TelemetrCollectionStore.decode(expandedData)
  let telegram = try telemetrStore.loadRuns() + [expanded]
  let snapshot = try WorkspaceRepository(fileURL: base.appendingPathComponent("workspace.json")).load()
  var rows: [[String: Any]] = []
  for company in catalog.companies where loaded.records[company.id] != nil {
   let research = CompanyResearch.build(company: company, evidence: company.evidence + (snapshot.reviews[company.id]?.addedEvidence ?? []), sourceRuns: source, telegramRuns: telegram, publicResearch: loaded.records[company.id])
   let excluded = Set(loaded.records[company.id]!.channels.filter { $0.basis == "excludedReference" }.map { CompanyResearch.canonicalURL($0.url).lowercased() })
   precondition(research.publishedContacts.allSatisfy { !excluded.contains(CompanyResearch.canonicalURL($0.address).lowercased()) })
   precondition(research.cooperationContacts.allSatisfy { !$0.sourceQuote.isEmpty && !$0.sourceURLs.isEmpty })
   if company.id == "discovery-lomonosov-school" {
    for post in expanded.posts { precondition(research.fullMaterials.contains { $0.evidence.contains { $0.id == "telemetr-\(company.id)-\(post.id)" } }, "Expanded post absent") }
   }
   rows.append(["id":company.id,"name":company.name,"fullTexts":research.fullMaterials.count,"fragments":research.fragments.count,"publishedContacts":research.publishedContacts.count,"cooperation":research.cooperationContacts.count,"linkedPeople":research.linkedPeople.count])
  }
  precondition(rows.count == 64)
  let totals: [String: Any] = ["schools":rows.count,"fullTexts":rows.reduce(0){$0 + ($1["fullTexts"] as! Int)},"publishedContacts":rows.reduce(0){$0 + ($1["publishedContacts"] as! Int)},"cooperation":rows.reduce(0){$0 + ($1["cooperation"] as! Int)},"companies":rows]
  try JSONSerialization.data(withJSONObject: totals, options: [.prettyPrinted,.sortedKeys]).write(to: root.appendingPathComponent("projection-audit.json"), options: .atomic)
  print("Validated 64 company archives; \(totals["fullTexts"]!) full texts; \(totals["publishedContacts"]!) published contacts; \(totals["cooperation"]!) cooperation contacts.")
  if CommandLine.arguments.contains("--publish") {
   let target=base.appendingPathComponent("public-research")
   try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
   let backup=root.appendingPathComponent("previous-public-research")
   try FileManager.default.createDirectory(at: backup, withIntermediateDirectories: true)
   for (id,record) in loaded.records {
    let dest=target.appendingPathComponent(id + ".json")
    if FileManager.default.fileExists(atPath: dest.path) && !FileManager.default.fileExists(atPath: backup.appendingPathComponent(id + ".json").path) { try FileManager.default.copyItem(at: dest, to: backup.appendingPathComponent(id + ".json")) }
    let encoder=JSONEncoder(); encoder.outputFormatting=[.prettyPrinted,.sortedKeys,.withoutEscapingSlashes]
    try encoder.encode(record).write(to:dest, options:.atomic)
   }
   _ = try telemetrStore.importRun(data: expandedData)
   print("Installed public research and 8 expanded Telemetr cards.")
  }
 }
}
