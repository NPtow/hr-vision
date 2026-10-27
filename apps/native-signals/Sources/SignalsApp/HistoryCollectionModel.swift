import Foundation
import SwiftUI
import SignalsCore

enum HistoryTool: String, CaseIterable, Identifiable {
    case vk = "История VK", hiring = "История найма"
    var id: String { rawValue }
    var script: String { self == .vk ? "school-vk-history.py" : "school-hiring-history.py" }
    var folder: String { self == .vk ? "vk" : "hiring" }
    var detail: String { self == .vk ? "Публикации подтверждённых страниц VK, доступные без входа. Закрытые страницы и ограничения источника будут отмечены." : "Доступные объявления Telegram, карьерных страниц и HH. Полный архив чужого работодателя не гарантируется." }
}

@MainActor
final class HistoryCollectionModel: ObservableObject {
    @Published var running = false
    @Published var activeTool: HistoryTool?
    @Published var status = ""
    @Published var error: String?
    @Published var imported = 0
    @Published var log: [String] = []
    private var process: Process?
    private var pipe: Pipe?
    private var buffer = ""
    private var completion: (() -> Void)?

    var dataDirectory: URL {
        if let raw = ProcessInfo.processInfo.environment["TG_SIGNALS_DATA_DIR"] { return URL(fileURLWithPath: raw, isDirectory: true) }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TG Signals", isDirectory: true)
    }

    func run(tool: HistoryTool, companyID: String?, start: Date, onComplete: @escaping () -> Void) {
        guard !running else { return }
        let resources = Bundle.main.resourceURL?.appendingPathComponent("HistoryCollectors")
        guard let script = resources?.appendingPathComponent(tool.script), FileManager.default.fileExists(atPath: script.path) else {
            error = "Файлы сборщика отсутствуют в установленном приложении. Требуется обновить установку."; return
        }
        let seeds = dataDirectory.appendingPathComponent("history-input", isDirectory: true)
        let schoolList = seeds.appendingPathComponent("schools.json")
        guard FileManager.default.fileExists(atPath: schoolList.path) else { error = "Список школ для сбора не найден. Сохранённые результаты доступны."; return }
        let out = dataDirectory.appendingPathComponent("history-runs/\(tool.folder)/\(UUID().uuidString)", isDirectory: true)
        do { try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true) }
        catch { self.error = "Не удалось создать папку сбора: " + error.localizedDescription; return }
        let p = Process(), stream = Pipe()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script.path, "--output-dir", out.path, "--school-list", schoolList.path,
                       "--research-dir", seeds.path, "--window-start", ISO8601DateFormatter().string(from: start), "--refresh"]
        if let companyID { p.arguments! += ["--company-id", companyID] }
        let identity = seeds.appendingPathComponent(tool == .vk ? "vk-identity.json" : "identity-discovery.json")
        if FileManager.default.fileExists(atPath: identity.path) { p.arguments! += ["--identity-file", identity.path] }
        if tool == .vk { p.arguments! += ["--discussions"] }
        if tool == .hiring {
            p.arguments! += ["--app-data-dir", dataDirectory.path]
            let review = seeds.appendingPathComponent("hiring-review.json")
            if FileManager.default.fileExists(atPath: review.path) { p.arguments! += ["--review-file", review.path] }
        }
        p.currentDirectoryURL = out
        p.environment = ProcessInfo.processInfo.environment.merging(["PYTHONUNBUFFERED": "1"]) { _, value in value }
        p.standardOutput = stream; p.standardError = stream
        buffer = ""; log = []; error = nil; imported = 0; activeTool = tool; completion = onComplete
        status = "Собираю доступные публикации…"; running = true; process = p; pipe = stream
        stream.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let value = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor [weak self] in self?.append(value) }
        }
        p.terminationHandler = { [weak self] process in
            Task { @MainActor [weak self] in self?.finish(code: process.terminationStatus, output: out, companyID: companyID) }
        }
        do { try p.run() }
        catch { running = false; self.error = "Не удалось запустить сбор: " + error.localizedDescription; self.process = nil; pipe = nil }
    }

    private func append(_ value: String) {
        buffer += value
        let lines = buffer.components(separatedBy: "\n")
        buffer = lines.last ?? ""
        for line in lines.dropLast() where !line.isEmpty {
            var readable = line
            if let data = line.data(using: .utf8), let item = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let message = item["message"] as? String {
                readable = ((item["companyName"] as? String).map { $0 + ": " } ?? "") + message
            }
            log.append(String(readable.prefix(550)))
            if log.count > 60 { log.removeFirst(log.count - 60) }
            status = String(readable.prefix(180))
        }
    }

    private func finish(code: Int32, output: URL, companyID: String?) {
        pipe?.fileHandleForReading.readabilityHandler = nil
        if !buffer.isEmpty { append("\n") }
        let result = SourceHistoryStore(directory: output.appendingPathComponent("archives")).load()
        let target = dataDirectory.appendingPathComponent("source-history", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            for var archive in result.archives where companyID == nil || archive.companyID == companyID {
                archive.id += "-" + output.lastPathComponent
                guard archive.id.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { continue }
                try encoder.encode(archive).write(to: target.appendingPathComponent(archive.id + ".json"), options: .atomic)
                imported += archive.documents.count
            }
            if !result.errors.isEmpty { error = "Часть файлов не прочитана: " + result.errors.joined(separator: " · ") }
            if code != 0 { error = "Сбор завершился с ограничением (код \(code)). Доступные проверенные результаты сохранены. Подробности — ниже." }
            status = "Сохранено материалов: \(imported). Проверь покрытие источников в разделе найма."
        } catch { self.error = "Не удалось сохранить результаты: " + error.localizedDescription }
        running = false; process = nil; pipe = nil
        completion?(); completion = nil
    }
}

struct HistoryCollectionSheet: View {
    @ObservedObject var model: HistoryCollectionModel
    let company: CompanyDossier
    let start: Date
    let onComplete: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State var tool: HistoryTool = .vk
    @State private var allSchools = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("Сбор источников").font(.title2.weight(.semibold)); Spacer(); Button("Закрыть") { dismiss() } }
            Picker("Инструмент", selection: $tool) { ForEach(HistoryTool.allCases) { Text($0.rawValue).tag($0) } }
                .pickerStyle(.segmented).disabled(model.running)
            Text(tool.detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text(allSchools ? "Выборка: 64 школы" : "Компания: " + company.name).font(.headline)
            Toggle("Вся выборка школьного образования", isOn: $allSchools).disabled(model.running)
            Text("Период: " + start.formatted(date: .numeric, time: .omitted) + " — сегодня").font(.callout)
            HStack {
                Button(model.running ? "Сбор выполняется" : "Собрать доступные публикации") {
                    model.run(tool: tool, companyID: allSchools ? nil : company.id, start: start, onComplete: onComplete)
                }.buttonStyle(.borderedProminent).disabled(model.running).accessibilityIdentifier("history-run")
                if model.running { ProgressView().controlSize(.small) }
            }
            if !model.status.isEmpty { Text(model.status).font(.callout).textSelection(.enabled) }
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            if !model.log.isEmpty {
                ScrollView { Text(model.log.joined(separator: "\n")).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 180)
            }
            Text("Результаты появятся в материалах и истории найма. Ограничения доступа сохраняются отдельно. TargetHunter не используется.").font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width: 650)
    }
}
