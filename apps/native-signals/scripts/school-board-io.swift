import Foundation
import CryptoKit
import SignalsCore

// Read-only app projection export and additive, agent-attributed board import.
// The only deletion permitted is an exact, empty initial seed card, through native protections.
// Compile: swiftc -parse-as-library -I .build/debug/Modules scripts/school-board-io.swift .build/debug/SignalsCore.build/*.o -o .build/school-board-io
// Usage: school-board-io export OUTPUT_DIR | validate COMPANY_ID DRAFT_JSON | apply COMPANY_ID DRAFT_JSON
// TG_SIGNALS_DATA_DIR may select an isolated test store. No credentials or network clients are used.

private struct Draft: Codable { var companyId: String; var nodes: [BoardNode]; var edges: [BoardEdge] }
private struct SavedBoard: Decodable { var version: Int; var document: BoardDocument }
private enum Failure: Error, LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}
private let iso = ISO8601DateFormatter()
private func date(_ value: Date?) -> Any { value.map { iso.string(from: $0) } ?? NSNull() as Any }
private func optional<T>(_ value: T?) -> Any { value as Any? ?? NSNull() }
private func json<T: Encodable>(_ value: T) throws -> Any {
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    return try JSONSerialization.jsonObject(with: encoder.encode(value), options: .fragmentsAllowed)
}
private func write(_ value: Any, to url: URL) throws {
    try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]).write(to: url, options: .atomic)
}
private func eligible(_ contact: ResearchContact) -> Bool {
    contact.affiliation == .published || (contact.kind == .person && contact.affiliation == .profileCompany)
}
private struct Inputs {
    let base: URL
    let companies: [CompanyDossier]
    let snapshot: WorkspaceSnapshot
    let source: [SourceCollectionRun]
    let telegram: [TelemetrCollectionRun]
    let publicData: [String: PublicResearchRecord]
    let history: [SourceHistoryArchive]
    init() throws {
        base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        companies = try CompanyCatalog.loadBundled().companies.filter { !$0.isDemo && $0.catalogSegment == "Школьное образование и экзамены" }.sorted { $0.id < $1.id }
        guard companies.count == 64 else { throw Failure.invalid("Expected 64 non-demo school companies; found \(companies.count)") }
        snapshot = try WorkspaceRepository(fileURL: base.appendingPathComponent("workspace.json")).load()
        source = try SourceCollectionStore(directoryURL: base.appendingPathComponent("source-runs")).loadRuns()
        telegram = try TelemetrCollectionStore(directoryURL: base.appendingPathComponent("collection-runs")).loadRuns()
        let archive = PublicResearchStore(directory: base.appendingPathComponent("public-research")).load()
        let historyData = SourceHistoryStore(directory: base.appendingPathComponent("source-history")).load()
        guard archive.errors.isEmpty && historyData.errors.isEmpty else { throw Failure.invalid((archive.errors + historyData.errors).joined(separator: "\n")) }
        publicData = archive.records; history = historyData.archives
    }
    func research(_ company: CompanyDossier) -> CompanyResearch {
        var value = CompanyResearch.build(company: company, evidence: company.evidence + (snapshot.reviews[company.id]?.addedEvidence ?? []), sourceRuns: source, telegramRuns: telegram, publicResearch: publicData[company.id])
        value.materials = SourceHistoryMaterials.merge(companyID: company.id, materials: value.materials, archives: history)
        return value
    }
    func board(_ company: CompanyDossier) throws -> BoardDocument? {
        let url = BoardDocumentStore(directory: base.appendingPathComponent("boards")).fileURL(companyID: company.id)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let saved = try JSONDecoder().decode(SavedBoard.self, from: Data(contentsOf: url))
        guard saved.version == 1, saved.document.companyId == company.id, saved.document.id == "board:" + company.id else { throw Failure.invalid("Invalid existing board identity") }
        return saved.document
    }
}
private func material(_ item: ResearchMaterial) throws -> [String: Any] {
    ["id": item.id, "title": item.title, "text": item.text, "url": optional(item.url), "dateLabel": item.dateLabel,
     "publishedAt": date(item.publishedAt), "retrievedAt": date(item.retrievedAt), "kind": item.kind.rawValue,
     "providers": item.providers, "quality": item.quality, "relationship": item.relationship,
     "evidence": try json(item.evidence), "originalAvailable": item.originalAvailable, "isOfficialDomain": item.isOfficialDomain,
     "hasFullText": item.hasFullText, "author": optional(item.author), "boardEligible": true]
}
private func contact(_ item: ResearchContact) throws -> [String: Any] {
    ["id": item.id, "name": item.name, "role": item.role, "address": item.address, "kind": item.kind.rawValue,
     "affiliation": item.affiliation.rawValue, "explanation": item.explanation, "materialIDs": item.materialIDs,
     "sourceURLs": item.sourceURLs, "retrievedAt": date(item.retrievedAt), "positions": try json(item.positions),
     "query": item.query, "purpose": item.purpose.rawValue, "purposeLabel": item.purpose.label,
     "sourceQuote": item.sourceQuote, "boardEligible": eligible(item)]
}
private func publication(_ item: HiringPublication) -> [String: Any] {
    ["id": item.id, "companyID": item.companyID, "title": item.title, "function": item.function,
     "publishedAt": date(item.publishedAt), "source": item.source, "sourceID": optional(item.sourceID), "url": optional(item.url),
     "status": item.status, "statusObservedAt": date(item.statusObservedAt), "quote": item.quote, "text": item.text,
     "relationship": item.relationship, "salary": optional(item.salary), "location": optional(item.location),
     "employment": optional(item.employment), "employerID": optional(item.employerID), "contact": optional(item.contact),
     "materialID": item.materialID, "lastObservedAt": date(item.lastObservedAt), "uncertainty": item.uncertainty,
     "isConfirmed": item.isConfirmed, "possibleRepeatCount": item.possibleRepeatCount,
     "observations": item.observations.map { ["id": $0.id, "observedAt": date($0.observedAt), "status": $0.status, "text": $0.text, "url": optional($0.url)] }]
}
private func export(_ inputs: Inputs, destination: URL) throws {
    let packets = destination.appendingPathComponent("packets", isDirectory: true)
    try FileManager.default.createDirectory(at: packets, withIntermediateDirectories: true)
    let end = Date(); var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "Europe/Moscow")!
    let start = calendar.date(byAdding: .month, value: -6, to: calendar.startOfDay(for: end))!
    var manifest: [[String: Any]] = []
    for company in inputs.companies {
        let research = inputs.research(company)
        let hiring = HiringResearch.build(companyID: company.id, materials: research.materials, archives: inputs.history, start: start, end: end)
        let board = try inputs.board(company)
        let filename = company.id + ".json"
        guard filename.range(of: "^[A-Za-z0-9_-]+\\.json$", options: .regularExpression) != nil else { throw Failure.invalid("Unsafe company ID") }
        let packet: [String: Any] = [
            "version": 1, "exportedAt": date(end), "company": try json(company),
            "materials": try research.materials.map(material), "contacts": try research.contacts.map(contact),
            "peopleSearchPerformed": research.peopleSearchPerformed, "collectionNotes": research.collectionNotes,
            "publicResearch": try research.publicResearch.map(json) ?? NSNull(),
            "sourceHistory": try json(inputs.history.filter { $0.companyID == company.id }),
            "existingBoard": try board.map(json) ?? NSNull(),
            "hiring": ["start": date(start), "end": date(end), "publications": hiring.publications.map(publication),
                       "undatedPublications": hiring.undatedPublications.map(publication), "uncertainPublications": hiring.uncertainPublications.map(publication),
                       "outsideWindowCount": hiring.outsideWindowCount, "sourceCoverage": try json(hiring.sourceCoverage), "notes": hiring.notes,
                       "monthlyCounts": hiring.monthlyCounts.map { ["id": $0.id, "label": $0.label, "count": $0.count, "publicationIDs": $0.publicationIDs] },
                       "roleFunctions": hiring.roleFunctions.map { ["function": $0.function, "count": $0.count, "publicationIDs": $0.publicationIDs] }]
        ]
        try write(packet, to: packets.appendingPathComponent(filename))
        manifest.append(["companyId": company.id, "name": company.name, "path": "packets/" + filename,
                         "materials": research.materials.count, "fullTexts": research.materials.filter(\.hasFullText).count,
                         "contacts": research.contacts.count, "eligibleContacts": research.contacts.filter(eligible).count,
                         "datedHiring": hiring.publications.count, "undatedHiring": hiring.undatedPublications.count,
                         "uncertainHiring": hiring.uncertainPublications.count, "existingBoardNodes": board?.nodes.count ?? 0,
                         "existingBoardRevision": optional(board?.revision)])
        fputs("Exported \(company.id): \(research.materials.count) materials, \(research.contacts.count) contacts\n", stderr)
    }
    try write(["version": 1, "exportedAt": date(end), "segment": "Школьное образование и экзамены", "companies": manifest,
               "projection": "CompanyResearch.build + SourceHistoryMaterials.merge; HiringResearch.build; BoardWorkspaceView contact eligibility",
               "count": manifest.count, "liveBoardsWritten": false], to: destination.appendingPathComponent("manifest.json"))
    print("Exported \(manifest.count) school packets to \(destination.path); no live board writes")
}
private func strictDraft(_ url: URL) throws -> Draft {
    let data = try Data(contentsOf: url)
    guard data.count <= 2_000_000, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          Set(object.keys) == ["companyId", "nodes", "edges"], let nodes = object["nodes"] as? [[String: Any]], let edges = object["edges"] as? [[String: Any]] else { throw Failure.invalid("Expected exactly {companyId,nodes,edges}") }
    let requiredNodes: Set<String> = ["id", "kind", "title", "body", "status", "position"]
    for node in nodes {
        guard requiredNodes.isSubset(of: Set(node.keys)), Set(node.keys).isSubset(of: requiredNodes.union(["referenceId"])),
              let p = node["position"] as? [String: Any], Set(p.keys) == ["x", "y"] else { throw Failure.invalid("Unknown or missing node fields") }
    }
    for edge in edges { guard Set(edge.keys) == ["id", "source", "target", "kind"] else { throw Failure.invalid("Unknown or missing edge fields") } }
    return try JSONDecoder().decode(Draft.self, from: data)
}
private func plan(_ draft: Draft, company: CompanyDossier, research: CompanyResearch, existing: BoardDocument?) throws -> [BoardOperation] {
    guard draft.companyId == company.id, !draft.nodes.isEmpty, draft.nodes.count + draft.edges.count <= 500,
          Set(draft.nodes.map(\.id)).count == draft.nodes.count, Set(draft.edges.map(\.id)).count == draft.edges.count else { throw Failure.invalid("Wrong company, empty draft, duplicate IDs or over 500 operations") }
    let oldNodes = Dictionary(uniqueKeysWithValues: (existing?.nodes ?? []).map { ($0.id, $0) })
    let oldEdges = Dictionary(uniqueKeysWithValues: (existing?.edges ?? []).map { ($0.id, $0) })
    let sources = Set(research.materials.map(\.id)), contacts = Set(research.contacts.filter(eligible).map(\.id))
    let ids = Set(draft.nodes.map(\.id)).union(oldNodes.keys)
    var operations: [BoardOperation] = []
    for node in draft.nodes {
        guard !node.id.isEmpty, node.id.count <= 250, !node.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, node.title.count <= 4_000, node.body.count <= 200_000,
              node.position.x.isFinite, node.position.y.isFinite, abs(node.position.x) <= 1_000_000, abs(node.position.y) <= 1_000_000,
              node.status == .proposed || node.status == .requiresVerification else { throw Failure.invalid("Invalid node fields/status: \(node.id)") }
        switch node.kind {
        case .source: guard let ref = node.referenceId, sources.contains(ref) else { throw Failure.invalid("Source not in current app projection: \(node.id)") }
        case .contact: guard let ref = node.referenceId, contacts.contains(ref) else { throw Failure.invalid("Contact not currently board eligible: \(node.id)") }
        default: guard node.referenceId == nil else { throw Failure.invalid("Reference on non-source/contact node: \(node.id)") }
        }
        if let old = oldNodes[node.id] { guard old == node else { throw Failure.invalid("ID collision with existing node; refusing to replace: \(node.id)") } }
        else { operations.append(.createNode(node)) }
    }
    for edge in draft.edges {
        guard !edge.id.isEmpty, edge.id.count <= 250, edge.source != edge.target, ids.contains(edge.source), ids.contains(edge.target) else { throw Failure.invalid("Invalid edge: \(edge.id)") }
        if let old = oldEdges[edge.id] { guard old == edge else { throw Failure.invalid("ID collision with existing edge; refusing to replace: \(edge.id)") } }
        else { operations.append(.connect(edge)) }
    }
    // Remove empty starter placeholders only on a substantive import, without bypassing native
    // humanFields/lease validation. User-authored content, positions, statuses and links survive.
    if !operations.isEmpty {
        let seeds = [
            BoardNode(id: "initial-offer", kind: .offer, title: "Оффер", position: .init(x: 80, y: 100)),
            BoardNode(id: "initial-recipient", kind: .note, title: "Получатель", position: .init(x: 470, y: 100))
        ]
        for seed in seeds where oldNodes[seed.id] == seed && !draft.nodes.contains(where: { $0.id == seed.id }) {
            let linked = (existing?.edges ?? []) + draft.edges
            if !linked.contains(where: { $0.source == seed.id || $0.target == seed.id }) { operations.insert(.deleteNode(nodeId: seed.id), at: 0) }
        }
    }
    guard operations.count <= 500 else { throw Failure.invalid("Over 500 operations including empty seed cleanup") }
    return operations
}
private func integrityTests(_ inputs: Inputs, destination: URL) throws {
    guard !FileManager.default.fileExists(atPath: destination.path), let company = inputs.companies.first else { throw Failure.invalid("Test directory must be new") }
    try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
    let research = inputs.research(company)
    guard let source = research.materials.first else { throw Failure.invalid("Test company has no material") }
    let store = BoardDocumentStore(directory: destination.appendingPathComponent("boards"))
    try store.updateReferences(companyID: company.id, title: company.name, sourceIDs: Set(research.materials.map(\.id)), contactIDs: Set(research.contacts.filter(eligible).map(\.id)))
    let initial = try store.load(companyID: company.id, title: company.name)
    let draft = Draft(companyId: company.id, nodes: [
        BoardNode(id: "test-source", kind: .source, title: source.title, referenceId: source.id, position: .init(x: 80, y: 100)),
        BoardNode(id: "test-observation", kind: .observation, title: "Integrity test", body: "Not a production board", position: .init(x: 430, y: 100))
    ], edges: [BoardEdge(id: "test-edge", source: "test-source", target: "test-observation", kind: .supports)])
    var checks: [String] = []
    func expect(_ value: Bool, _ message: String) throws { guard value else { throw Failure.invalid("TEST FAILED: " + message) }; checks.append(message) }
    func rejects(_ body: () throws -> Void, _ message: String) throws {
        do { try body() } catch { checks.append(message); return }
        throw Failure.invalid("TEST FAILED: " + message)
    }
    let ops = try plan(draft, company: company, research: research, existing: initial.document)
    let applied = try store.apply(.init(documentId: initial.document.id, baseRevision: initial.document.revision, actor: .agent, operations: ops), companyID: company.id, title: company.name)
    try expect(applied.document.nodes.count == 2 && applied.document.edges.count == 1, "Exact empty seeds removed through one native agent transaction")
    try expect(try plan(draft, company: company, research: research, existing: applied.document).isEmpty, "Identical draft replay is a no-op")
    var invalid = draft; invalid.nodes[0].referenceId = "missing-reference"
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Unknown source rejected")
    invalid = draft; invalid.nodes[0].kind = .contact
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Source ID cannot become contact")
    invalid = draft; invalid.companyId = "another-company"
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Other company rejected")
    invalid = draft; invalid.nodes[0].status = .accepted
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Agent cannot accept a card")
    invalid = draft; invalid.edges[0].target = "missing-node"
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Dangling edge rejected")
    invalid = draft; invalid.nodes.append(invalid.nodes[0])
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Duplicate node ID rejected")
    invalid = draft; invalid.nodes[0].position.x = .infinity
    try rejects({ _ = try plan(invalid, company: company, research: research, existing: applied.document) }, "Nonfinite position rejected")
    let edited = try store.apply(.init(documentId: applied.document.id, baseRevision: applied.document.revision, actor: .human,
        operations: [.setNodeFields(nodeId: "test-observation", fields: .init(body: "Human correction"))]), companyID: company.id, title: company.name)
    let bytes = try Data(contentsOf: store.fileURL(companyID: company.id))
    try rejects({ _ = try plan(draft, company: company, research: research, existing: edited.document) }, "Collision with human correction rejected")
    try expect(try Data(contentsOf: store.fileURL(companyID: company.id)) == bytes, "Rejected draft leaves file byte-identical")
    let undone = try store.undo(companyID: company.id, title: company.name, baseRevision: edited.document.revision)
    try expect(undone.document.nodes.first(where: { $0.id == "test-observation" })?.body == "Not a production board", "Native undo history survives import")
    let protectedStore = BoardDocumentStore(directory: destination.appendingPathComponent("protected-boards"))
    try protectedStore.updateReferences(companyID: company.id, title: company.name, sourceIDs: Set(research.materials.map(\.id)), contactIDs: Set(research.contacts.filter(eligible).map(\.id)))
    let p0 = try protectedStore.load(companyID: company.id, title: company.name)
    let p1 = try protectedStore.apply(.init(documentId: p0.document.id, baseRevision: p0.document.revision, actor: .human,
        operations: [.moveNodes([.init(nodeId: "initial-offer", x: 100, y: 100)])]), companyID: company.id, title: company.name)
    let p2 = try protectedStore.apply(.init(documentId: p1.document.id, baseRevision: p1.document.revision, actor: .human,
        operations: [.moveNodes([.init(nodeId: "initial-offer", x: 80, y: 100)])]), companyID: company.id, title: company.name)
    let protectedOps = try plan(draft, company: company, research: research, existing: p2.document)
    let protectedBytes = try Data(contentsOf: protectedStore.fileURL(companyID: company.id))
    try rejects({ _ = try protectedStore.apply(.init(documentId: p2.document.id, baseRevision: p2.document.revision, actor: .agent, operations: protectedOps), companyID: company.id, title: company.name) }, "Native human protection blocks cleanup even after human restored seed geometry")
    try expect(try Data(contentsOf: protectedStore.fileURL(companyID: company.id)) == protectedBytes, "Protected transaction is atomic with no partial seed deletions")
    try write(["passed": checks, "count": checks.count, "liveBoardsWritten": false], to: destination.appendingPathComponent("report.json"))
    print("Passed \(checks.count) isolated integrity checks")
}
@main private struct SchoolBoardIO {
    static func main() {
        do { try run() } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
    }
    static func run() throws {
        let args = CommandLine.arguments
        guard args.count >= 3 else { throw Failure.invalid("Usage: school-board-io export OUTPUT_DIR | validate COMPANY_ID DRAFT_JSON | apply COMPANY_ID DRAFT_JSON") }
        let inputs = try Inputs()
        if args[1] == "export", args.count == 3 { try export(inputs, destination: URL(fileURLWithPath: args[2], isDirectory: true)); return }
        if args[1] == "self-test", args.count == 3 { try integrityTests(inputs, destination: URL(fileURLWithPath: args[2], isDirectory: true)); return }
        guard ["validate", "apply"].contains(args[1]), args.count == 4,
              let company = inputs.companies.first(where: { $0.id == args[2] }) else { throw Failure.invalid("Unknown command or company outside school segment") }
        let draft = try strictDraft(URL(fileURLWithPath: args[3])), research = inputs.research(company)
        let existing = try inputs.board(company)
        let operations = try plan(draft, company: company, research: research, existing: existing)
        if args[1] == "validate" { print("Valid: \(company.id), \(operations.count) create-only operations; no writes"); return }
        if operations.isEmpty { print("Already present: \(company.id); no writes"); return }
        let store = BoardDocumentStore(directory: inputs.base.appendingPathComponent("boards"))
        try store.updateReferences(companyID: company.id, title: company.name, sourceIDs: Set(research.materials.map(\.id)), contactIDs: Set(research.contacts.filter(eligible).map(\.id)))
        let state = try store.load(companyID: company.id, title: company.name)
        let refreshed = try plan(draft, company: company, research: research, existing: state.document)
        if refreshed.isEmpty { print("Already present after refresh: \(company.id)"); return }
        let transaction = BoardTransaction(documentId: state.document.id, baseRevision: state.document.revision, actor: .agent,
            taskId: "school-boards-2026-10-12", operations: refreshed)
        let result = try store.apply(transaction, companyID: company.id, title: company.name)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        print(String(decoding: try encoder.encode(result), as: UTF8.self))
    }
}
