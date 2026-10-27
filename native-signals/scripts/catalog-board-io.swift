import Foundation
import CryptoKit
import SignalsCore

// Read-only app projection export and additive, agent-attributed board import.
// The only deletion permitted is an exact, empty initial seed card, through native protections.
// Compile: swiftc -parse-as-library -I .build/debug/Modules scripts/catalog-board-io.swift .build/debug/SignalsCore.build/*.o -o .build/catalog-board-io
// Usage: catalog-board-io export OUTPUT_DIR [--scope all|non-school] [--exclude-segment NAME] [--company ID]
//        catalog-board-io companies OUTPUT_JSON [filters]
//        catalog-board-io validate-data APP_DIR [filters]
//        catalog-board-io validate|apply COMPANY_ID DRAFT_JSON [filters]
//        catalog-board-io validate-batch DRAFT_DIR [filters]
//        catalog-board-io apply-batch DRAFT_DIR RECEIPTS_DIR [filters]
//        catalog-board-io self-test NEW_TEST_DIR [filters]
// Default scope is all non-demo companies. Batch apply preflights every draft before any board write,
// then applies one native transaction per company; it is resumable, not an all-or-nothing cross-company transaction.
// TG_SIGNALS_DATA_DIR may select an isolated test store. No credentials or network clients are used.

private struct Draft: Codable { var companyId: String; var nodes: [BoardNode]; var edges: [BoardEdge] }
private struct SavedBoard: Decodable { var version: Int; var document: BoardDocument }
private enum Failure: Error, LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}
private let iso = ISO8601DateFormatter()
private let taskID = "segments-2026-10-14"
private let schoolSegment = "Школьное образование и экзамены"
private struct Selection {
    var scope = "all"
    var excludedSegments: Set<String> = []
    var companyIDs: Set<String> = []
    func select(_ catalog: [CompanyDossier]) throws -> [CompanyDossier] {
        guard ["all", "non-school"].contains(scope) else { throw Failure.invalid("Scope must be all or non-school") }
        let eligible = catalog.filter { !$0.isDemo && (scope != "non-school" || $0.catalogSegment != schoolSegment) && !excludedSegments.contains($0.catalogSegment) }
        guard companyIDs.isSubset(of: Set(eligible.map(\.id))) else { throw Failure.invalid("Company filter includes unknown, demo or excluded companies") }
        let selected = eligible.filter { companyIDs.isEmpty || companyIDs.contains($0.id) }.sorted { $0.id < $1.id }
        guard !selected.isEmpty else { throw Failure.invalid("Selection is empty") }
        return selected
    }
    var metadata: [String: Any] { ["scope": scope, "excludedSegments": excludedSegments.sorted(), "companyIDs": companyIDs.sorted(), "demosExcluded": true] }
}
private func parseArguments(_ values: [String]) throws -> (positional: [String], selection: Selection) {
    var positional: [String] = []; var selection = Selection(); var index = 0
    while index < values.count {
        let value = values[index]
        if value.hasPrefix("--") {
            guard ["--scope", "--exclude-segment", "--company"].contains(value), index + 1 < values.count, !values[index + 1].hasPrefix("--") else { throw Failure.invalid("Unknown or incomplete option: " + value) }
            let option = values[index + 1]
            switch value {
            case "--scope": selection.scope = option
            case "--exclude-segment": selection.excludedSegments.insert(option)
            case "--company": selection.companyIDs.insert(option)
            default: break
            }
            index += 2
        } else { positional.append(value); index += 1 }
    }
    return (positional, selection)
}
private func companyList(_ companies: [CompanyDossier]) -> [[String: Any]] {
    companies.map { company in
        ["id": company.id, "name": company.name, "website": optional(company.website),
         "segment": company.catalogSegment, "catalogSegment": company.catalogSegment, "isDemo": company.isDemo,
         "product": optional(company.discovery?.product), "audience": optional(company.discovery?.audience),
         "groupNote": optional(company.discovery?.groupNote)]
    }
}
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
    init(companies selectedCompanies: [CompanyDossier]) throws {
        base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        companies = selectedCompanies
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
        value.mergeSourceHistory(companyID: company.id, archives: history)
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
private func export(_ inputs: Inputs, destination: URL, selection: Selection) throws {
    guard !FileManager.default.fileExists(atPath: destination.path) else { throw Failure.invalid("Export destination must be new; refusing to mix or replace snapshots") }
    let packets = destination.appendingPathComponent("packets", isDirectory: true)
    try FileManager.default.createDirectory(at: packets, withIntermediateDirectories: true)
    try write(companyList(inputs.companies), to: destination.appendingPathComponent("companies.json"))
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
            "version": 1, "exportedAt": date(end), "company": try json(company), "catalogSegment": company.catalogSegment, "website": optional(company.website),
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
        manifest.append(["companyId": company.id, "name": company.name, "catalogSegment": company.catalogSegment, "website": optional(company.website), "path": "packets/" + filename,
                         "materials": research.materials.count, "fullTexts": research.materials.filter(\.hasFullText).count,
                         "contacts": research.contacts.count, "eligibleContacts": research.contacts.filter(eligible).count,
                         "datedHiring": hiring.publications.count, "undatedHiring": hiring.undatedPublications.count,
                         "uncertainHiring": hiring.uncertainPublications.count, "existingBoardNodes": board?.nodes.count ?? 0,
                         "existingBoardRevision": optional(board?.revision)])
        fputs("Exported \(company.id): \(research.materials.count) materials, \(research.contacts.count) contacts\n", stderr)
    }
    try write(["version": 1, "exportedAt": date(end), "selection": selection.metadata, "taskId": taskID, "companies": manifest,
               "projection": "CompanyResearch.build + mergeSourceHistory (materials and candidate contacts); HiringResearch.build; BoardWorkspaceView contact eligibility",
               "count": manifest.count, "liveBoardsWritten": false], to: destination.appendingPathComponent("manifest.json"))
    print("Exported \(manifest.count) catalog packets to \(destination.path); no live board writes")
}
private func strictDraftData(_ data: Data) throws -> Draft {
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
private func validateData(_ directory: URL, companies: [CompanyDossier]) throws {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory), isDirectory.boolValue else { throw Failure.invalid("Data directory must exist") }
    let catalogIDs = Set(companies.map(\.id))
    let publicDirectory = directory.appendingPathComponent("public-research")
    let historyDirectory = directory.appendingPathComponent("source-history")
    let publicData = PublicResearchStore(directory: publicDirectory).load()
    let historyData = SourceHistoryStore(directory: historyDirectory).load()
    var errors = publicData.errors + historyData.errors
    var publicFiles = 0, seenPublic: Set<String> = []
    // Native PublicResearchStore uses last-file-wins for company IDs. Check every decoded file
    // too so a duplicate or excluded earlier file cannot disappear from this validation.
    if FileManager.default.fileExists(atPath: publicDirectory.path) {
        for url in try FileManager.default.contentsOfDirectory(at: publicDirectory, includingPropertiesForKeys: nil).filter({ $0.pathExtension == "json" }) {
            publicFiles += 1
            if let record = try? JSONDecoder().decode(PublicResearchRecord.self, from: Data(contentsOf: url)) {
                if !catalogIDs.contains(record.companyID) { errors.append("Public research company outside selected catalog: " + record.companyID) }
                if !seenPublic.insert(record.companyID).inserted { errors.append("Duplicate public research company: " + record.companyID) }
            }
        }
    }
    var seenHistory: Set<String> = []
    for archive in historyData.archives {
        if !catalogIDs.contains(archive.companyID) { errors.append("Source history company outside selected catalog: " + archive.companyID) }
        if !seenHistory.insert(archive.id).inserted { errors.append("Duplicate source history archive ID: " + archive.id) }
    }
    let result: [String: Any] = ["status": errors.isEmpty ? "valid" : "invalid", "dataDir": directory.path,
        "publicResearchFiles": publicFiles, "publicResearchCompanies": publicData.records.count,
        "publicDocuments": publicData.records.values.reduce(0) { $0 + $1.documents.count },
        "publicContacts": publicData.records.values.reduce(0) { $0 + $1.contacts.count },
        "historyArchives": historyData.archives.count, "historyCompanies": Set(historyData.archives.map(\.companyID)).count,
        "historyDocuments": historyData.archives.reduce(0) { $0 + $1.documents.count },
        "errors": errors, "liveBoardsWritten": false,
        "validation": "Native PublicResearchStore and SourceHistoryStore decode; per-file company scope and duplicate container IDs. Content affiliation and factual correctness require separate review."]
    try printJSON(result)
    if !errors.isEmpty { throw Failure.invalid("Data validation failed with \(errors.count) error(s)") }
}
private struct PreparedDraft {
    let company: CompanyDossier
    let draft: Draft
    let research: CompanyResearch
    let dataHash: String
    let operationCount: Int
}
private func prepare(_ inputs: Inputs, company: CompanyDossier, url: URL) throws -> PreparedDraft {
    // Read once: the validated bytes are also the bytes fingerprinted in the receipt.
    let data = try Data(contentsOf: url)
    let draft = try strictDraftData(data)
    let research = inputs.research(company)
    let operations = try plan(draft, company: company, research: research, existing: inputs.board(company))
    return PreparedDraft(company: company, draft: draft, research: research,
                         dataHash: SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(), operationCount: operations.count)
}
private func apply(_ prepared: PreparedDraft, inputs: Inputs) throws -> [String: Any] {
    let company = prepared.company, draft = prepared.draft, research = prepared.research
    // Read board again immediately before touching it; a human edit after preflight must survive.
    let existing = try inputs.board(company)
    let operations = try plan(draft, company: company, research: research, existing: existing)
    var receipt: [String: Any] = ["companyId": company.id, "taskId": taskID, "draftSHA256": prepared.dataHash,
                                  "appliedAt": date(Date()), "operations": operations.count]
    if operations.isEmpty {
        receipt["status"] = "already_present"
        receipt["document"] = try existing.map(json) ?? NSNull()
        return receipt
    }
    let store = BoardDocumentStore(directory: inputs.base.appendingPathComponent("boards"))
    try store.updateReferences(companyID: company.id, title: company.name, sourceIDs: Set(research.materials.map(\.id)), contactIDs: Set(research.contacts.filter(eligible).map(\.id)))
    let state = try store.load(companyID: company.id, title: company.name)
    let refreshed = try plan(draft, company: company, research: research, existing: state.document)
    if refreshed.isEmpty { receipt["status"] = "already_present"; receipt["state"] = try json(state); receipt["operations"] = 0; return receipt }
    let transaction = BoardTransaction(documentId: state.document.id, baseRevision: state.document.revision, actor: .agent, taskId: taskID, operations: refreshed)
    let result = try store.apply(transaction, companyID: company.id, title: company.name)
    receipt["status"] = "applied"; receipt["state"] = try json(result); receipt["operations"] = refreshed.count
    return receipt
}
private func preparedBatch(_ inputs: Inputs, directory: URL) throws -> [PreparedDraft] {
    let urls = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isRegularFileKey]).filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    guard !urls.isEmpty else { throw Failure.invalid("Draft directory has no JSON drafts") }
    let companies = Dictionary(uniqueKeysWithValues: inputs.companies.map { ($0.id, $0) })
    var result: [PreparedDraft] = []; var seen: Set<String> = []
    for url in urls {
        guard try url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else { throw Failure.invalid("Draft must be a regular file: " + url.lastPathComponent) }
        let filenameID = url.deletingPathExtension().lastPathComponent
        guard let company = companies[filenameID] else { throw Failure.invalid("Draft filename outside selected catalog: " + url.lastPathComponent) }
        let value = try prepare(inputs, company: company, url: url)
        guard seen.insert(value.company.id).inserted else { throw Failure.invalid("Duplicate company draft") }
        result.append(value)
        fputs("Validated \(company.id): \(value.operationCount) operations\n", stderr)
    }
    return result
}
private func printJSON(_ value: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    print(String(decoding: data, as: UTF8.self))
}
@main private struct CatalogBoardIO {
    static func main() { do { try run() } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) } }
    static func run() throws {
        let parsed = try parseArguments(Array(CommandLine.arguments.dropFirst()))
        let args = parsed.positional, selection = parsed.selection
        guard args.count >= 2 else { throw Failure.invalid("Usage: catalog-board-io export|companies|self-test|validate-data PATH | validate|apply COMPANY_ID DRAFT_JSON | validate-batch DRAFT_DIR | apply-batch DRAFT_DIR RECEIPTS_DIR [--scope all|non-school] [--exclude-segment NAME] [--company ID]") }
        let companies = try selection.select(CompanyCatalog.loadBundled().companies)
        if args[0] == "companies", args.count == 2 {
            let url = URL(fileURLWithPath: args[1])
            guard !FileManager.default.fileExists(atPath: url.path) else { throw Failure.invalid("Company list destination already exists") }
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try write(companyList(companies), to: url)
            print("Wrote \(companies.count) non-demo companies to \(url.path)"); return
        }
        if args[0] == "validate-data", args.count == 2 { try validateData(URL(fileURLWithPath: args[1], isDirectory: true), companies: companies); return }
        let inputs = try Inputs(companies: companies)
        if args[0] == "export", args.count == 2 { try export(inputs, destination: URL(fileURLWithPath: args[1], isDirectory: true), selection: selection); return }
        if args[0] == "self-test", args.count == 2 { try integrityTests(inputs, destination: URL(fileURLWithPath: args[1], isDirectory: true)); return }
        if (args[0] == "validate-batch" && args.count == 2) || (args[0] == "apply-batch" && args.count == 3) {
            let batch = try preparedBatch(inputs, directory: URL(fileURLWithPath: args[1], isDirectory: true))
            if args[0] == "validate-batch" { try printJSON(["status": "valid", "count": batch.count, "operations": batch.reduce(0) { $0 + $1.operationCount }, "liveBoardsWritten": false]); return }
            let receipts = URL(fileURLWithPath: args[2], isDirectory: true)
            try FileManager.default.createDirectory(at: receipts, withIntermediateDirectories: true)
            var completed: [[String: Any]] = []
            for value in batch {
                do {
                    let result = try apply(value, inputs: inputs)
                    try write(result, to: receipts.appendingPathComponent(value.company.id + ".json"))
                    completed.append(["companyId": value.company.id, "status": result["status"]!, "operations": result["operations"]!, "draftSHA256": value.dataHash])
                    try write(["taskId": taskID, "status": "running", "total": batch.count, "completed": completed], to: receipts.appendingPathComponent("batch-report.json"))
                    fputs("\(result["status"]!): \(value.company.id)\n", stderr)
                } catch {
                    try write(["taskId": taskID, "status": "failed", "total": batch.count, "completed": completed, "failedCompanyId": value.company.id, "error": error.localizedDescription], to: receipts.appendingPathComponent("batch-report.json"))
                    throw error
                }
            }
            let report: [String: Any] = ["taskId": taskID, "status": "complete", "total": batch.count, "completed": completed, "atomicity": "one native agent transaction per company; all drafts preflighted before writes"]
            try write(report, to: receipts.appendingPathComponent("batch-report.json")); try printJSON(report); return
        }
        guard ["validate", "apply"].contains(args[0]), args.count == 3, let company = inputs.companies.first(where: { $0.id == args[1] }) else { throw Failure.invalid("Unknown command or company outside selected catalog") }
        let prepared = try prepare(inputs, company: company, url: URL(fileURLWithPath: args[2]))
        if args[0] == "validate" { try printJSON(["status": "valid", "companyId": company.id, "operations": prepared.operationCount, "draftSHA256": prepared.dataHash, "liveBoardsWritten": false]); return }
        try printJSON(apply(prepared, inputs: inputs))
    }
}
