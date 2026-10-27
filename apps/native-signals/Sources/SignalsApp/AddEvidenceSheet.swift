import SwiftUI
import SignalsCore

struct AddEvidenceSheet: View {
    @EnvironmentObject private var workspace: SignalsWorkspace
    @Environment(\.dismiss) private var dismiss
    let company: CompanyDossier
    @State private var title = ""
    @State private var address = ""
    @State private var observation = ""

    private var validURL: Bool {
        if address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return true }
        guard let url = URL(string: address.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return ["http", "https"].contains(url.scheme?.lowercased() ?? "") && url.host != nil
    }
    private var ready: Bool {
        !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !observation.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && validURL
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Добавить источник").font(.title2.weight(.semibold))
            Text(company.name).font(.callout).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 7) {
                Text("Название").font(.caption.weight(.medium))
                TextField("Например, интервью основателя", text: $title).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("Ссылка, если есть").font(.caption.weight(.medium))
                TextField("https://…", text: $address).textFieldStyle(.roundedBorder)
                if !validURL { Text("Нужна ссылка http:// или https:// с адресом сайта.").font(.caption).foregroundStyle(.orange) }
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("Что удалось узнать").font(.caption.weight(.medium))
                TextEditor(text: $observation).font(.body).padding(5).frame(height: 130)
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            }
            Text("Ссылка и текст появятся в материалах этой компании.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = workspace.persistenceError { Text(error).font(.caption).foregroundStyle(.orange) }
            HStack {
                Spacer()
                Button("Отмена") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Сохранить источник") {
                    let url = address.trimmingCharacters(in: .whitespacesAndNewlines)
                    let evidence = Evidence(id: UUID().uuidString,
                        title: title.trimmingCharacters(in: .whitespacesAndNewlines),
                        excerpt: observation.trimmingCharacters(in: .whitespacesAndNewlines),
                        url: url.isEmpty ? nil : url,
                        dateLabel: "Добавлено \(Date.now.formatted(date: .numeric, time: .omitted))",
                        kind: "Материал пользователя", isDemo: company.isDemo)
                    workspace.update(company) { $0.addedEvidence.append(evidence) }
                    if workspace.persistenceError == nil { dismiss() }
                }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!ready || workspace.editingDisabled)
            }
        }.padding(26).frame(width: 490)
    }
}
