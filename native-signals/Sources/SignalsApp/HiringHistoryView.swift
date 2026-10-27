import SwiftUI
import SignalsCore

struct HiringHistoryView: View {
    let research: HiringResearch
    let start: Date
    let end: Date
    let onOpenMaterial: (String) -> Void
    var onAddToBoard: ((HiringPublication) -> Void)? = nil
    @State private var scope = "dated"
    @State private var query = ""
    @State private var source = "Все источники"
    @State private var function = "Все функции"
    @State private var selectedID: String?
    @State private var showCoverage = false

    private var candidates: [HiringPublication] {
        scope == "undated" ? research.undatedPublications : scope == "uncertain" ? research.uncertainPublications : research.publications
    }
    private var rows: [HiringPublication] {
        candidates.filter {
            (source == "Все источники" || $0.source == source) &&
            (function == "Все функции" || $0.function == function) &&
            (query.isEmpty || ($0.title + " " + $0.text).localizedCaseInsensitiveContains(query))
        }.sorted { ($0.publishedAt ?? .distantPast) > ($1.publishedAt ?? .distantPast) }
    }
    private var selected: HiringPublication? { rows.first { $0.id == selectedID } ?? rows.first }
    private var allSources: [String] { Array(Set(candidates.map(\.source))).sorted() }
    private var allFunctions: [String] { Array(Set(candidates.map(\.function))).sorted() }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("История публикаций о найме").font(.headline)
                Text(start.formatted(date: .numeric, time: .omitted) + " — " + end.formatted(date: .numeric, time: .omitted))
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("Покрытие источников") { showCoverage.toggle() }.accessibilityIdentifier("hiring-coverage")
            }.padding(.horizontal, 20).padding(.top, 14)
            Text("Размещения не равны новым сотрудникам. Повторные объявления сохранены; полнота истории зависит от источника.")
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 20).padding(.top, 5)
            if showCoverage { coverage.padding(.horizontal, 20).padding(.top, 10) }
            monthChart.padding(.horizontal, 20).padding(.vertical, 12)
            HStack(spacing: 12) {
                Picker("Дата и проверка", selection: $scope) {
                    Text("За период · \(research.publications.count)").tag("dated")
                    Text("Без даты · \(research.undatedPublications.count)").tag("undated")
                    Text("Проверить · \(research.uncertainPublications.count)").tag("uncertain")
                }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 550).accessibilityIdentifier("hiring-scope")
                Spacer()
            }.padding(.horizontal, 20)
            HStack {
                TextField("Роль или слова в объявлении", text: $query).textFieldStyle(.roundedBorder).accessibilityIdentifier("hiring-search")
                Picker("Источник", selection: $source) {
                    Text("Все источники").tag("Все источники")
                    ForEach(allSources, id: \.self) { Text($0).tag($0) }
                }.labelsHidden().frame(width: 150)
                Picker("Функция", selection: $function) {
                    Text("Все функции").tag("Все функции")
                    ForEach(allFunctions, id: \.self) { Text($0).tag($0) }
                }.labelsHidden().frame(width: 180)
            }.padding(.horizontal, 20).padding(.vertical, 10)
            HSplitView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Найдено размещений: \(rows.count)").font(.caption).foregroundStyle(.secondary).padding(10).accessibilityIdentifier("hiring-count")
                    List(rows, selection: $selectedID) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(item.title).font(.body.weight(.medium)).lineLimit(2)
                            Text(item.function + " · " + item.source).font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Text(item.publishedAt?.formatted(date: .numeric, time: .omitted) ?? "Дата публикации неизвестна")
                                Spacer()
                                Text(statusLabel(item.status))
                            }.font(.caption2).foregroundStyle(.secondary)
                            if item.possibleRepeatCount > 0 { Text("Похожие роли: ещё \(item.possibleRepeatCount) · не объединены").font(.caption2).foregroundStyle(.secondary) }
                        }.padding(.vertical, 5).tag(item.id).accessibilityElement(children: .combine)
                    }.listStyle(.inset)
                    if rows.isEmpty {
                        ContentUnavailableView("Объявления не найдены", systemImage: "briefcase", description: Text(scope == "dated" ? "В сохранённых источниках нет подтверждённых объявлений с датой за этот период. Проверь покрытие и записи без даты." : "По этому фильтру записей нет."))
                    }
                }.frame(minWidth: 300, idealWidth: 410, maxWidth: 500)
                if let item = selected { detail(item).frame(minWidth: 320, maxWidth: .infinity) }
                else { Color.clear.frame(maxWidth: .infinity) }
            }
        }
        .onChange(of: scope) { _, _ in source = "Все источники"; function = "Все функции"; selectedID = nil }
        .onChange(of: rows.map(\.id)) { _, ids in if selectedID == nil || !ids.contains(selectedID!) { selectedID = ids.first } }
    }

    private var monthChart: some View {
        let maxCount = max(research.monthlyCounts.map(\.count).max() ?? 1, 1)
        return HStack(alignment: .bottom, spacing: 14) {
            ForEach(research.monthlyCounts) { month in
                VStack(spacing: 3) {
                    Text("\(month.count)").font(.caption.monospacedDigit())
                    RoundedRectangle(cornerRadius: 3).fill(month.count == 0 ? Color.secondary.opacity(0.14) : Color.accentColor.opacity(0.6))
                        .frame(height: month.count == 0 ? 3 : max(5, CGFloat(month.count) / CGFloat(maxCount) * 33))
                    Text(month.label).font(.caption2).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity).accessibilityElement(children: .combine)
            }
            Text("Найденные\nпубликации\nпо месяцам").font(.caption2).foregroundStyle(.secondary)
        }.frame(height: 68, alignment: .bottom).help("Числа относятся к собранным объявлениям. Ноль означает отсутствие находок в сохранённых данных, а не отсутствие найма.")
    }

    private var coverage: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 9) {
                ForEach(Array(research.sourceCoverage.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .top) {
                        Text(item.source).font(.caption.weight(.semibold)).frame(width: 90, alignment: .leading)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(coverageLabel(item.status) + " · получено \(item.receivedCount)").font(.caption)
                            Text(item.note).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            if let raw = item.sourceURL, let url = URL(string: raw), ["https", "http"].contains(url.scheme ?? "") { Link(raw, destination: url).font(.caption2).lineLimit(1) }
                        }
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }.frame(maxHeight: 165).padding(10).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
    }

    private func detail(_ item: HiringPublication) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(item.title).font(.title2.weight(.semibold)).textSelection(.enabled)
                Text(item.source + " · " + item.function).font(.callout).foregroundStyle(.secondary)
                Text(item.publishedAt.map { "Опубликовано " + $0.formatted(date: .abbreviated, time: .omitted) } ?? "Дата публикации неизвестна — не включено в динамику")
                    .font(.caption).foregroundStyle(.secondary)
                Text(statusLabel(item.status) + (item.statusObservedAt.map { " · проверено " + $0.formatted(date: .abbreviated, time: .omitted) } ?? "") + " · текст получен " + item.lastObservedAt.formatted(date: .abbreviated, time: .omitted)).font(.caption).foregroundStyle(.secondary)
                if scope == "uncertain" { Text("Требует проверки: связь с компанией, полнота текста или характер объявления не подтверждены.").font(.callout).foregroundStyle(.orange) }
                if let salary = item.salary, !salary.isEmpty { LabeledContent("Оплата", value: salary) }
                if let location = item.location, !location.isEmpty { LabeledContent("Место", value: location) }
                if let employment = item.employment, !employment.isEmpty { LabeledContent("Формат", value: employment) }
                HStack {
                    Button("Материал") { onOpenMaterial(item.materialID) }
                    if let raw = item.url, let url = URL(string: raw), ["https", "http"].contains(url.scheme ?? "") { Link("Оригинал ↗", destination: url) }
                    Spacer()
                    if let add = onAddToBoard { Button("В разбор", systemImage: "plus.rectangle.on.rectangle") { add(item) } }
                }
                Divider()
                Text(item.text).font(.system(size: 14)).lineSpacing(5).textSelection(.enabled)
                if let contact = item.contact, !contact.isEmpty { Text("Опубликованный контакт: " + contact).font(.callout).textSelection(.enabled) }
            }.padding(22).frame(maxWidth: .infinity, alignment: .leading)
        }.accessibilityIdentifier("hiring-detail")
    }
    private func statusLabel(_ value: String) -> String {
        switch value { case "active": return "Активна при проверке"; case "archived": return "В архиве источника"; case "unavailable": return "Недоступна"; default: return "Статус неизвестен" }
    }
    private func coverageLabel(_ value: String) -> String {
        switch value { case "completeWithinScope": return "Пройден указанный источник"; case "unavailable": return "Недоступно"; case "notStarted": return "Не проверено"; case "noResults": return "Нет находок"; default: return "Частичное покрытие" }
    }
}
