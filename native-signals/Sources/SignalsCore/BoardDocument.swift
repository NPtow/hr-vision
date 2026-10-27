import Foundation
import CryptoKit
import Darwin

public enum BoardNodeKind: String, Codable, CaseIterable, Sendable { case source, observation, hypothesis, offer, contact, note }
public enum BoardNodeStatus: String, Codable, CaseIterable, Sendable { case proposed, accepted, rejected, requiresVerification }
public enum BoardEdgeKind: String, Codable, CaseIterable, Sendable { case supports, contradicts, requiresVerification, contactRationale }
public enum BoardActor: String, Codable, Sendable { case human, agent }

public struct BoardPosition: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}
public struct BoardNode: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var kind: BoardNodeKind
    public var title: String
    public var body: String
    public var status: BoardNodeStatus
    public var referenceId: String?
    public var position: BoardPosition
    public init(id: String = UUID().uuidString, kind: BoardNodeKind, title: String, body: String = "", status: BoardNodeStatus = .proposed, referenceId: String? = nil, position: BoardPosition = .init(x: 0, y: 0)) {
        self.id = id; self.kind = kind; self.title = title; self.body = body; self.status = status; self.referenceId = referenceId; self.position = position
    }
}
public struct BoardEdge: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var source: String
    public var target: String
    public var kind: BoardEdgeKind
    public init(id: String = UUID().uuidString, source: String, target: String, kind: BoardEdgeKind) { self.id = id; self.source = source; self.target = target; self.kind = kind }
}
public struct BoardDocument: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var companyId: String
    public var title: String
    public var revision: Int
    public var nodes: [BoardNode]
    public var edges: [BoardEdge]
    public init(id: String, companyId: String, title: String, revision: Int = 0, nodes: [BoardNode] = [], edges: [BoardEdge] = []) {
        self.id = id; self.companyId = companyId; self.title = title; self.revision = revision; self.nodes = nodes; self.edges = edges
    }
}
public struct BoardNodeFields: Codable, Equatable, Sendable {
    public var title: String?
    public var body: String?
    public var status: BoardNodeStatus?
    public init(title: String? = nil, body: String? = nil, status: BoardNodeStatus? = nil) { self.title = title; self.body = body; self.status = status }
    fileprivate var names: Set<String> { Set([(title != nil ? "title" : nil), (body != nil ? "body" : nil), (status != nil ? "status" : nil)].compactMap { $0 }) }
}
public struct BoardNodeMove: Codable, Equatable, Sendable {
    public var nodeId: String
    public var x: Double
    public var y: Double
    public init(nodeId: String, x: Double, y: Double) { self.nodeId = nodeId; self.x = x; self.y = y }
}
public enum BoardOperation: Codable, Equatable, Sendable {
    case createNode(BoardNode)
    case setNodeFields(nodeId: String, fields: BoardNodeFields)
    case moveNodes([BoardNodeMove])
    case connect(BoardEdge)
    case removeEdge(edgeId: String)
    case deleteNode(nodeId: String)
    private enum Keys: String, CodingKey { case type, node, nodeId, fields, positions, edge, edgeId }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        switch try c.decode(String.self, forKey: .type) {
        case "createNode": self = .createNode(try c.decode(BoardNode.self, forKey: .node))
        case "setNodeFields": self = .setNodeFields(nodeId: try c.decode(String.self, forKey: .nodeId), fields: try c.decode(BoardNodeFields.self, forKey: .fields))
        case "moveNodes": self = .moveNodes(try c.decode([BoardNodeMove].self, forKey: .positions))
        case "connect": self = .connect(try c.decode(BoardEdge.self, forKey: .edge))
        case "removeEdge": self = .removeEdge(edgeId: try c.decode(String.self, forKey: .edgeId))
        case "deleteNode": self = .deleteNode(nodeId: try c.decode(String.self, forKey: .nodeId))
        default: throw BoardError.invalid("Неизвестная операция доски")
        }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        switch self {
        case .createNode(let node): try c.encode("createNode", forKey: .type); try c.encode(node, forKey: .node)
        case .setNodeFields(let id, let fields): try c.encode("setNodeFields", forKey: .type); try c.encode(id, forKey: .nodeId); try c.encode(fields, forKey: .fields)
        case .moveNodes(let positions): try c.encode("moveNodes", forKey: .type); try c.encode(positions, forKey: .positions)
        case .connect(let edge): try c.encode("connect", forKey: .type); try c.encode(edge, forKey: .edge)
        case .removeEdge(let id): try c.encode("removeEdge", forKey: .type); try c.encode(id, forKey: .edgeId)
        case .deleteNode(let id): try c.encode("deleteNode", forKey: .type); try c.encode(id, forKey: .nodeId)
        }
    }
}
public struct BoardTransaction: Codable, Equatable, Sendable {
    public var transactionId: String
    public var documentId: String
    public var baseRevision: Int
    public var actor: BoardActor
    public var taskId: String?
    public var operations: [BoardOperation]
    public init(transactionId: String = UUID().uuidString, documentId: String, baseRevision: Int, actor: BoardActor, taskId: String? = nil, operations: [BoardOperation]) {
        self.transactionId = transactionId; self.documentId = documentId; self.baseRevision = baseRevision; self.actor = actor; self.taskId = taskId; self.operations = operations
    }
}
public struct BoardDocumentState: Codable, Sendable {
    public var document: BoardDocument
    public var canUndo: Bool
    public var canRedo: Bool
    public var ackTransactionId: String?
}
public enum BoardError: Error, LocalizedError {
    case invalid(String), conflict(String), protectedField(String), storage(String)
    public var errorDescription: String? {
        switch self {
        case .invalid(let value): return "Некорректная команда: \(value)."
        case .conflict(let value): return "Доска изменилась: \(value). Обновите состояние и повторите действие."
        case .protectedField(let value): return "Сохранена правка человека: \(value). Агент может добавить отдельное предложение."
        case .storage(let value): return "Не удалось прочитать или сохранить доску. Исходный файл не заменён. \(value)"
        }
    }
}

/// Canonical one-Mac store. A process lock covers read, validation and atomic replacement;
/// every client, including a CLI, must use this API rather than overwrite a snapshot.
public final class BoardDocumentStore {
    public let directory: URL
    private struct History: Codable { var transactionId: String; var actor: BoardActor; var before: BoardDocument; var after: BoardDocument; var beforeHumanFields: [String: Set<String>]; var afterHumanFields: [String: Set<String>]; var beforeHumanEdges: Set<String>; var afterHumanEdges: Set<String> }
    private struct EditLease: Codable { var sessionId: String; var fields: [String: Set<String>]; var expiresAt: Date }
    private struct Stored: Codable {
        var version = 1
        var document: BoardDocument
        var history: [History] = []
        var cursor = 0
        var receipts: [String: String] = [:]
        var humanFields: [String: Set<String>] = [:]
        var fieldRevisions: [String: Int] = [:]
        var humanEdges: Set<String> = []
        var sourceIDs: Set<String> = []
        var contactIDs: Set<String> = []
        var leases: [EditLease] = []
    }
    public init(directory: URL? = nil) {
        let base = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
        self.directory = directory ?? base.appendingPathComponent("boards", isDirectory: true)
    }
    public func fileURL(companyID: String) -> URL { directory.appendingPathComponent(Self.digest(Data(companyID.utf8)) + ".json") }
    public func load(companyID: String, title: String) throws -> BoardDocumentState {
        try locked(companyID: companyID) { let value = try read(companyID: companyID, title: title); if !FileManager.default.fileExists(atPath: fileURL(companyID: companyID).path) { try write(value) }; return state(value) }
    }
    public func updateReferences(companyID: String, title: String, sourceIDs: Set<String>, contactIDs: Set<String>) throws {
        try locked(companyID: companyID) { var value = try read(companyID: companyID, title: title); if value.sourceIDs != sourceIDs || value.contactIDs != contactIDs { value.sourceIDs = sourceIDs; value.contactIDs = contactIDs; try write(value) } }
    }
    /// A renewable short lease prevents an external agent touching the active input/drag.
    public func setEditing(companyID: String, title: String, sessionID: String, fields: [String: Set<String>]) throws {
        try locked(companyID: companyID) {
            var value = try read(companyID: companyID, title: title)
            value.leases.removeAll { $0.sessionId == sessionID || $0.expiresAt < Date() }
            if !fields.isEmpty { value.leases.append(.init(sessionId: sessionID, fields: fields, expiresAt: Date().addingTimeInterval(45))) }
            try write(value)
        }
    }
    public func apply(_ transaction: BoardTransaction, companyID: String, title: String) throws -> BoardDocumentState {
        try locked(companyID: companyID) {
            var value = try read(companyID: companyID, title: title)
            let fingerprint = try Self.fingerprint(transaction)
            if try replay(transaction.transactionId, fingerprint: fingerprint, value: value) { return state(value, ack: transaction.transactionId) }
            guard transaction.documentId == value.document.id else { throw BoardError.invalid("другая компания или документ") }
            try operationRevision(transaction, value)
            guard !transaction.operations.isEmpty, transaction.operations.count <= 500 else { throw BoardError.invalid("требуется от 1 до 500 операций") }
            let before = value.document, beforeHuman = value.humanFields, beforeEdges = value.humanEdges
            for operation in transaction.operations { try reduce(operation, actor: transaction.actor, value: &value) }
            try validate(value.document)
            guard value.document != before else { throw BoardError.invalid("команда не изменяет доску") }
            value.document.revision += 1
            stampChanges(before: before, after: value.document, value: &value)
            value.history = Array(value.history.prefix(value.cursor))
            value.history.append(.init(transactionId: transaction.transactionId, actor: transaction.actor, before: before, after: value.document, beforeHumanFields: beforeHuman, afterHumanFields: value.humanFields, beforeHumanEdges: beforeEdges, afterHumanEdges: value.humanEdges))
            value.cursor = value.history.count
            value.receipts[transaction.transactionId] = fingerprint
            try write(value)
            return state(value, ack: transaction.transactionId)
        }
    }
    public func undo(companyID: String, title: String, baseRevision: Int, transactionID: String = UUID().uuidString, actor: BoardActor = .human) throws -> BoardDocumentState {
        try historyStep(companyID: companyID, title: title, baseRevision: baseRevision, transactionID: transactionID, actor: actor, forward: false)
    }
    public func redo(companyID: String, title: String, baseRevision: Int, transactionID: String = UUID().uuidString, actor: BoardActor = .human) throws -> BoardDocumentState {
        try historyStep(companyID: companyID, title: title, baseRevision: baseRevision, transactionID: transactionID, actor: actor, forward: true)
    }
    private func historyStep(companyID: String, title: String, baseRevision: Int, transactionID: String, actor: BoardActor, forward: Bool) throws -> BoardDocumentState {
        try locked(companyID: companyID) {
            var value = try read(companyID: companyID, title: title)
            let fingerprint = "\(forward ? "redo" : "undo"):\(baseRevision):\(actor.rawValue)"
            if try replay(transactionID, fingerprint: fingerprint, value: value) { return state(value, ack: transactionID) }
            try revision(baseRevision, value)
            guard forward ? value.cursor < value.history.count : value.cursor > 0 else { throw BoardError.invalid("нет шага для \(forward ? "повтора" : "отмены")") }
            let entry = value.history[forward ? value.cursor : value.cursor - 1]
            // Agent undo is deliberately restricted to the latest untouched agent step.
            if actor == .agent && entry.actor != .agent { throw BoardError.protectedField("последний шаг принадлежит человеку") }
            if actor == .agent && value.leases.contains(where: { $0.expiresAt > Date() && !$0.fields.isEmpty }) { throw BoardError.conflict("человек редактирует карточку") }
            let previous = value.document
            let nextRevision = value.document.revision + 1
            value.document = forward ? entry.after : entry.before
            value.document.revision = nextRevision
            stampChanges(before: previous, after: value.document, value: &value)
            value.humanFields = forward ? entry.afterHumanFields : entry.beforeHumanFields
            value.humanEdges = forward ? entry.afterHumanEdges : entry.beforeHumanEdges
            value.cursor += forward ? 1 : -1
            value.receipts[transactionID] = fingerprint
            try write(value)
            return state(value, ack: transactionID)
        }
    }
    private func reduce(_ operation: BoardOperation, actor: BoardActor, value: inout Stored) throws {
        func index(_ id: String) throws -> Int { guard let index = value.document.nodes.firstIndex(where: { $0.id == id }) else { throw BoardError.conflict("карточка \(id) отсутствует") }; return index }
        func protect(_ id: String, _ fields: Set<String>) throws {
            guard actor == .agent else { return }
            if !fields.intersection(value.humanFields[id] ?? []).isEmpty { throw BoardError.protectedField("карточка \(id), \(fields.sorted().joined(separator: ", "))") }
            if value.leases.contains(where: { $0.expiresAt > Date() && !fields.intersection($0.fields[id] ?? []).isEmpty }) { throw BoardError.conflict("человек сейчас редактирует карточку \(id)") }
        }
        switch operation {
        case .createNode(let node):
            guard !value.document.nodes.contains(where: { $0.id == node.id }) else { throw BoardError.invalid("идентификатор карточки уже существует") }
            if actor == .agent && (node.status == .accepted || node.status == .rejected) { throw BoardError.invalid("принятие и отклонение доступны человеку") }
            if node.kind == .source { guard let ref = node.referenceId, value.sourceIDs.contains(ref) else { throw BoardError.invalid("источник отсутствует в архиве компании") } }
            if node.kind == .contact { guard let ref = node.referenceId, value.contactIDs.contains(ref) else { throw BoardError.invalid("контакт не связан с компанией или отсутствует") } }
            if node.kind != .source && node.kind != .contact && node.referenceId != nil { throw BoardError.invalid("ссылка допустима только для источника или контакта") }
            value.document.nodes.append(node)
            if actor == .human {
                value.humanFields[node.id] = Set([!node.title.isEmpty ? "title" : nil, !node.body.isEmpty ? "body" : nil, "position", "status"].compactMap { $0 })
                if node.status == .accepted || node.status == .rejected { value.humanFields[node.id, default: []].formUnion(["title", "body"]) }
            }
        case .setNodeFields(let id, let fields):
            let i = try index(id); try protect(id, fields.names)
            guard !fields.names.isEmpty else { throw BoardError.invalid("пустой набор полей") }
            if actor == .agent, let status = fields.status, status == .accepted || status == .rejected { throw BoardError.invalid("принятие и отклонение доступны человеку") }
            if let title = fields.title { value.document.nodes[i].title = title }
            if let body = fields.body { value.document.nodes[i].body = body }
            if let status = fields.status { value.document.nodes[i].status = status }
            if actor == .human {
                value.humanFields[id, default: []].formUnion(fields.names)
                if fields.status == .accepted || fields.status == .rejected { value.humanFields[id, default: []].formUnion(["title", "body"]) }
            }
        case .moveNodes(let positions):
            guard !positions.isEmpty, Set(positions.map(\.nodeId)).count == positions.count else { throw BoardError.invalid("пустые или повторяющиеся перемещения") }
            for position in positions { let i = try index(position.nodeId); try protect(position.nodeId, ["position"]); value.document.nodes[i].position = .init(x: position.x, y: position.y); if actor == .human { value.humanFields[position.nodeId, default: []].insert("position") } }
        case .connect(let edge):
            guard !value.document.edges.contains(where: { $0.id == edge.id }) else { throw BoardError.invalid("идентификатор связи уже существует") }
            _ = try index(edge.source); _ = try index(edge.target)
            value.document.edges.append(edge)
            if actor == .human { value.humanEdges.insert(edge.id) }
        case .removeEdge(let id):
            guard let i = value.document.edges.firstIndex(where: { $0.id == id }) else { throw BoardError.conflict("связь отсутствует") }
            if actor == .agent && value.humanEdges.contains(id) { throw BoardError.protectedField("связь \(id)") }
            value.document.edges.remove(at: i); value.humanEdges.remove(id)
        case .deleteNode(let id):
            let i = try index(id); try protect(id, ["title", "body", "position", "status"])
            let linkedEdges = Set(value.document.edges.filter { $0.source == id || $0.target == id }.map(\.id))
            if actor == .agent && !linkedEdges.intersection(value.humanEdges).isEmpty { throw BoardError.protectedField("связи карточки \(id)") }
            value.humanEdges.subtract(linkedEdges)
            value.document.nodes.remove(at: i); value.document.edges.removeAll { $0.source == id || $0.target == id }; value.humanFields.removeValue(forKey: id)
        }
    }
    private func validate(_ document: BoardDocument) throws {
        guard !document.id.isEmpty, !document.companyId.isEmpty, document.revision >= 0, document.nodes.count <= 10_000, document.edges.count <= 30_000,
              Set(document.nodes.map(\.id)).count == document.nodes.count, Set(document.edges.map(\.id)).count == document.edges.count else { throw BoardError.invalid("структура документа") }
        let ids = Set(document.nodes.map(\.id))
        for node in document.nodes {
            guard !node.id.isEmpty, node.id.count <= 250, node.title.count <= 4_000, node.body.count <= 200_000, node.position.x.isFinite, node.position.y.isFinite, abs(node.position.x) <= 1_000_000, abs(node.position.y) <= 1_000_000 else { throw BoardError.invalid("поля карточки \(node.id)") }
            if node.kind == .source || node.kind == .contact { guard node.referenceId?.isEmpty == false else { throw BoardError.invalid("карточка без ссылки на оригинал") } }
        }
        for edge in document.edges { guard !edge.id.isEmpty, edge.id.count <= 250, edge.source != edge.target, ids.contains(edge.source), ids.contains(edge.target) else { throw BoardError.invalid("связь с отсутствующей карточкой или самой собой") } }
    }
    private func operationRevision(_ transaction: BoardTransaction, _ value: Stored) throws {
        guard transaction.baseRevision >= 0, transaction.baseRevision <= value.document.revision else { throw BoardError.conflict("неизвестная базовая версия") }
        if transaction.baseRevision == value.document.revision { return }
        var keys = Set<String>()
        func node(_ id: String, _ fields: Set<String>) { keys.insert("node:\(id):exists"); for field in fields { keys.insert("node:\(id):\(field)") } }
        for operation in transaction.operations {
            switch operation {
            case .createNode(let item): node(item.id, ["any"])
            case .setNodeFields(let id, let fields): node(id, fields.names)
            case .moveNodes(let positions): for p in positions { node(p.nodeId, ["position"]) }
            case .connect(let edge): node(edge.source, []); node(edge.target, []); keys.insert("edge:" + edge.id)
            case .removeEdge(let id): keys.insert("edge:" + id)
            case .deleteNode(let id): node(id, ["any", "edges"])
            }
        }
        guard keys.allSatisfy({ (value.fieldRevisions[$0] ?? 0) <= transaction.baseRevision }) else { throw BoardError.conflict("изменены затронутые поля, сохранена версия \(value.document.revision)") }
    }
    private func stampChanges(before: BoardDocument, after: BoardDocument, value: inout Stored) {
        let old = Dictionary(uniqueKeysWithValues: before.nodes.map { ($0.id, $0) }), new = Dictionary(uniqueKeysWithValues: after.nodes.map { ($0.id, $0) })
        let revision = after.revision
        for id in Set(old.keys).union(new.keys) {
            let a = old[id], b = new[id]
            if a != b { value.fieldRevisions["node:\(id):any"] = revision }
            if a == nil || b == nil { value.fieldRevisions["node:\(id):exists"] = revision }
            if a?.title != b?.title { value.fieldRevisions["node:\(id):title"] = revision }
            if a?.body != b?.body { value.fieldRevisions["node:\(id):body"] = revision }
            if a?.status != b?.status { value.fieldRevisions["node:\(id):status"] = revision }
            if a?.position != b?.position { value.fieldRevisions["node:\(id):position"] = revision }
        }
        let oldEdges = Dictionary(uniqueKeysWithValues: before.edges.map { ($0.id, $0) }), newEdges = Dictionary(uniqueKeysWithValues: after.edges.map { ($0.id, $0) })
        for id in Set(oldEdges.keys).union(newEdges.keys) where oldEdges[id] != newEdges[id] {
            value.fieldRevisions["edge:" + id] = revision
            for edge in [oldEdges[id], newEdges[id]].compactMap({ $0 }) { value.fieldRevisions["node:\(edge.source):edges"] = revision; value.fieldRevisions["node:\(edge.target):edges"] = revision }
        }
    }
    private func revision(_ expected: Int, _ value: Stored) throws { guard expected == value.document.revision else { throw BoardError.conflict("ожидалась версия \(expected), сохранена \(value.document.revision)") } }
    private func replay(_ id: String, fingerprint: String, value: Stored) throws -> Bool {
        guard !id.isEmpty, id.count <= 250 else { throw BoardError.invalid("transactionId") }
        guard let existing = value.receipts[id] else { return false }
        guard existing == fingerprint else { throw BoardError.invalid("transactionId уже использован для другой команды") }
        return true
    }
    private func state(_ value: Stored, ack: String? = nil) -> BoardDocumentState { .init(document: value.document, canUndo: value.cursor > 0, canRedo: value.cursor < value.history.count, ackTransactionId: ack) }
    private func read(companyID: String, title: String) throws -> Stored {
        let url = fileURL(companyID: companyID)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return Stored(document: .init(id: "board:" + companyID, companyId: companyID, title: title, nodes: [
                .init(id: "initial-offer", kind: .offer, title: "Оффер", position: .init(x: 80, y: 100)),
                .init(id: "initial-recipient", kind: .note, title: "Получатель", position: .init(x: 470, y: 100))]))
        }
        do {
            let value = try JSONDecoder().decode(Stored.self, from: Data(contentsOf: url))
            guard value.version == 1, value.document.companyId == companyID, value.document.id == "board:" + companyID, value.cursor >= 0, value.cursor <= value.history.count else { throw BoardError.invalid("версия или идентификатор сохранённого документа") }
            try validate(value.document)
            for entry in value.history { try validate(entry.before); try validate(entry.after); guard entry.before.companyId == companyID, entry.after.companyId == companyID else { throw BoardError.invalid("история другого документа") } }
            return value
        } catch { throw BoardError.storage(error.localizedDescription) }
    }
    private func write(_ value: Stored) throws {
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            let data = try encoder.encode(value)
            try data.write(to: fileURL(companyID: value.document.companyId), options: .atomic)
            // ACK follows durable file contents. The lock remains held until this completes.
            let handle = try FileHandle(forWritingTo: fileURL(companyID: value.document.companyId)); defer { try? handle.close() }; try handle.synchronize()
        } catch { throw BoardError.storage(error.localizedDescription) }
    }
    private func locked<T>(companyID: String, _ body: () throws -> T) throws -> T {
        guard !companyID.isEmpty, companyID.count <= 500 else { throw BoardError.invalid("companyID") }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let lockURL = fileURL(companyID: companyID).appendingPathExtension("lock")
        let fd = Darwin.open(lockURL.path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw BoardError.storage("Не открыт файл блокировки") }
        defer { Darwin.close(fd) }
        guard flock(fd, LOCK_EX) == 0 else { throw BoardError.storage("Не получена блокировка") }
        defer { flock(fd, LOCK_UN) }
        return try body()
    }
    private static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private static func fingerprint<T: Encodable>(_ value: T) throws -> String { let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys; return digest(try encoder.encode(value)) }
}
