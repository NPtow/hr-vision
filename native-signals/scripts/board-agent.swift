import Foundation
import SignalsCore

/// Local agent entry point: load current revision, then submit a semantic transaction.
/// Usage: board-agent COMPANY_ID [transaction.json]
@main struct BoardAgent {
    static func main() {
        do { try run() } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
    }
    static func run() throws {
        guard CommandLine.arguments.count >= 2 else { fatalError("Usage: board-agent COMPANY_ID [transaction.json]") }
        let companyID = CommandLine.arguments[1]
        let catalog = try CompanyCatalog.loadBundled()
        guard let company = catalog.companies.first(where: { $0.id == companyID && !$0.isDemo }) else { fatalError("Unknown company") }
        let store = BoardDocumentStore()
        let result: BoardDocumentState
        if CommandLine.arguments.count == 3 {
            let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
            let transaction = try JSONDecoder().decode(BoardTransaction.self, from: data)
            guard transaction.actor == .agent else { fatalError("Agent CLI only accepts actor=agent") }
            result = try store.apply(transaction, companyID: companyID, title: company.name)
        } else { result = try store.load(companyID: companyID, title: company.name) }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted,.sortedKeys,.withoutEscapingSlashes]
        print(String(decoding: try encoder.encode(result), as: UTF8.self))
    }
}
