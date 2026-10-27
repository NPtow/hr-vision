import SwiftUI
import SignalsCore

struct SourceCollectionView: View {
    @EnvironmentObject private var workspace: SignalsWorkspace
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var model: SourceCollectionModel
    let initialCompanyID: String?
    var initialProvider: SourceProvider? = nil
    @State private var provider: SourceProvider = .yandex
    @State private var companyID = ""
    @State private var companyFilter = ""
    @State private var query = ""
    @State private var page = 0
    @State private var selectedRunID: String?
    @State private var historyFilter = ""
    @State private var historyCompanyID = ""
    @State private var harvestOperation: HarvestOperation = .companies
    @State private var linkedInURL = ""
    @State private var jobTitles = ""

    private var companies: [CompanyDossier] {
        workspace.companies.filter { !$0.isDemo }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    private var visibleCompanies: [CompanyDossier] {
        companies.filter { $0.id == companyID || $0.matchesCatalogSearch(companyFilter) }
    }
    private var company: CompanyDossier? { companies.first { $0.id == companyID } }
    private var providerRuns: [SourceCollectionRun] { model.runs.filter { $0.provider == provider } }
    private var visibleRuns: [SourceCollectionRun] {
        providerRuns.filter { run in
            (historyCompanyID.isEmpty || run.request?.companyID == historyCompanyID) &&
            (historyFilter.isEmpty || [run.request?.companyName, run.request?.query].compactMap { $0 }.joined(separator: " ").localizedStandardContains(historyFilter))
        }
    }
    private var searchedCompanies: [CompanyDossier] {
        let identifiers = Set(providerRuns.compactMap { $0.request?.companyID })
        return companies.filter { identifiers.contains($0.id) }
    }
    private var currentRun: SourceCollectionRun? { visibleRuns.first { $0.id == selectedRunID } ?? visibleRuns.first { $0.request != nil } ?? visibleRuns.first }
    private var busy: Bool { model.busyProviders.contains(provider) }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HStack(spacing: 0) {
                sourceSidebar.frame(width: 225)
                Divider()
                VStack(spacing: 0) {
                    providerHeader
                    Divider()
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 22) {
                            if page == 0 { connection; requestForm }
                            else { history }
                        }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if busy {
                        Divider()
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text(provider == .targetHunter ? "Получаем список доступных инструментов…" : "Выполняем один поисковый запрос…")
                            Spacer()
                        }.font(.callout).padding(16)
                    }
                    messages
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 980, idealWidth: 1060, minHeight: 700, idealHeight: 780)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear {
            if companyID.isEmpty { companyID = initialCompanyID.flatMap { id in companies.contains { $0.id == id } ? id : nil } ?? companies.first?.id ?? "" }
            if query.isEmpty { query = defaultQuery }
            if let initialProvider { provider = initialProvider }
            else { provider = .serper }
            if !model.searchRuns(provider).isEmpty || (provider == .targetHunter && model.targetHunterBatch != nil) { page = 1 }
            if let initialCompanyID, !model.searchRuns(provider, companyID: initialCompanyID).isEmpty {
                historyCompanyID = initialCompanyID
            }
        }
        .onChange(of: companyID) { _, _ in query = defaultQuery }
        .onChange(of: provider) { _, next in
            selectedRunID = nil
            historyFilter = ""
            if let initialCompanyID, !model.searchRuns(next, companyID: initialCompanyID).isEmpty {
                historyCompanyID = initialCompanyID
            } else { historyCompanyID = "" }
        }
        .task {
            while !Task.isCancelled {
                await model.refreshFromDisk()
                do { try await Task.sleep(for: .seconds(5)) } catch { break }
            }
        }
    }

    private var defaultQuery: String { company.map { "\"\($0.name)\"" } ?? "" }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Label("Сервисы поиска", systemImage: "network").font(.title2.weight(.semibold))
                Text("Результаты поиска и подключение сервисов").font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Готово") { dismiss() }.keyboardShortcut(.cancelAction)
        }.padding(22)
    }

    private var sourceSidebar: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("СЕРВИСЫ").font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.horizontal, 14).padding(.top, 20)
            ForEach([SourceProvider.yandex, .serper, .targetHunter, .harvest]) { item in
                Button { provider = item } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: item.symbol).font(.title3).frame(width: 22).padding(.top, 2)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.uiTitle).font(.callout.weight(.semibold))
                            Text(model.status(item)).font(.caption).foregroundStyle(.secondary)
                            let count = model.runs.filter { $0.provider == item }.count
                            if count > 0 { Text("В истории: \(count)").font(.caption2).foregroundStyle(.secondary) }
                        }
                        Spacer(minLength: 0)
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        .background(provider == item ? Color.accentColor.opacity(0.1) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                }.buttonStyle(.plain).accessibilityIdentifier("source-provider-\(item.rawValue)")
            }.padding(.horizontal, 8)
            Spacer()
            Text("Оплата и вход в кабинет сами по себе не подтверждают доступ приложения. Его проверяет запрос с ключом.")
                .font(.caption).foregroundStyle(.secondary).padding(16)
        }.background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
    }

    private var providerHeader: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text(provider.uiTitle).font(.title3.weight(.semibold))
                Label(model.status(provider), systemImage: model.verified.contains(provider) ? "checkmark.circle" : "key")
                    .font(.caption).foregroundStyle(model.verified.contains(provider) ? Color.green : Color.secondary)
            }
            Spacer()
            Picker("Раздел источника", selection: $page) {
                Text("Подключение и поиск").tag(0)
                Text("Результаты").tag(1)
            }.pickerStyle(.segmented).labelsHidden().frame(width: 315)
        }.padding(20)
    }

    private var connection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(provider.explanation).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Link(destination: provider.accountURL) { Label(provider == .targetHunter ? "Получить токен" : "Кабинет и ключ", systemImage: "arrow.up.right.square") }
                    .buttonStyle(.bordered)
                if let payment = provider.paymentURL {
                    Link("Тарифы и оплата", destination: payment).buttonStyle(.bordered)
                }
            }
            if provider == .serper {
                Text("Serper предлагает 2 500 стартовых запросов без карты. Проверь выданный лимит в кабинете перед покупкой.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if provider == .targetHunter {
                Text("Обычная «Автоматизация» — 2 390 ₽/мес. на 11.10.2026. Включённость MCP в тариф не подтверждена: сначала проверь токен и доступные функции, затем решай об оплате.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if provider == .harvest {
                Text("Начни с компании, затем выбери её страницу LinkedIn и найди людей. Публикации загружаются отдельно по ссылке на профиль.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(provider == .targetHunter ? "Токен доступа TargetHunter" : "API-ключ").font(.callout.weight(.medium))
                SecureField("Вставить ключ", text: Binding(get: { model.credential(provider).apiKey }, set: { model.setKey($0, provider: provider) }))
                    .textFieldStyle(.roundedBorder).disabled(busy).accessibilityIdentifier("source-api-key")
                if provider == .yandex {
                    TextField("ID каталога Yandex Cloud", text: Binding(get: { model.credential(provider).folderID }, set: { model.setFolder($0, provider: provider) }))
                        .textFieldStyle(.roundedBorder).disabled(busy).accessibilityIdentifier("source-folder-id")
                    Text("Роль сервисного аккаунта: search-api.webSearch.user. Область API-ключа: yc.search-api.execute. У каталога должен быть активный платёжный аккаунт.")
                        .font(.caption).foregroundStyle(.secondary)
                    Link("Как настроить Search API", destination: URL(string: "https://aistudio.yandex.ru/ru/docs/search-api/operations/web-search-sync")!)
                        .font(.caption)
                } else if provider == .targetHunter {
                    Text("Для токена требуется право vk-parser. Доступность поиска зависит от прав и тарифа аккаунта.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                Button(provider == .targetHunter ? "Сохранить локально" : "Сохранить в Связку ключей") { model.saveCredentials(provider) }.disabled(!model.hasCredentials(provider) || busy)
                Button("Загрузить сохранённый") { model.loadCredentials(provider) }.disabled(busy)
                Menu {
                    Button("Удалить сохранённый доступ", role: .destructive) { model.removeCredentials(provider) }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize().disabled(busy)
            }.controlSize(.small)
            Text(provider == .targetHunter
                 ? "Сохранённый токен автоматически загружается приложением и локальным сборщиком."
                 : "Ключ остаётся в памяти до закрытия приложения. Сохранение в Связку ключей — отдельное действие.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var requestForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            Divider()
            Text(provider == .targetHunter ? "Проверить доступ к VK" : "Первый поиск").font(.headline)
            if provider == .targetHunter {
                Text("Эта проверка получает только список функций аккаунта. Результаты пакетного поиска сообществ по компаниям доступны в разделе «Результаты».")
                    .font(.callout).foregroundStyle(.secondary)
                Button {
                    runRequest()
                } label: { Label("Проверить доступ и функции", systemImage: "network") }
                    .buttonStyle(.borderedProminent).disabled(!model.canRequest(provider))
                    .accessibilityIdentifier("source-check-tools")
            } else if provider == .harvest {
                harvestForm
            } else {
                TextField("Найти компанию в каталоге", text: $companyFilter).textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("source-company-search")
                Picker("Компания", selection: $companyID) {
                    ForEach(visibleCompanies) { item in Text(item.name).tag(item.id) }
                }.accessibilityIdentifier("source-company-picker").disabled(busy)
                TextField("Поисковый запрос", text: $query).textFieldStyle(.roundedBorder).disabled(busy)
                    .accessibilityIdentifier("source-search-query")
                Text(provider.requestNote).font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button { runRequest() } label: { Label("Найти до 10 ссылок", systemImage: "magnifyingglass") }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canRequest(provider) || company == nil || query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("source-search-submit")
                    Spacer()
                    Text("Одна компания · один запрос").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var harvestForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Найти компанию в каталоге", text: $companyFilter).textFieldStyle(.roundedBorder)
            Picker("Компания", selection: Binding(get: { companyID }, set: { companyID = $0; linkedInURL = "" })) {
                ForEach(visibleCompanies) { Text($0.name).tag($0.id) }
            }.disabled(busy)
            Picker("Что получить", selection: $harvestOperation) {
                ForEach(HarvestOperation.allCases) { Text($0.title).tag($0) }
            }.pickerStyle(.segmented).disabled(busy)
            if harvestOperation == .companies {
                TextField("Название компании", text: $query).textFieldStyle(.roundedBorder).disabled(busy)
            } else {
                TextField(harvestOperation == .people ? "https://www.linkedin.com/company/…" : "https://www.linkedin.com/in/…", text: $linkedInURL)
                    .textFieldStyle(.roundedBorder).disabled(busy).accessibilityIdentifier("harvest-target-url")
                if harvestOperation == .people {
                    TextField("Должности через запятую · пусто — все роли", text: $jobTitles)
                        .textFieldStyle(.roundedBorder).disabled(busy)
                }
            }
            Text("Первая страница, до 100 записей. Без автоматического продолжения и повторов. Ориентир Free / Starter на 11.10.2026: \(harvestOperation == .people ? "$0,10" : "$0,004") за запрос; текущую ставку смотри в кабинете.")
                .font(.caption).foregroundStyle(.secondary)
            Button {
                guard let company else { return }
                let operation = harvestOperation, target = linkedInURL, titles = jobTitles
                let search = query.trimmingCharacters(in: CharacterSet(charactersIn: "\" "))
                Task {
                    await model.performHarvest(company: company, operation: operation, search: search, targetURL: target, jobTitles: titles)
                    if model.errors[.harvest] == nil { page = 1; historyCompanyID = company.id }
                }
            } label: { Label(harvestOperation.title, systemImage: "magnifyingglass") }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canRequest(.harvest) || company == nil || (harvestOperation == .companies ? query.isEmpty : linkedInURL.isEmpty))
                .accessibilityIdentifier("harvest-search-submit")
        }
    }

    private func runRequest() {
        let requestedProvider = provider
        let requestedCompany = company
        let requestedQuery = query
        Task {
            await model.perform(requestedProvider, company: requestedCompany, query: requestedQuery)
            if provider == requestedProvider, model.errors[requestedProvider] == nil {
                selectedRunID = model.runs.first(where: { $0.provider == requestedProvider })?.id
                page = 1
            }
        }
    }

    @ViewBuilder private var history: some View {
        if provider == .targetHunter { targetHunterCoverage }
        if !providerRuns.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    TextField("Найти компанию или запрос в результатах", text: $historyFilter)
                        .textFieldStyle(.roundedBorder).accessibilityIdentifier("source-history-search")
                    Button { Task { await model.refreshFromDisk(force: true) } } label: {
                        Label("Обновить", systemImage: "arrow.clockwise")
                    }.disabled(model.refreshing).accessibilityIdentifier("source-history-refresh")
                }
                Picker("Компания", selection: $historyCompanyID) {
                    Text("Все компании").tag("")
                    ForEach(searchedCompanies) { item in Text(item.name).tag(item.id) }
                }.accessibilityIdentifier("source-history-company")
            }
        }
        if let run = currentRun {
            Picker("Запуск", selection: Binding(get: { currentRun?.id ?? "" }, set: { selectedRunID = $0 })) {
                ForEach(visibleRuns) { item in
                    Text("\(item.request?.companyName ?? "Проверка доступа") · \(item.results.count) · \(item.startedAt.formatted(date: .abbreviated, time: .shortened))").tag(item.id)
                }
            }.accessibilityIdentifier("source-run-history")
            if let request = run.request {
                VStack(alignment: .leading, spacing: 6) {
                    Text(request.companyName).font(.title3.weight(.semibold))
                    Text("Запрос: \(request.query)").font(.callout).textSelection(.enabled)
                    Text(resultCountLabel(run)).font(.callout.weight(.medium))
                    if let harvest = run.harvest {
                        Text("\(harvest.operation.title) · страница 1 · получено от сервиса \(harvest.receivedCount), сохранено \(run.results.count)")
                            .font(.caption).foregroundStyle(.secondary)
                        if harvest.operation != .posts, let total = harvest.reportedCount {
                            Text("Всего совпадений по данным сервиса: \(total). Это не число подтверждённых связей с компанией.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text(run.completedAt.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(provider == .targetHunter
                         ? "Совпадения по названию сообщества. Принадлежность компании ещё не подтверждена."
                         : provider == .harvest
                         ? "Данные LinkedIn через HarvestAPI. Связь человека или страницы с компанией требует проверки. Репост не означает авторство."
                         : "Это выдача поисковика. Текст страницы и связь с компанией требуют проверки.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if run.results.isEmpty {
                    if run.harvest?.operation == .posts {
                        ContentUnavailableView("Публикации не получены", systemImage: "text.magnifyingglass", description: Text("Сервис вернул пустую первую страницу. Это не подтверждает отсутствие публикаций в LinkedIn."))
                    } else {
                        ContentUnavailableView(provider == .targetHunter ? "Сообщества не найдены" : "По запросу нет ссылок", systemImage: "magnifyingglass", description: Text(provider == .targetHunter ? "По этому запросу TargetHunter не вернул сообществ. Это не означает, что у компании нет страницы VK." : "Доступ работает. Попробуй другое название или уточни запрос."))
                    }
                } else {
                    ForEach(run.results) { result in resultCard(result, run: run) }
                }
                if let task = run.targetHunterTask, task.completeness != .complete {
                    Label(task.completeness == .partial
                          ? "Сохранена часть выдачи TargetHunter. Остальные сообщества в этом запуске не получены."
                          : "Полнота выдачи TargetHunter не установлена.", systemImage: "info.circle")
                        .font(.caption).foregroundStyle(.secondary)
                } else if run.hasMore {
                    Text("Выдача может продолжаться. Следующие страницы не загружены.").font(.caption).foregroundStyle(.secondary)
                }
            } else { toolDiscovery(run) }
        } else if !providerRuns.isEmpty {
            ContentUnavailableView("Нет результатов по этому фильтру", systemImage: "line.3.horizontal.decrease.circle", description: Text("Выбери все компании или измени текст поиска."))
        } else {
            ContentUnavailableView {
                Label(provider == .targetHunter && model.targetHunterBatch != nil ? "Сбор ещё не вернул результаты" : "Запросов пока нет", systemImage: "text.magnifyingglass")
            } description: {
                Text(provider == .targetHunter && model.targetHunterBatch != nil
                     ? "Полученные сообщества появятся здесь автоматически."
                     : "Результаты сохраняются на этом Mac и доступны после перезапуска приложения.")
            } actions: { Button("К подключению") { page = 0 } }
                .frame(maxWidth: .infinity, minHeight: 200)
        }
    }

    private var targetHunterCoverage: some View {
        let searches = model.searchRuns(.targetHunter)
        let byCompany = Dictionary(grouping: searches, by: { $0.request?.companyID ?? "" })
        let latest = byCompany.values.compactMap { $0.first }
        let savedResults = latest.reduce(0) { $0 + $1.results.count }
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Поиск сообществ VK").font(.headline)
                Spacer()
                Text("\(byCompany.count) из \(companies.count) компаний").font(.callout.weight(.medium))
                    .monospacedDigit().accessibilityIdentifier("source-vk-coverage")
            }
            ProgressView(value: Double(byCompany.count), total: Double(max(companies.count, 1)))
                .accessibilityLabel("Компании с сохранённым результатом поиска VK")
            HStack(spacing: 18) {
                Text("\(savedResults) совпадений сохранено")
                Text("\(latest.filter { $0.results.isEmpty }.count) без совпадений")
            }.font(.caption).foregroundStyle(.secondary)
            if let batch = model.targetHunterBatch {
                HStack(spacing: 8) {
                    if batch.running && Date().timeIntervalSince(batch.updatedAt) < 120 {
                        ProgressView().controlSize(.mini)
                        Text("Сбор выполняется")
                    } else if batch.imported >= batch.selectedCompanies && batch.selectedCompanies > 0 {
                        Image(systemName: "checkmark.circle")
                        Text("Сбор завершён")
                    } else {
                        Image(systemName: "pause.circle")
                        Text(batch.running ? "Состояние сбора давно не обновлялось" : "Сбор приостановлен")
                    }
                    Spacer()
                    Text("Обновлено \(batch.updatedAt.formatted(date: .omitted, time: .shortened))")
                }.font(.caption).foregroundStyle(.secondary)
                Text("Ожидают: \(batch.planned + batch.pending) · Ошибки: \(batch.failed) · Нужна проверка: \(batch.uncertain) · Частичная выдача: \(batch.partial)")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                if batch.completed > batch.imported {
                    Text("Получено от сервиса, ожидает сохранения в приложении: \(batch.completed - batch.imported)")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let error = model.batchReadError {
                Text(error).font(.caption).foregroundStyle(.orange)
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private func resultCountLabel(_ run: SourceCollectionRun) -> String {
        guard let task = run.targetHunterTask else { return "\(run.results.count) ссылок" }
        if let reported = task.reportedCount {
            return "Получено \(task.receivedCount) из \(reported) сообществ" + (task.completeness == .partial ? " · частичная выдача" : "")
        }
        return "Получено \(task.receivedCount) сообществ · общее число неизвестно"
    }

    @ViewBuilder private func toolDiscovery(_ run: SourceCollectionRun) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Доступные функции TargetHunter").font(.title3.weight(.semibold))
            Text("Получено: \(run.tools.count). Этот запуск проверяет доступ к функциям.").font(.callout).foregroundStyle(.secondary)
            if run.hasMore {
                Text("Список неполный: сервер сообщил о продолжении. Следующая страница автоматически не загружалась.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        if run.tools.isEmpty {
            ContentUnavailableView("Сервис вернул пустой список", systemImage: "list.bullet", description: Text("Проверь права и тариф аккаунта в кабинете TargetHunter."))
        }
        ForEach(run.tools) { tool in
            VStack(alignment: .leading, spacing: 6) {
                Text(tool.name).font(.callout.weight(.semibold))
                Text(tool.description).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private func resultCard(_ result: SourceSearchResult, run: SourceCollectionRun) -> some View {
        return VStack(alignment: .leading, spacing: 10) {
            Text(result.title).font(.headline).textSelection(.enabled)
            if let item = result.linkedIn {
                Text(item.operation == .people ? "Место работы указано в профиле · актуальность не проверена" : "Связь с компанией не проверена")
                    .font(.caption).foregroundStyle(.secondary)
                if let date = item.publishedAt { Text("Опубликовано: \(date)").font(.caption).foregroundStyle(.secondary) }
                if item.isRepost { Label("Репост: автор и распространитель могут различаться", systemImage: "arrow.2.squarepath").font(.caption).foregroundStyle(.secondary) }
                if let author = item.authorName { Text("Автор: \(author)").font(.caption).foregroundStyle(.secondary) }
                if item.operation != .posts {
                    Button(item.operation == .companies ? "Искать людей по этой странице" : "Читать публикации профиля") {
                        companyID = result.companyID
                        linkedInURL = result.url
                        harvestOperation = item.operation == .companies ? .people : .posts
                        page = 0
                    }.controlSize(.small)
                }
            }
            if let community = result.vkCommunity {
                HStack(spacing: 16) {
                    if let members = community.membersCount {
                        Label("\(members.formatted()) участников", systemImage: "person.2")
                    }
                    if let closed = community.isClosed {
                        Label(closed == 0 ? "Открытое" : closed == 1 ? "Закрытое" : "Частное", systemImage: closed == 0 ? "globe" : "lock")
                    }
                }.font(.caption).foregroundStyle(.secondary)
                Text("Принадлежность компании не проверена").font(.caption).foregroundStyle(.secondary)
            }
            if !result.snippet.isEmpty { Text(result.snippet).font(.callout).textSelection(.enabled) }
            Text(result.url).font(.caption).foregroundStyle(.secondary).lineLimit(2).textSelection(.enabled)
            HStack {
                if let url = URL(string: result.url), ["https", "http"].contains(url.scheme?.lowercased() ?? "") {
                    Link(destination: url) { Label("Открыть источник", systemImage: "arrow.up.right.square") }
                }
            }.font(.caption).controlSize(.small)
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 9))
    }

    @ViewBuilder private var messages: some View {
        if let message = model.errors[provider] ?? model.storageError {
            Divider()
            Label(message, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                .textSelection(.enabled).padding(16).frame(maxWidth: .infinity, alignment: .leading)
        } else if let message = model.notices[provider] {
            Divider()
            Text(message).font(.caption).foregroundStyle(.secondary).padding(16).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private extension SourceProvider {
    var uiTitle: String {
        switch self {
        case .yandex: return "Яндекс · Search API"
        case .serper: return "Google · Serper"
        case .targetHunter: return "VK · TargetHunter"
        case .harvest: return "LinkedIn · HarvestAPI"
        }
    }
    var symbol: String { self == .targetHunter ? "person.3" : "globe" }
    var explanation: String {
        switch self {
        case .yandex: return "Поиск по русскоязычному вебу: сайты школ, новости, интервью и публичные страницы. Подключается через Yandex Cloud."
        case .serper: return "Выдача Google через Serper: упоминания компании, интервью и публичные страницы. Сервис возвращает ссылки и фрагменты найденного текста."
        case .targetHunter: return "Поиск сообществ VK по названиям компаний через TargetHunter. Результаты пакетного сбора доступны по каждой компании и обновляются, пока это окно открыто."
        case .harvest: return "Компании, люди и публикации LinkedIn через HarvestAPI. Каждый шаг сохраняет источник и дату; совпадения требуют проверки."
        }
    }
    var accountURL: URL {
        switch self {
        case .yandex: return URL(string: "https://console.yandex.cloud/")!
        case .serper: return URL(string: "https://serper.dev/login")!
        case .targetHunter: return URL(string: "https://targethunter.ru/settings/access")!
        case .harvest: return URL(string: "https://harvestapi.io/admin/api-keys")!
        }
    }
    var paymentURL: URL? {
        self == .targetHunter ? URL(string: "https://vk.targethunter.ru/license") : self == .harvest ? URL(string: "https://harvestapi.io/admin/account?tab=rates") : nil
    }
    var requestNote: String {
        switch self {
        case .yandex: return "Один синхронный запрос Search API, до 10 ссылок: не более 0,488 ₽ по проверенной дневной ставке на 11.10.2026, включая НДС. Автоповтора нет."
        case .serper: return "Один запрос из квоты Serper, до 10 ссылок. 2 500 стартовых запросов без карты — выдачу квоты проверь в кабинете. Автоповтора нет."
        case .targetHunter: return "Проверка доступа получает только список доступных функций."
        case .harvest: return "Один запрос HarvestAPI. Следующие страницы загружаются отдельно."
        }
    }
}
