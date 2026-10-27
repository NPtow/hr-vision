import SwiftUI
import AppKit
import UniformTypeIdentifiers
import SignalsCore

struct TelemetrCollectionView: View {
    @EnvironmentObject private var workspace: SignalsWorkspace
    @ObservedObject var model: TelemetrCollectionModel
    @StateObject private var rpa = TelemetrRPAModel()
    @Environment(\.dismiss) private var dismiss
    @State private var page = 0
    @State private var selectedRunID: String?
    @State private var selectedPostKey: String?
    @State private var companyFilter = "all"
    @State private var search = ""
    @State private var importing = false
    @State private var pastingJSON = false
    @State private var pastedJSON = ""

    private var currentRun: TelemetrCollectionRun? {
        model.runs.first(where: { $0.id == selectedRunID }) ?? model.runs.first
    }
    private var visiblePosts: [TelemetrPost] {
        (currentRun?.posts ?? []).filter {
            (companyFilter == "all" || $0.companyID == companyFilter) &&
            (search.isEmpty || $0.text.localizedCaseInsensitiveContains(search) || ($0.channelTitle ?? "").localizedCaseInsensitiveContains(search))
        }
    }
    private var currentPost: TelemetrPost? {
        visiblePosts.first(where: { postKey($0) == selectedPostKey }) ?? visiblePosts.first
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if page == 0 { results }
            else { TelemetrRPAView(model: rpa, collection: model) }
            if let message = model.errorMessage {
                Divider()
                Label(message, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            }
            if model.busy {
                Divider()
                HStack(spacing: 10) { ProgressView().controlSize(.small); Text(model.progress).font(.callout); Spacer() }
                    .padding(14)
            }
        }
        .frame(minWidth: 960, idealWidth: 1080, minHeight: 650, idealHeight: 750)
        .background(Color(nsColor: .windowBackgroundColor))
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json], allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls): if let url = urls.first { model.importFile(url); selectedRunID = model.runs.first?.id }
            case .failure(let error): model.errorMessage = "Не удалось открыть файл: \(error.localizedDescription)"
            }
        }
        .fileDialogConfirmationLabel("Импортировать результаты")
        .sheet(isPresented: $pastingJSON) { jsonPasteSheet }
        .onChange(of: selectedRunID) { _, _ in selectedPostKey = nil; companyFilter = "all" }
        .onChange(of: companyFilter) { _, _ in selectedPostKey = nil }
        .task {
            while !Task.isCancelled {
                await rpa.refresh(collection: model)
                do { try await Task.sleep(for: .seconds(3)) } catch { break }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Label("История Telegram", systemImage: "text.magnifyingglass").font(.title2.weight(.semibold))
                Text("Telemetr · публикации и источники").font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Picker("Раздел", selection: $page) {
                Text("Результаты").tag(0)
                Text("Статус сбора").tag(2)
            }.pickerStyle(.segmented).frame(width: 380)
            Button("Готово") { dismiss() }.keyboardShortcut(.cancelAction)
        }.padding(22)
    }

    private var results: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                if !model.runs.isEmpty {
                    Picker("Сбор", selection: Binding(get: { currentRun?.id ?? "" }, set: { selectedRunID = $0 })) {
                        ForEach(model.runs) { run in
                            Text("\(run.title ?? run.startedAt.formatted(date: .abbreviated, time: .shortened)) · \(run.posts.count) публикаций").tag(run.id)
                        }
                    }.labelsHidden().frame(maxWidth: 500)
                } else { Text("История поиска").font(.headline) }
                Spacer()
                Button { model.reload() } label: { Label("Обновить", systemImage: "arrow.clockwise") }
                    .disabled(model.busy)
                Button { importing = true } label: { Label("Импорт JSON", systemImage: "square.and.arrow.down") }
                    .disabled(model.busy || model.storageUnavailable)
                Button {
                    pastedJSON = NSPasteboard.general.string(forType: .string) ?? ""
                    model.errorMessage = nil
                    pastingJSON = true
                } label: { Label("Вставить JSON", systemImage: "doc.on.clipboard") }
                    .disabled(model.busy || model.storageUnavailable)
            }.padding(.horizontal, 22).padding(.vertical, 14)
            if let run = currentRun {
                runSummary(run)
                Divider()
                HSplitView {
                    postList(run).frame(minWidth: 280, idealWidth: 320, maxWidth: 400)
                    if let post = currentPost { postDetail(post, run: run).frame(minWidth: 480, maxWidth: .infinity) }
                    else {
                        ContentUnavailableView("Публикаций по фильтру нет", systemImage: "line.3.horizontal.decrease.circle", description: Text("Измени компанию или поисковый текст."))
                            .frame(minWidth: 480, maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
            } else {
                ContentUnavailableView {
                    Label("Первые результаты появятся здесь", systemImage: "text.magnifyingglass")
                } description: {
                    Text("Запусти сбор через оплаченный сайт Telemetr или импортируй результаты из файла.")
                } actions: {
                    Button("Импортировать результаты") { importing = true }
                    Button("Как выполняется сбор") { page = 2 }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    private var jsonPasteSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Вставить результаты Telemetr").font(.title2.weight(.semibold))
            Text("Вставь JSON со сбором публикаций. Он сохранится в истории после проверки формата.")
                .font(.callout).foregroundStyle(.secondary)
            TextEditor(text: $pastedJSON)
                .font(.system(.body, design: .monospaced))
                .padding(8)
                .frame(minHeight: 260)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
                .accessibilityLabel("JSON результатов Telemetr")
                .accessibilityIdentifier("telemetr-json-import")
            if let message = model.errorMessage {
                Text(message).font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Spacer()
                Button("Отмена") { pastingJSON = false }.keyboardShortcut(.cancelAction)
                Button("Импортировать") {
                    if model.importData(Data(pastedJSON.utf8)) {
                        selectedRunID = model.runs.first?.id
                        pastedJSON = ""
                        pastingJSON = false
                    }
                }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(pastedJSON.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.busy || model.storageUnavailable)
            }
        }.padding(24).frame(width: 660, height: 440)
    }

    private func runSummary(_ run: TelemetrCollectionRun) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title = run.title { Text(title).font(.headline) }
            HStack(spacing: 20) {
                Label(originLabel(run.origin), systemImage: run.origin == .telemetrWebsite ? "globe" : "network")
                    .font(.callout.weight(.semibold))
                Text("\(run.queries.count) запроса · \(run.posts.count) публикаций").font(.callout)
                Spacer()
                Text(run.status == .complete ? "Выборка собрана" : "Частичный результат")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if run.origin == .telemetrWebsite {
                Text("Результаты получены через сайт. API-запросы не выполнялись; расход квот API отсутствует.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text(quotaUsage(run)).font(.caption).foregroundStyle(.secondary)
            }
            if run.origin == .telemetrWebsite {
                Text("\(run.posts.filter { $0.textFromCard == true }.count) раскрытых карточек · \(run.posts.filter { $0.textFromCard != true }.count) фрагментов выдачи")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let message = run.errorMessage {
                Text(message).font(.caption).foregroundStyle(.orange)
            }
            DisclosureGroup("Покрытие по компаниям") {
                ScrollView {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(Array(run.queries.enumerated()), id: \.offset) { _, result in
                            let total = result.totalCount.map { " из \($0) в выдаче" } ?? ""
                            Text("\(result.query.companyName): \(result.posts.count)\(total)")
                            if let message = result.errorMessage { Text(message).foregroundStyle(.orange) }
                        }
                    }.font(.caption).frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: 150)
            }.font(.caption)
        }.padding(.horizontal, 22).padding(.bottom, 14)
    }

    private func postList(_ run: TelemetrCollectionRun) -> some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                Picker("Компания", selection: $companyFilter) {
                    Text("Все компании").tag("all")
                    ForEach(uniqueCompanies(run), id: \.companyID) { query in
                        Text("\(query.companyName) · \(run.posts.filter { $0.companyID == query.companyID }.count)").tag(query.companyID)
                    }
                }.labelsHidden().frame(maxWidth: .infinity)
                TextField("Найти в результатах", text: $search).textFieldStyle(.roundedBorder)
            }.padding(14)
            List(selection: Binding(get: { currentPost.map(postKey) }, set: { selectedPostKey = $0 })) {
                ForEach(Array(visiblePosts.enumerated()), id: \.offset) { _, post in
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            Text(post.companyName).font(.caption.weight(.semibold))
                            Spacer()
                            Image(systemName: relevanceSymbol(post.relevance)).foregroundStyle(relevanceColor(post.relevance))
                        }
                        Text(post.channelTitle ?? "Канал не указан").font(.callout.weight(.medium)).lineLimit(1)
                        Text(post.text).font(.callout).foregroundStyle(.secondary).lineLimit(3)
                        Text(post.publishedAt.map { $0.formatted(date: .abbreviated, time: .omitted) } ?? post.sourceDateLabel ?? "Дата не установлена")
                            .font(.caption2).foregroundStyle(.secondary)
                    }.padding(.vertical, 7).tag(postKey(post))
                }
            }.listStyle(.inset)
            HStack { Text("\(visiblePosts.count) публикаций"); Spacer(); Text("\(run.posts.filter { $0.relevance == .confirmed }.count) связей проверено") }
                .font(.caption).foregroundStyle(.secondary).padding(14)
        }
    }

    private func postDetail(_ post: TelemetrPost, run: TelemetrCollectionRun) -> some View {
        return ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 9) {
                    Text(post.companyName).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(post.channelTitle ?? "Публикация Telegram").font(.title2.weight(.semibold))
                    HStack(spacing: 12) {
                        Label(originLabel(post.origin), systemImage: "globe")
                        Text(post.publishedAt.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? post.sourceDateLabel ?? "Дата публикации не установлена")
                    }.font(.caption).foregroundStyle(.secondary)
                }
                if post.origin == .telemetrWebsite && post.textFromCard != true {
                    Label("Фрагмент поисковой выдачи · текст может быть сокращён", systemImage: "text.alignleft")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let summary = post.signalSummary {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Что сообщает публикация").font(.headline)
                        Text(summary)
                    }.font(.callout).textSelection(.enabled)
                    Divider()
                    Text("Текст источника").font(.headline)
                }
                Text(post.text).font(.body).lineSpacing(5).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if post.sourceDeleted == true {
                    Label("Пост удалён из Telegram. Сохранена архивная карточка Telemetr.", systemImage: "archivebox")
                        .font(.callout).foregroundStyle(.secondary)
                }
                if let address = post.sourceURL, let url = URL(string: address), ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                    Link(destination: url) { Label("Открыть публикацию", systemImage: "arrow.up.right.square") }
                    Text(address).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let address = post.sourcePageURL, let url = URL(string: address), url.scheme == "https", url.host == "telemetr.me" {
                    Link("Открыть выдачу Telemetr", destination: url)
                }
                Divider()
                VStack(alignment: .leading, spacing: 9) {
                    Text("Связь с компанией").font(.headline)
                    Label(relevanceLabel(post.relevance), systemImage: relevanceSymbol(post.relevance))
                        .font(.callout).foregroundStyle(relevanceColor(post.relevance))
                    Text("Упоминание может быть рекламой, отзывом или чужим кейсом. Проверяется связь материала с компанией.")
                        .font(.callout).foregroundStyle(.secondary)
                    HStack {
                        Button("Связь подтверждена") { model.setRelevance(.confirmed, post: post) }
                            .disabled(model.storageUnavailable || post.relevance == .confirmed)
                        Button("Не относится") { model.setRelevance(.rejected, post: post) }
                            .disabled(model.storageUnavailable || post.relevance == .rejected)
                        if post.relevance != .unreviewed {
                            Button("Сбросить") { model.setRelevance(.unreviewed, post: post) }.buttonStyle(.link)
                        }
                    }
                    if let error = workspace.persistenceError { Text(error).font(.caption).foregroundStyle(.orange) }
                }
                Divider()
                VStack(alignment: .leading, spacing: 7) {
                    Text("Как найдено").font(.headline)
                    LabeledContent("Запрос", value: post.query)
                    LabeledContent("Получено", value: post.retrievedAt.formatted(date: .abbreviated, time: .shortened))
                    Text(post.textFromCard == true ? "Сохранён текст раскрытой карточки Telemetr. Изображения, видео и комментарии в текст не включены." : post.origin == .telemetrWebsite ? "Сохранён текст, показанный сайтом Telemetr. В выдаче он может быть сокращён." : "Сохранён текст, полученный от API Telemetr.")
                        .foregroundStyle(.secondary)
                }.font(.caption).textSelection(.enabled)
            }.padding(26).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func quotaUsage(_ run: TelemetrCollectionRun) -> String {
        guard let before = run.limitsBefore, let after = run.limitsAfter else {
            return "Фактический расход API не установлен: нет двух замеров квот."
        }
        guard before.model == after.model, before.platform == after.platform else {
            return "В ходе сбора изменились режим доступа или платформа API. Точный расход по двум замерам не установлен."
        }
        func delta(_ old: TelemetrQuotaBucket?, _ new: TelemetrQuotaBucket?) -> String {
            guard let old, let new else { return "нет данных" }
            guard old.resetAt == new.resetAt, old.limit == new.limit, new.used >= old.used else {
                return "не установлен: квота изменилась"
            }
            return String(new.used - old.used)
        }
        return "Изменение квот API: запросы — \(delta(before.requests, after.requests)); публикации — \(delta(before.posts, after.posts)); каналы — \(delta(before.channels, after.channels)); каталог — \(delta(before.search, after.search)). При параллельном использовании ключа сюда попадёт и другой расход."
    }
    private func postKey(_ post: TelemetrPost) -> String { "\(post.companyID)|\(post.id)" }
    private func uniqueCompanies(_ run: TelemetrCollectionRun) -> [TelemetrQuery] {
        var seen = Set<String>()
        return run.queries.map(\.query).filter { seen.insert($0.companyID).inserted }
    }
    private func originLabel(_ origin: TelemetrOrigin) -> String { origin == .telemetrWebsite ? "Telemetr · сайт" : "Telemetr · API" }
    private func relevanceLabel(_ relevance: TelemetrRelevance) -> String {
        switch relevance { case .unreviewed: return "Связь ещё не проверена"; case .confirmed: return "Связь с компанией подтверждена вручную"; case .rejected: return "Не относится к компании" }
    }
    private func relevanceSymbol(_ relevance: TelemetrRelevance) -> String {
        switch relevance { case .unreviewed: return "questionmark.circle"; case .confirmed: return "checkmark.circle"; case .rejected: return "xmark.circle" }
    }
    private func relevanceColor(_ relevance: TelemetrRelevance) -> Color {
        switch relevance { case .unreviewed: return .secondary; case .confirmed: return Color(red: 0.20, green: 0.48, blue: 0.39); case .rejected: return .orange }
    }
}
