import SwiftUI
import WebKit
import SignalsCore

/// The web view is a renderer only. Document writes, history and reference validation live in Swift.
struct BoardWorkspaceView: View {
    let company: CompanyDossier
    let materials: [ResearchMaterial]
    let contacts: [ResearchContact]
    let onOpenMaterial: (String) -> Void
    let onOpenContact: (String) -> Void
    @StateObject private var model: BoardWorkspaceModel
    @Environment(\.scenePhase) private var scenePhase
    private let refresh = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    init(company: CompanyDossier, materials: [ResearchMaterial], contacts: [ResearchContact], onOpenMaterial: @escaping (String) -> Void, onOpenContact: @escaping (String) -> Void) {
        self.company = company; self.materials = materials; self.contacts = contacts
        self.onOpenMaterial = onOpenMaterial; self.onOpenContact = onOpenContact
        _model = StateObject(wrappedValue: BoardWorkspaceModel(company: company, materials: materials, contacts: contacts))
    }
    private var materialSignature: [[String]] {
        materials.map { [$0.id, $0.title, $0.text, $0.url ?? "", $0.dateLabel, $0.providers.joined(separator: "|"), $0.quality] }
    }
    private var contactSignature: [[String]] {
        contacts.map { [$0.id, $0.name, $0.role, $0.address, $0.explanation, $0.sourceQuote, $0.affiliation.rawValue, $0.kind.rawValue, $0.sourceURLs.joined(separator: "|")] }
    }
    var body: some View {
        VStack(spacing: 0) {
            if let error = model.error {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle").foregroundStyle(.orange)
                    Text(error).font(.callout).textSelection(.enabled)
                    Spacer()
                    Button("Перечитать") { model.reload(force: true) }
                }.padding(12).background(Color.orange.opacity(0.08)).accessibilityIdentifier("board-error")
            }
            BoardWebRenderer(model: model, onOpenMaterial: onOpenMaterial, onOpenContact: onOpenContact)
                .accessibilityIdentifier("company-analysis-board")
        }
        .onChange(of: company.id) { _, _ in model.configure(company: company, materials: materials, contacts: contacts) }
        .onChange(of: materialSignature) { _, _ in model.configure(company: company, materials: materials, contacts: contacts) }
        .onChange(of: contactSignature) { _, _ in model.configure(company: company, materials: materials, contacts: contacts) }
        .onChange(of: scenePhase) { _, phase in if phase == .active { model.reload(force: true) } }
        .onReceive(refresh) { _ in model.reload() }
        .onDisappear { model.clearEditing() }
    }
}

private struct BoardReference: Codable, Equatable {
    var id: String
    var title: String
    var body: String?
    var subtitle: String?
    var url: String?
}

@MainActor
private final class BoardWorkspaceModel: ObservableObject {
    @Published var state: BoardDocumentState?
    @Published var error: String?
    var sources: [BoardReference] = []
    var contacts: [BoardReference] = []
    var companyID: String
    var title: String
    let store = BoardDocumentStore()
    var sessionID: String?
    var editing: [String: Set<String>] = [:]
    private var renewedAt = Date.distantPast
    var change: (() -> Void)?

    init(company: CompanyDossier, materials: [ResearchMaterial], contacts: [ResearchContact]) {
        companyID = company.id; title = company.name
        configure(company: company, materials: materials, contacts: contacts)
    }
    func configure(company: CompanyDossier, materials: [ResearchMaterial], contacts rawContacts: [ResearchContact]) {
        if companyID != company.id { clearEditing(); state = nil }
        companyID = company.id; title = company.name
        sources = materials.map { .init(id: $0.id, title: $0.title, body: $0.text, subtitle: [$0.dateLabel, $0.providers.joined(separator: ", "), $0.quality].filter { !$0.isEmpty }.joined(separator: " · "), url: $0.url) }
        contacts = rawContacts.filter { $0.affiliation == .published || ($0.kind == .person && $0.affiliation == .profileCompany) }.map {
            .init(id: $0.id, title: $0.name, body: [$0.address, $0.explanation, $0.sourceQuote].filter { !$0.isEmpty }.joined(separator: "\n"), subtitle: [$0.role, $0.affiliation.rawValue].filter { !$0.isEmpty }.joined(separator: " · "), url: $0.sourceURLs.first)
        }
        do {
            try store.updateReferences(companyID: companyID, title: title, sourceIDs: Set(sources.map(\.id)), contactIDs: Set(contacts.map(\.id)))
            reload(force: true)
        } catch { self.error = error.localizedDescription }
    }
    func reload(force: Bool = false) {
        do {
            if !editing.isEmpty, let sessionID, Date().timeIntervalSince(renewedAt) > 10 {
                try store.setEditing(companyID: companyID, title: title, sessionID: sessionID, fields: editing); renewedAt = Date()
            }
            let current = try store.load(companyID: companyID, title: title)
            if state?.document != current.document || state?.canUndo != current.canUndo || state?.canRedo != current.canRedo || force {
                state = current
                if editing.isEmpty { change?() }
            }
            if force { error = nil }
        } catch { self.error = error.localizedDescription }
    }
    func clearEditing() {
        if let sessionID, !editing.isEmpty { try? store.setEditing(companyID: companyID, title: title, sessionID: sessionID, fields: [:]) }
        editing = [:]
    }
    func setEditing(nodeIDs: [String], fields: Set<String>, active: Bool) throws {
        guard let sessionID else { return }
        let validIDs = Set(state?.document.nodes.map(\.id) ?? [])
        guard Set(nodeIDs).isSubset(of: validIDs), fields.isSubset(of: ["title", "body", "status", "position"]) else { throw BoardError.invalid("поля редактирования") }
        for id in nodeIDs { if active { editing[id, default: []].formUnion(fields) } else { editing[id]?.subtract(fields); if editing[id]?.isEmpty == true { editing.removeValue(forKey: id) } } }
        try store.setEditing(companyID: companyID, title: title, sessionID: sessionID, fields: editing)
        renewedAt = Date()
        if editing.isEmpty { change?() }
    }
    func apply(_ transaction: BoardTransaction) throws -> BoardDocumentState {
        guard transaction.actor == .human else { throw BoardError.invalid("web-команда должна принадлежать человеку") }
        let result = try store.apply(transaction, companyID: companyID, title: title)
        state = result; error = nil; return result
    }
    func history(forward: Bool, baseRevision: Int, transactionID: String) throws -> BoardDocumentState {
        let result = try forward ? store.redo(companyID: companyID, title: title, baseRevision: baseRevision, transactionID: transactionID) : store.undo(companyID: companyID, title: title, baseRevision: baseRevision, transactionID: transactionID)
        state = result; error = nil; return result
    }
}

private struct BoardWebRenderer: NSViewRepresentable {
    @ObservedObject var model: BoardWorkspaceModel
    let onOpenMaterial: (String) -> Void
    let onOpenContact: (String) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(model: model, onOpenMaterial: onOpenMaterial, onOpenContact: onOpenContact) }
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.userContentController.add(context.coordinator, name: "board")
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.setValue(false, forKey: "drawsBackground")
        context.coordinator.webView = view
        model.change = { [weak coordinator = context.coordinator] in coordinator?.sendState() }
        do {
            let url = try Self.assetURL()
            context.coordinator.assetURL = url
            view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        } catch { model.error = error.localizedDescription }
        return view
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {
        context.coordinator.onOpenMaterial = onOpenMaterial
        context.coordinator.onOpenContact = onOpenContact
    }
    static func dismantleNSView(_ nsView: WKWebView, coordinator: Coordinator) {
        coordinator.model.clearEditing(); coordinator.model.change = nil
        nsView.configuration.userContentController.removeScriptMessageHandler(forName: "board")
        nsView.navigationDelegate = nil
    }
    private static func assetURL() throws -> URL {
        let bundle: Bundle
        if Bundle.main.bundleURL.pathExtension == "app" {
            guard let resources = Bundle.main.resourceURL, let installed = Bundle(url: resources.appendingPathComponent("TGSignals_SignalsApp.bundle")) else { throw BoardError.storage("Не найден пакет встроенного холста") }
            bundle = installed
        } else { bundle = Bundle.module }
        guard let url = bundle.url(forResource: "index", withExtension: "html", subdirectory: "Board") else { throw BoardError.storage("Не найден Board/index.html") }
        return url
    }

    @MainActor
    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        let model: BoardWorkspaceModel
        weak var webView: WKWebView?
        var assetURL: URL?
        var onOpenMaterial: (String) -> Void
        var onOpenContact: (String) -> Void
        private var currentSession: String?
        private var recovering = false
        init(model: BoardWorkspaceModel, onOpenMaterial: @escaping (String) -> Void, onOpenContact: @escaping (String) -> Void) { self.model = model; self.onOpenMaterial = onOpenMaterial; self.onOpenContact = onOpenContact }
        private func trusted(_ url: URL?) -> Bool { guard let url, let assetURL else { return false }; return url.isFileURL && url.standardizedFileURL.path == assetURL.standardizedFileURL.path }
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "board", message.frameInfo.isMainFrame, trusted(message.frameInfo.request.url), trusted(message.webView?.url),
                  let payload = message.body as? [String: Any], payload["protocolVersion"] as? Int == 1,
                  let session = payload["sessionId"] as? String, !session.isEmpty, session.count < 200,
                  let type = payload["type"] as? String else { return }
            if type == "ready" {
                model.clearEditing(); currentSession = session; model.sessionID = session; recovering = false
                model.reload(force: true); sendState(); return
            }
            guard session == currentSession else { return }
            if let documentID = payload["documentId"] as? String, documentID != model.state?.document.id { return }
            do {
                switch type {
                case "transaction":
                    let data = try JSONSerialization.data(withJSONObject: payload)
                    guard data.count <= 2_000_000 else { throw BoardError.invalid("слишком большая команда") }
                    let transaction = try JSONDecoder().decode(BoardTransaction.self, from: data)
                    sendState(try model.apply(transaction))
                case "undo", "redo":
                    guard let revision = payload["baseRevision"] as? Int, let transactionID = payload["transactionId"] as? String else { throw BoardError.invalid("история без версии или transactionId") }
                    sendState(try model.history(forward: type == "redo", baseRevision: revision, transactionID: transactionID))
                case "editingChanged":
                    guard let ids = payload["nodeIds"] as? [String], let fields = payload["fields"] as? [String], let active = payload["editing"] as? Bool else { throw BoardError.invalid("сообщение о редактировании") }
                    try model.setEditing(nodeIDs: ids, fields: Set(fields), active: active)
                case "selectionChanged":
                    // Selection is renderer-local; it never writes a document revision.
                    break
                case "openReference":
                    guard let id = payload["referenceId"] as? String, let kind = payload["kind"] as? String else { throw BoardError.invalid("нет ссылки на оригинал") }
                    if kind == "source", model.sources.contains(where: { $0.id == id }) { onOpenMaterial(id) }
                    else if kind == "contact", model.contacts.contains(where: { $0.id == id }) { onOpenContact(id) }
                    else { throw BoardError.invalid("оригинал отсутствует в компании") }
                default: throw BoardError.invalid("неподдерживаемое сообщение")
                }
            } catch {
                model.error = error.localizedDescription
                var failure: [String: Any] = ["type": "error", "message": error.localizedDescription]
                if let id = payload["transactionId"] as? String { failure["transactionId"] = id }
                send(failure)
                model.reload(); sendState()
            }
        }
        func sendState(_ explicit: BoardDocumentState? = nil) {
            guard let value = explicit ?? model.state, currentSession != nil else { return }
            do {
                var payload = try JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as! [String: Any]
                payload["type"] = "state"
                payload["sources"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(model.sources))
                payload["contacts"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(model.contacts))
                send(payload)
            } catch { model.error = error.localizedDescription }
        }
        private func send(_ body: [String: Any]) {
            guard let webView, let session = currentSession, trusted(webView.url) else { return }
            var payload = body; payload["protocolVersion"] = 1; payload["sessionId"] = session
            webView.callAsyncJavaScript("window.boardHost.receive(payload)", arguments: ["payload": payload], in: nil, in: .page, completionHandler: { [weak self] result in
                guard let self, self.currentSession == session else { return }
                if case .failure(let error) = result { self.model.error = "Холст не получил состояние: \(error.localizedDescription)" }
            })
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if trusted(navigationAction.request.url), navigationAction.targetFrame?.isMainFrame != false { decisionHandler(.allow); return }
            if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url, ["https", "http"].contains(url.scheme?.lowercased() ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { model.error = "Холст не загрузился: \(error.localizedDescription)" }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { model.error = "Холст не загрузился: \(error.localizedDescription)" }
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            model.clearEditing(); currentSession = nil; model.sessionID = nil
            guard !recovering, let assetURL else { model.error = "Процесс холста завершился. Откройте раздел «Разбор» ещё раз."; return }
            recovering = true
            webView.loadFileURL(assetURL, allowingReadAccessTo: assetURL.deletingLastPathComponent())
        }
    }
}
