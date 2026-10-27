import XCTest
@testable import SignalsCore

final class BoardDocumentTests: XCTestCase {
    private var directory: URL!
    private var store: BoardDocumentStore!
    override func setUpWithError() throws { directory = FileManager.default.temporaryDirectory.appendingPathComponent("board-tests-" + UUID().uuidString); store = BoardDocumentStore(directory: directory) }
    override func tearDownWithError() throws { if let directory { try? FileManager.default.removeItem(at: directory) } }
    private func load() throws -> BoardDocumentState { try store.load(companyID: "school", title: "Школа") }
    @discardableResult private func apply(_ operations: [BoardOperation], actor: BoardActor = .human, base: Int? = nil, id: String = UUID().uuidString) throws -> BoardDocumentState {
        let current = try load()
        return try store.apply(.init(transactionId: id, documentId: current.document.id, baseRevision: base ?? current.document.revision, actor: actor, operations: operations), companyID: "school", title: "Школа")
    }
    func testSeedIsTwoEmptyTargetsAndCompanyIsIsolated() throws {
        let state = try load()
        XCTAssertEqual(state.document.nodes.map(\.title), ["Оффер", "Получатель"])
        XCTAssertTrue(state.document.nodes.allSatisfy { $0.body.isEmpty && $0.referenceId == nil })
        XCTAssertEqual(state.document.revision, 0)
        XCTAssertFalse(state.canUndo)
        try apply([.setNodeFields(nodeId: "initial-offer", fields: .init(body: "Текст человека"))])
        let other = try store.load(companyID: "other", title: "Другая школа")
        XCTAssertEqual(other.document.revision, 0)
        XCTAssertTrue(other.document.nodes[0].body.isEmpty)
    }
    func testTransactionProtocolAndRoleRules() throws {
        let json = #"{"transactionId":"t","documentId":"board:school","baseRevision":0,"actor":"agent","operations":[{"type":"createNode","node":{"id":"a","kind":"hypothesis","title":"Проверить","body":"","status":"proposed","position":{"x":0,"y":0}}}]}"#
        let transaction = try JSONDecoder().decode(BoardTransaction.self, from: Data(json.utf8))
        let result = try store.apply(transaction, companyID: "school", title: "Школа")
        XCTAssertEqual(result.document.nodes.last?.status, .proposed)
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "a", fields: .init(status: .accepted))], actor: .agent))
        let accepted = try apply([.setNodeFields(nodeId: "a", fields: .init(status: .accepted))])
        XCTAssertEqual(accepted.document.nodes.last?.status, .accepted)
        XCTAssertThrowsError(try apply([.deleteNode(nodeId: "a")], actor: .agent))
    }
    func testHumanEditsAreProtectedAndIndependentFieldsRebase() throws {
        try apply([.createNode(.init(id: "n", kind: .note, title: "Черновик"))], actor: .agent)
        let revision = try load().document.revision
        try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Человек изменил текст"))])
        let moved = try apply([.moveNodes([.init(nodeId: "n", x: 90, y: 20)])], actor: .agent, base: revision)
        XCTAssertEqual(moved.document.nodes.last?.position.x, 90)
        XCTAssertEqual(moved.document.nodes.last?.body, "Человек изменил текст")
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Перезаписать"))], actor: .agent))
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Старая правка"))], base: revision))
    }
    func testIdempotencySurvivesReloadAndRejectsReusedIDs() throws {
        let op = BoardOperation.createNode(.init(id: "n", kind: .note, title: "Один"))
        let transaction = BoardTransaction(transactionId: "once", documentId: "board:school", baseRevision: 0, actor: .human, operations: [op])
        let first = try store.apply(transaction, companyID: "school", title: "Школа")
        store = BoardDocumentStore(directory: directory)
        let repeatResult = try store.apply(transaction, companyID: "school", title: "Школа")
        XCTAssertEqual(first.document, repeatResult.document)
        XCTAssertEqual(repeatResult.ackTransactionId, "once")
        var different = transaction; different.operations = [.deleteNode(nodeId: "n")]
        XCTAssertThrowsError(try store.apply(different, companyID: "school", title: "Школа"))
    }
    func testInvalidMultiOperationTransactionIsAtomic() throws {
        let before = try load().document
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "initial-offer", fields: .init(body: "Не сохранить")), .connect(.init(id: "bad", source: "missing", target: "initial-offer", kind: .supports))]))
        XCTAssertEqual(try load().document, before)
    }
    func testDeleteCascadesEdgesAndStaleCommandCannotResurrectNode() throws {
        try apply([.createNode(.init(id: "n", kind: .note, title: "Удаляемая")), .connect(.init(id: "e", source: "n", target: "initial-offer", kind: .supports))])
        let stale = try load().document.revision
        let deleted = try apply([.deleteNode(nodeId: "n")])
        XCTAssertTrue(deleted.document.edges.isEmpty)
        XCTAssertThrowsError(try apply([.createNode(.init(id: "n", kind: .note, title: "Старая"))], base: stale))
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Старая"))], base: stale))
    }
    func testUndoRedoOneJournalSurvivesReloadAndKeepsRevisionMonotonic() throws {
        try apply([.createNode(.init(id: "n", kind: .note, title: "Агент"))], actor: .agent)
        try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Дополнение человека"))])
        XCTAssertThrowsError(try store.undo(companyID: "school", title: "Школа", baseRevision: 2, actor: .agent))
        XCTAssertEqual(try load().document.nodes.last?.body, "Дополнение человека")
        var state = try store.undo(companyID: "school", title: "Школа", baseRevision: 2, transactionID: "undo-human")
        XCTAssertEqual(state.document.revision, 3); XCTAssertEqual(state.document.nodes.last?.body, "")
        store = BoardDocumentStore(directory: directory)
        state = try store.redo(companyID: "school", title: "Школа", baseRevision: 3, transactionID: "redo-human")
        XCTAssertEqual(state.document.revision, 4); XCTAssertEqual(state.document.nodes.last?.body, "Дополнение человека")
        let repeated = try store.redo(companyID: "school", title: "Школа", baseRevision: 3, transactionID: "redo-human")
        XCTAssertEqual(repeated.document.revision, 4)
    }
    func testAcceptedContentAndHumanConnectionsAreProtectedFromAgent() throws {
        try apply([.createNode(.init(id: "a", kind: .hypothesis, title: "Проверяемое предположение")), .createNode(.init(id: "b", kind: .note, title: "Основание"))], actor: .agent)
        try apply([.setNodeFields(nodeId: "a", fields: .init(status: .accepted)), .connect(.init(id: "human-link", source: "b", target: "a", kind: .supports))])
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "a", fields: .init(body: "Новый смысл под старым принятием"))], actor: .agent))
        XCTAssertThrowsError(try apply([.removeEdge(edgeId: "human-link")], actor: .agent))
        XCTAssertThrowsError(try apply([.deleteNode(nodeId: "b")], actor: .agent))
        XCTAssertEqual(try load().document.edges.count, 1)
    }
    func testActiveInputLeaseBlocksConflictingAgentButNotIndependentMove() throws {
        try apply([.createNode(.init(id: "n", kind: .note, title: "Агент"))], actor: .agent)
        try store.setEditing(companyID: "school", title: "Школа", sessionID: "renderer", fields: ["n": ["body"]])
        XCTAssertThrowsError(try apply([.setNodeFields(nodeId: "n", fields: .init(body: "Замена"))], actor: .agent))
        try apply([.moveNodes([.init(nodeId: "n", x: 50, y: 50)])], actor: .agent)
        try store.setEditing(companyID: "school", title: "Школа", sessionID: "renderer", fields: [:])
        try apply([.setNodeFields(nodeId: "n", fields: .init(body: "После окончания ввода"))], actor: .agent)
    }
    func testReferencesMustExistAndCannotCrossCompanies() throws {
        XCTAssertThrowsError(try apply([.createNode(.init(id: "s", kind: .source, title: "Источник", referenceId: "material"))]))
        try store.updateReferences(companyID: "school", title: "Школа", sourceIDs: ["material"], contactIDs: ["person"])
        try apply([.createNode(.init(id: "s", kind: .source, title: "Источник", referenceId: "material")), .createNode(.init(id: "c", kind: .contact, title: "Контакт", referenceId: "person"))])
        let other = try store.load(companyID: "other", title: "Другая")
        XCTAssertThrowsError(try store.apply(.init(documentId: other.document.id, baseRevision: 0, actor: .human, operations: [.createNode(.init(id: "s", kind: .source, title: "Источник", referenceId: "material"))]), companyID: "other", title: "Другая"))
    }
    func testCorruptFileIsNotOverwrittenByLoadUpdateOrApply() throws {
        _ = try load()
        let url = store.fileURL(companyID: "school")
        let corrupt = Data("broken { archive".utf8)
        try corrupt.write(to: url)
        XCTAssertThrowsError(try load())
        XCTAssertThrowsError(try store.updateReferences(companyID: "school", title: "Школа", sourceIDs: [], contactIDs: []))
        XCTAssertThrowsError(try store.apply(.init(documentId: "board:school", baseRevision: 0, actor: .human, operations: [.deleteNode(nodeId: "initial-offer")]), companyID: "school", title: "Школа"))
        XCTAssertEqual(try Data(contentsOf: url), corrupt)
    }
    func testTwoStoreInstancesObserveEachOthersTransactions() throws {
        let second = BoardDocumentStore(directory: directory)
        let original = try load()
        try apply([.setNodeFields(nodeId: "initial-offer", fields: .init(body: "Сохранено первым"))])
        XCTAssertThrowsError(try second.apply(.init(documentId: original.document.id, baseRevision: original.document.revision, actor: .human, operations: [.setNodeFields(nodeId: "initial-offer", fields: .init(body: "Старая правка второго"))]), companyID: "school", title: "Школа"))
        XCTAssertEqual(try second.load(companyID: "school", title: "Школа").document.nodes[0].body, "Сохранено первым")
    }
}
