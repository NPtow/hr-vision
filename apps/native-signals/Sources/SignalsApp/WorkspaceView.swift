import SwiftUI
import SignalsCore

private enum ResearchTab: String, CaseIterable { case materials = "Материалы", hiring = "Найм", contacts = "Контакты", board = "Разбор" }

struct WorkspaceView: View {
    @EnvironmentObject private var workspace: SignalsWorkspace
    @StateObject private var telemetr = TelemetrCollectionModel()
    @StateObject private var sources = SourceCollectionModel()
    @StateObject private var historyCollector = HistoryCollectionModel()
    @State private var showHistoryCollector = false
    @AppStorage("research-company-selection") private var rememberedCompany = "discovery-partaonline-ru"
    @State private var selection: String?
    @State private var companySearch = ""
    @AppStorage("research-segment") private var segment = "Школьное образование и экзамены"
    @State private var tab: ResearchTab = .materials
    @State private var query = ""
    @State private var kind: ResearchMaterialKind = .all
    @State private var provider = "Все источники"
    @State private var onlySaved = false
    @State private var showOtherPeople = false
    @State private var showUnverifiedAddresses = false
    @State private var showFragments = false
    @State private var selectedMaterialID: String?
    @State private var selectedContactID: String?
    @State private var research = CompanyResearch()
    @State private var historyEnd = Date()
    @State private var cachedHiring: HiringResearch?
    @State private var boardError: String?
    @State private var addingSource = false
    @State private var showNote = false
    @State private var columnVisibility: NavigationSplitViewVisibility = .all

    private var company: CompanyDossier? { workspace.companies.first { $0.id == selection && !$0.isDemo } }
    private var companies: [CompanyDossier] { workspace.companies.filter { !$0.isDemo } }
    private var visibleCompanies: [CompanyDossier] {
        companies.filter { $0.matchesCatalogSearch(companySearch) && (segment.isEmpty || $0.catalogSegment == segment) }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    private var providers: [String] { Array(Set(research.materials.flatMap(\.providers))).sorted() }
    private var visibleMaterials: [ResearchMaterial] {
        research.materials.filter { item in
            (query.isEmpty || item.searchText.localizedCaseInsensitiveContains(query)) &&
            (kind == .all || item.kind == kind) && (provider == "Все источники" || item.providers.contains(provider)) &&
            (!onlySaved || isSaved(item)) && (showFragments ? !item.hasFullText : item.hasFullText)
        }
    }
    private var material: ResearchMaterial? { visibleMaterials.first { $0.id == selectedMaterialID } }
    private var contact: ResearchContact? { (primaryContacts + (showOtherPeople ? otherContacts : [])).first { $0.id == selectedContactID } }
    private var primaryContacts: [ResearchContact] { (research.linkedPeople + research.publishedContacts + (showUnverifiedAddresses ? research.addresses.filter { $0.affiliation != .published } : [])).filter(contactMatches) }
    private var otherContacts: [ResearchContact] { research.otherPeople.filter(contactMatches) }
    private var historyStart: Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Moscow")!
        return calendar.date(byAdding: .month, value: -6, to: calendar.startOfDay(for: historyEnd))!
    }
    private var hiringResearch: HiringResearch {
        cachedHiring ?? HiringResearch.build(companyID: "", materials: [], archives: [], start: historyStart, end: historyEnd)
    }

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebar.navigationSplitViewColumnWidth(min: 220, ideal: 245, max: 300)
        } detail: {
            if let company { companyView(company) }
            else { ContentUnavailableView("Выбери компанию", systemImage: "building.2", description: Text("Поиск и направления — в списке слева.")) }
        }
        .navigationTitle("TG Рассылщик")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if tab != .board {
                Button("Обновить данные", systemImage: "arrow.clockwise") {
                    Task { await sources.refreshFromDisk(force: true); telemetr.reload(); workspace.reloadPublicResearch(); rebuild() }
                }.disabled(sources.refreshing).help("Перечитать сохранённые результаты. Новых запросов к сервисам нет.")
                    .accessibilityIdentifier("research-refresh")
                Menu {
                    Button("Поиск Google, Яндекс, LinkedIn…") { workspace.showSources = true }
                    Button("История сборов Telegram…") { telemetr.reload(); workspace.showTelemetr = true }
                } label: { Text("Сервисы") }.accessibilityIdentifier("research-services")
                Button("Собрать", systemImage: "tray.and.arrow.down") { showHistoryCollector = true }
                    .disabled(company?.catalogSegment != "Школьное образование и экзамены")
                    .accessibilityIdentifier("history-tools")
                Button("Добавить материал", systemImage: "plus") { addingSource = true }
                    .disabled(company == nil || workspace.editingDisabled)
                }
            }
        }
        .sheet(isPresented: $addingSource) { if let company { AddEvidenceSheet(company: company) } }
        .sheet(isPresented: $workspace.showTelemetr) { TelemetrCollectionView(model: telemetr) }
        .sheet(isPresented: $workspace.showSources) { SourceCollectionView(model: sources, initialCompanyID: selection) }
        .sheet(isPresented: $workspace.showAbout) { about }
        .sheet(isPresented: $showHistoryCollector) {
            if let company { HistoryCollectionSheet(model: historyCollector, company: company, start: historyStart) { workspace.reloadPublicResearch(); historyEnd = Date(); rebuild() } }
        }
        .onAppear { selection = visibleCompanies.contains { $0.id == rememberedCompany } ? rememberedCompany : visibleCompanies.first?.id; rebuild() }
        .onChange(of: selection) { _, value in
            if let value { rememberedCompany = value }
            query = ""; kind = .all; provider = "Все источники"; onlySaved = false
            selectedMaterialID = nil; selectedContactID = nil; showOtherPeople = false; showUnverifiedAddresses = false; showFragments = false; rebuild()
        }
        .onChange(of: visibleCompanies.map(\.id)) { _, ids in
            if selection == nil || !ids.contains(selection!) { selection = ids.first }
        }
        .onChange(of: visibleMaterials.map(\.id)) { _, ids in
            if selectedMaterialID == nil || !ids.contains(selectedMaterialID!) { selectedMaterialID = ids.first }
        }
        .onChange(of: tab) { _, _ in query = "" }
        .onReceive(sources.$runs) { _ in DispatchQueue.main.async { rebuild() } }
        .onReceive(telemetr.$runs) { _ in DispatchQueue.main.async { rebuild() } }
        .onReceive(workspace.$snapshot) { _ in DispatchQueue.main.async { rebuild() } }
        .onReceive(workspace.$publicResearch) { _ in DispatchQueue.main.async { rebuild() } }
        .onReceive(workspace.$sourceHistory) { _ in DispatchQueue.main.async { rebuild() } }
        .task { await sources.refreshFromDisk(); rebuild() }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Компании").font(.title2.weight(.semibold)).padding(.top, 12)
            TextField("Найти компанию", text: $companySearch).textFieldStyle(.roundedBorder).accessibilityIdentifier("company-search")
            Picker("Направление", selection: $segment) {
                Text("Все направления").tag("")
                ForEach(Array(Set(companies.map(\.catalogSegment))).sorted(), id: \.self) { Text($0).tag($0) }
            }.labelsHidden().accessibilityIdentifier("company-segment-filter")
            Text("\(visibleCompanies.count) из \(companies.count)").font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("company-catalog-count")
            if let issue = workspace.catalogError { Text(issue).font(.caption).foregroundStyle(.orange) }
            List(selection: $selection) {
                ForEach(visibleCompanies) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.name).font(.body.weight(.medium)).lineLimit(1)
                        if let archive = workspace.publicResearch[item.id] {
                            let count = archive.channels.filter(\.verified).count
                            Text(count == 0 ? "Telegram не найден" : "Telegram · \(count)")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        } else { Text(item.catalogSegment).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                    }.padding(.vertical, 4).tag(item.id).accessibilityIdentifier("company-\(item.id)")
                }
            }.listStyle(.sidebar).padding(.horizontal, -12)
            Button("О приложении") { workspace.showAbout = true }.buttonStyle(.plain).font(.caption).foregroundStyle(.secondary).padding(.bottom, 10)
        }.padding(.horizontal, 12)
    }

    private func companyView(_ company: CompanyDossier) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if tab == .board {
                HStack(spacing: 24) {
                    Text(company.name).font(.system(size: 15, weight: .semibold)).lineLimit(1)
                    Spacer(minLength: 12)
                    companySections.frame(maxWidth: 570)
                }.padding(.horizontal, 20).padding(.vertical, 12)
                if let error = boardError ?? workspace.persistenceError { Text(error).font(.caption).foregroundStyle(.orange).padding(.horizontal, 20) }
            } else {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(company.name).font(.system(size: 27, weight: .semibold))
                        Text(company.discovery?.product ?? company.subtitle).font(.callout).foregroundStyle(.secondary).lineLimit(2)
                    }
                    Spacer()
                    if let url = safeURL(company.website) { Link("Сайт компании", destination: url) }
                    Button("Заметка") { showNote = true }.popover(isPresented: $showNote) { note(company) }
                }
                HStack {
                    companySections.frame(width: 650)
                    Spacer()
                    if sources.refreshing { ProgressView().controlSize(.small) }
                }
                if let error = workspace.persistenceError ?? sources.storageError ?? telemetr.errorMessage {
                    Text(error).font(.caption).foregroundStyle(.orange)
                }
                if let error = boardError { Text(error).font(.caption).foregroundStyle(.orange) }
                if let error = workspace.researchErrors.first { Text(error).font(.caption).foregroundStyle(.orange) }
            }.padding(20)
            }
            Divider()
            if tab == .materials { materialsView }
            else if tab == .hiring {
                HiringHistoryView(research: hiringResearch, start: historyStart, end: historyEnd, onOpenMaterial: openMaterial, onAddToBoard: { addToBoard(kind: .source, referenceID: $0.materialID, title: $0.title) })
                    .id(company.id)
            } else if tab == .board {
                BoardWorkspaceView(company: company, materials: research.materials, contacts: research.linkedPeople + research.publishedContacts, onOpenMaterial: openMaterial, onOpenContact: { id in tab = .contacts; query = ""; selectedContactID = id }).id(company.id)
            } else { contactsView }
            if tab != .board {
            Divider()
            HStack {
                Text("Сохранённые результаты на этом Mac").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !research.collectionNotes.isEmpty {
                    Menu("Ограничения сбора") { ForEach(research.collectionNotes, id: \.self) { Text($0) } }.menuStyle(.borderlessButton).fixedSize()
                }
            }.padding(.horizontal, 20).padding(.vertical, 8)
            }
        }.background(Color(nsColor: .textBackgroundColor))
    }

    private var companySections: some View {
        Picker("Раздел компании", selection: $tab) {
            Text("Материалы · \(research.fullMaterials.count)").tag(ResearchTab.materials)
            Text("Найм · \(hiringResearch.publications.count)").tag(ResearchTab.hiring)
            Text("Контакты · \(research.linkedPeople.count + research.publishedContacts.count)").tag(ResearchTab.contacts)
            Text("Разбор").tag(ResearchTab.board)
        }.pickerStyle(.segmented).labelsHidden().accessibilityIdentifier("research-tabs")
    }

    private var materialsView: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Полнота текста", selection: $showFragments) {
                    Text("Тексты · \(research.fullMaterials.count)").tag(false)
                    Text("Фрагменты поиска · \(research.fragments.count)").tag(true)
                }.pickerStyle(.segmented).labelsHidden().frame(width: 350).accessibilityIdentifier("material-completeness")
                Spacer()
                Text(showFragments ? "Поисковая выдача; полный текст не загружен" : "Можно читать здесь, без входа в Telemetr").font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 20).padding(.top, 12)
            HStack(spacing: 10) {
                TextField("Поиск по текстам и заголовкам", text: $query).textFieldStyle(.roundedBorder).accessibilityIdentifier("materials-search")
                Picker("Тип материала", selection: $kind) {
                    ForEach(ResearchMaterialKind.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }.labelsHidden().frame(width: 160).accessibilityIdentifier("materials-kind")
                    .help("Вакансии и акции выделены по словам в тексте. Категория не подтверждает актуальность или авторство.")
                Picker("Источник", selection: $provider) {
                    Text("Все источники").tag("Все источники")
                    ForEach(providers, id: \.self) { Text($0).tag($0) }
                }.labelsHidden().frame(width: 150)
                Toggle("Избранное", isOn: $onlySaved).toggleStyle(.checkbox)
            }.padding(.horizontal, 20).padding(.vertical, 12)
            HSplitView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Материалы: \(visibleMaterials.count)").font(.caption).foregroundStyle(.secondary).padding(12).accessibilityIdentifier("materials-count")
                    List(selection: $selectedMaterialID) {
                        ForEach(visibleMaterials) { item in materialRow(item).tag(item.id) }
                    }.listStyle(.inset)
                    if visibleMaterials.isEmpty { Text("По этому фильтру материалов нет").foregroundStyle(.secondary).padding() }
                }.frame(minWidth: 270, idealWidth: 345, maxWidth: 440)
                if let material { materialDetail(material).frame(minWidth: 330, maxWidth: .infinity) }
                else { ContentUnavailableView("Нет выбранного текста", systemImage: "doc.text", description: Text(research.fullMaterials.isEmpty && !showFragments ? "Полные тексты ещё не сохранены. Результаты поисков доступны во вкладке «Фрагменты поиска»." : "Выбери запись слева или измени фильтр.")).frame(maxWidth: .infinity) }
            }
        }
    }

    private func materialRow(_ item: ResearchMaterial) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.providers.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if isSaved(item) { Image(systemName: "star.fill").font(.caption).foregroundStyle(.orange) }
            }
            Text(item.title).font(.body.weight(.medium)).lineLimit(2)
            Text(item.text.isEmpty ? "Текст не получен; доступна ссылка" : item.text).font(.callout).foregroundStyle(.secondary).lineLimit(3)
            HStack { Text(item.kind.rawValue); Spacer(); Text(item.publishedAt?.formatted(date: .numeric, time: .omitted) ?? "Дата неизвестна") }.font(.caption2).foregroundStyle(.secondary)
        }.padding(.vertical, 7).accessibilityElement(children: .combine)
    }

    private func materialDetail(_ item: ResearchMaterial) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(item.title).font(.title2.weight(.semibold)).textSelection(.enabled)
                if let author = item.author { Text("Канал: " + author).font(.callout).textSelection(.enabled) }
                HStack {
                    Text(item.providers.joined(separator: " · ")).lineLimit(1)
                    Text(item.publishedAt.map { "Опубликовано " + $0.formatted(date: .abbreviated, time: .omitted) } ?? item.dateLabel)
                    Spacer()
                    Button(isSaved(item) ? "В избранном" : "В избранное", systemImage: isSaved(item) ? "star.fill" : "star") { toggleSaved(item) }
                        .disabled(workspace.editingDisabled).accessibilityIdentifier("material-save")
                }.font(.caption)
                Button("В разбор", systemImage: "plus.rectangle.on.rectangle") { addToBoard(kind: .source, referenceID: item.id, title: item.title) }
                Text(item.quality).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("material-quality")
                if item.originalAvailable, let url = safeURL(item.url) {
                    Link("Оригинал ↗", destination: url).accessibilityIdentifier("material-open")
                }
                Divider()
                Text(item.text.isEmpty ? "Сервис сохранил ссылку без текста. Содержимое можно прочитать в оригинале." : item.text)
                    .font(.system(size: 14)).lineSpacing(5).textSelection(.enabled)
                if item.evidence.count > 1 {
                    DisclosureGroup("Другие сохранённые фрагменты · \(item.evidence.count - 1)") {
                        ForEach(item.evidence.filter { $0.excerpt != item.text }) { evidence in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(evidence.provenance?.provider ?? evidence.kind).font(.caption.weight(.semibold))
                                Text(evidence.excerpt).font(.callout).textSelection(.enabled)
                                Text(evidence.dateLabel).font(.caption).foregroundStyle(.secondary)
                                Divider()
                            }.padding(.top, 8)
                        }
                    }
                }
                DisclosureGroup("Как найдено") {
                    Text(item.relationship).font(.caption).foregroundStyle(.secondary)
                    if !item.originalAvailable, let url = safeURL(item.url) { Link("Поисковая страница источника ↗", destination: url).font(.caption) }
                    ForEach(item.evidence) { evidence in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(evidence.provenance?.provider ?? evidence.kind)
                            if let provenance = evidence.provenance { Text("Запрос: " + provenance.query); Text("Получено: " + provenance.retrievedAt) }
                        }.font(.caption).foregroundStyle(.secondary).padding(.top, 6).textSelection(.enabled)
                    }
                }
            }.padding(22).frame(maxWidth: .infinity, alignment: .leading)
        }.accessibilityIdentifier("material-detail")
    }

    private var contactsView: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let archive = research.publicResearch {
                VStack(alignment: .leading, spacing: 6) {
                    Text(research.cooperationContacts.isEmpty ? "Контакт для сотрудничества в просмотренных источниках не найден" : "Для сотрудничества · \(research.cooperationContacts.count)").font(.headline)
                    Text("Telegram компании: \(archive.channels.filter(\.verified).count) · Прочитано страниц: \(archive.checks.filter { $0.status.hasPrefix("read") }.count)").font(.caption).foregroundStyle(.secondary)
                }.padding(.horizontal, 20).padding(.top, 14).accessibilityIdentifier("school-contact-coverage")
            }
            HStack {
                TextField("Имя, роль или адрес", text: $query).textFieldStyle(.roundedBorder).accessibilityIdentifier("contacts-search")
                Toggle("Другие совпадения LinkedIn · \(research.otherPeople.count)", isOn: $showOtherPeople).toggleStyle(.checkbox).accessibilityIdentifier("contacts-show-others")
                Toggle("Непроверенные адреса · \(research.addresses.filter { $0.affiliation != .published }.count)", isOn: $showUnverifiedAddresses).toggleStyle(.checkbox)
            }.padding(.horizontal, 20).padding(.vertical, 12)
            Text("Профили с указанием компании: \(research.linkedPeople.count) · Опубликованные каналы и контакты: \(research.publishedContacts.count)")
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 20).padding(.bottom, 12).accessibilityIdentifier("contacts-count")
            HSplitView {
                List(selection: $selectedContactID) {
                    ForEach([ContactPurpose.cooperation, .hiring, .general, .support, .enrollment, .channel, .chat, .bot, .unknown], id: \.self) { purpose in
                        let items = primaryContacts.filter { $0.affiliation == .published && $0.purpose == purpose }
                        if !items.isEmpty { Section(purpose.label) { ForEach(items) { contactRow($0).tag($0.id) } } }
                    }
                    if !primaryContacts.filter({ $0.kind == .person }).isEmpty {
                        Section("Люди · компания указана в профиле") {
                            ForEach(primaryContacts.filter { $0.kind == .person }) { contactRow($0).tag($0.id) }
                        }
                    }
                    if showUnverifiedAddresses {
                        Section("Адреса из материалов · принадлежность не проверена") {
                            ForEach(primaryContacts.filter { $0.kind != .person && $0.affiliation != .published }) { contactRow($0).tag($0.id) }
                        }
                    }
                    if showOtherPeople { Section("Не установлена связь с компанией") { ForEach(otherContacts) { contactRow($0).tag($0.id) } } }
                    if primaryContacts.isEmpty && (!showOtherPeople || otherContacts.isEmpty) {
                        Text(!query.isEmpty ? "По этому фильтру контактов нет." : "Подтверждённых контактов в сохранённых источниках пока нет.").font(.callout).foregroundStyle(.secondary)
                    }
                }.listStyle(.inset).frame(minWidth: 270, idealWidth: 345, maxWidth: 440)
                if let contact { contactDetail(contact).frame(minWidth: 330, maxWidth: .infinity) }
                else { ContentUnavailableView("Выбери контакт", systemImage: "person.crop.circle", description: Text("Здесь будут роль, ссылка и материал, из которого взят контакт.")).frame(maxWidth: .infinity) }
            }
        }
        .onChange(of: showOtherPeople) { _, enabled in if !enabled && contact?.affiliation != .profileCompany && contact?.kind == .person { selectedContactID = primaryContacts.first?.id } }
        .onChange(of: showUnverifiedAddresses) { _, _ in if contact == nil { selectedContactID = primaryContacts.first?.id } }
        .onChange(of: query) { _, _ in if let contact, !contactMatches(contact) { selectedContactID = primaryContacts.first?.id ?? (showOtherPeople ? otherContacts.first?.id : nil) } }
    }

    private func contactRow(_ item: ResearchContact) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(item.name).font(.body.weight(.medium)).lineLimit(2)
            Text(item.role.isEmpty ? "Роль не указана" : item.role).font(.callout).foregroundStyle(.secondary).lineLimit(3)
            Text(item.kind.label + " · " + item.affiliation.rawValue).font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 7).accessibilityElement(children: .combine)
    }

    private func contactDetail(_ item: ResearchContact) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(item.name).font(.title2.weight(.semibold)).textSelection(.enabled)
                Text(item.role.isEmpty ? "Роль не указана" : item.role).font(.body).textSelection(.enabled)
                Text(item.affiliation.rawValue).font(.callout.weight(.medium)).foregroundStyle(.secondary)
                Text(item.explanation).font(.callout).foregroundStyle(.secondary)
                if !item.sourceQuote.isEmpty {
                    Text(item.affiliation == .published ? "Как контакт подписан в источнике" : "Фрагмент с адресом").font(.headline)
                    Text(item.sourceQuote).font(.body).textSelection(.enabled).padding(12).frame(maxWidth: .infinity, alignment: .leading).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
                }
                if item.affiliation == .published || (item.kind == .person && item.affiliation == .profileCompany) {
                    Button("В разбор", systemImage: "plus.rectangle.on.rectangle") { addToBoard(kind: .contact, referenceID: item.id, title: item.name) }
                }
                HStack {
                    if let url = item.actionURL { Link(item.kind.openActionTitle, destination: url) }
                    Button("Скопировать " + (item.kind == .person ? "ссылку" : "адрес")) {
                        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(item.copyAddress, forType: .string)
                    }
                }
                DisclosureGroup("Как найдено · " + item.retrievedAt.formatted(date: .abbreviated, time: .omitted)) {
                    Text(item.address).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    if !item.query.isEmpty { Text("Поиск: " + item.query).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                }
                if item.kind == .person {
                    Divider()
                    Text("Кем указан в LinkedIn").font(.headline)
                    ForEach(Array(item.positions.enumerated()), id: \.offset) { _, position in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(position.title + " · " + position.companyName)
                            if let url = safeURL(position.companyURL) { Link("Страница работодателя из профиля", destination: url).font(.caption) }
                        }.textSelection(.enabled)
                    }
                }
                Divider()
                ForEach(item.sourceURLs, id: \.self) { source in
                    if let url = safeURL(source) { Link("Источник: " + (url.host ?? "") + url.path, destination: url).font(.caption) }
                }
                Text(item.kind == .person ? "Найденные публикации человека" : "Откуда взят адрес").font(.headline)
                let materials = research.materials.filter { item.materialIDs.contains($0.id) }
                if materials.isEmpty { Text(item.kind == .person ? "Публикации этого профиля в сохранённых данных не получены." : "Нет сохранённого текста источника.").foregroundStyle(.secondary) }
                ForEach(materials) { material in
                    Button {
                        query = ""; kind = .all; provider = "Все источники"; onlySaved = false
                        showFragments = !material.hasFullText
                        selectedMaterialID = material.id; tab = .materials
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(material.title).font(.body.weight(.medium))
                            let excerpt = ([material.text] + material.evidence.map(\.excerpt)).first { $0.localizedCaseInsensitiveContains(item.address) } ?? material.text
                            Text(excerpt.isEmpty ? (material.url ?? "") : contextQuote(excerpt, address: item.address)).font(.callout).foregroundStyle(.secondary).lineLimit(5)
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    }.buttonStyle(.bordered)
                }
            }.padding(22).frame(maxWidth: .infinity, alignment: .leading)
        }.accessibilityIdentifier("contact-detail")
    }

    private func rebuild() {
        guard let company else { research = CompanyResearch(); cachedHiring = nil; return }
        research = CompanyResearch.build(company: company, evidence: workspace.evidence(for: company), sourceRuns: sources.runs, telegramRuns: telemetr.runs, publicResearch: workspace.publicResearch[company.id])
        research.mergeSourceHistory(companyID: company.id, archives: workspace.sourceHistory)
        cachedHiring = HiringResearch.build(companyID: company.id, materials: research.materials, archives: workspace.sourceHistory, start: historyStart, end: historyEnd)
        if selectedMaterialID == nil || !research.materials.contains(where: { $0.id == selectedMaterialID }) { selectedMaterialID = visibleMaterials.first?.id }
        if selectedContactID == nil || !primaryContacts.contains(where: { $0.id == selectedContactID }) { selectedContactID = research.cooperationContacts.first?.id ?? primaryContacts.first?.id }
    }
    private func openMaterial(_ id: String) {
        guard let item = research.materials.first(where: { $0.id == id }) else { return }
        query = ""; kind = .all; provider = "Все источники"; onlySaved = false
        showFragments = !item.hasFullText; selectedMaterialID = id; tab = .materials
    }
    private func addToBoard(kind: BoardNodeKind, referenceID: String, title: String) {
        guard let company else { return }
        do {
            let store = BoardDocumentStore()
            let contacts = research.linkedPeople + research.publishedContacts
            try store.updateReferences(companyID: company.id, title: company.name, sourceIDs: Set(research.materials.map(\.id)), contactIDs: Set(contacts.filter { $0.affiliation == .published || ($0.kind == .person && $0.affiliation == .profileCompany) }.map(\.id)))
            let state = try store.load(companyID: company.id, title: company.name)
            if !state.document.nodes.contains(where: { $0.kind == kind && $0.referenceId == referenceID }) {
                let count = state.document.nodes.count
                let node = BoardNode(kind: kind, title: title, referenceId: referenceID, position: .init(x: Double(count % 3) * 330, y: Double(count / 3) * 240))
                _ = try store.apply(.init(documentId: state.document.id, baseRevision: state.document.revision, actor: .human, operations: [.createNode(node)]), companyID: company.id, title: company.name)
            }
            boardError = nil; tab = .board
        } catch { boardError = error.localizedDescription }
    }
    private func contactMatches(_ item: ResearchContact) -> Bool { query.isEmpty || item.searchText.localizedCaseInsensitiveContains(query) }
    private func isSaved(_ item: ResearchMaterial) -> Bool {
        guard let company else { return false }
        let ids = workspace.review(for: company).favoriteIDs
        return item.evidence.contains { ids.contains($0.id) }
    }
    private func toggleSaved(_ item: ResearchMaterial) {
        guard let company else { return }
        workspace.update(company) { $0.toggleFavorite(evidenceIDs: item.evidence.map(\.id)) }
    }
    private func safeURL(_ raw: String?) -> URL? {
        guard let raw, let url = URL(string: raw), ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil else { return nil }
        return url
    }
    private func contextQuote(_ text: String, address: String) -> String {
        var variants = [address, address.replacingOccurrences(of: "mailto:", with: "")]
        if address.contains("t.me/"), let username = URL(string: address)?.path.split(separator: "/").first { variants.append("@" + username) }
        guard let range = variants.compactMap({ text.range(of: $0, options: .caseInsensitive) }).first else { return String(text.prefix(260)) }
        let start = text.index(range.lowerBound, offsetBy: -85, limitedBy: text.startIndex) ?? text.startIndex
        let end = text.index(range.upperBound, offsetBy: 120, limitedBy: text.endIndex) ?? text.endIndex
        return (start == text.startIndex ? "" : "…") + String(text[start..<end])
    }
    private func note(_ company: CompanyDossier) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Заметка · " + company.name).font(.headline)
            TextEditor(text: Binding(get: { workspace.review(for: company).note }, set: { value in workspace.update(company) { $0.note = value } }))
                .frame(width: 430, height: 200).disabled(workspace.editingDisabled)
            Text("Сохраняется на этом Mac").font(.caption).foregroundStyle(.secondary)
        }.padding(20)
    }
    private var about: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("TG Рассылщик").font(.title2.weight(.semibold))
            Text("Материалы и контакты по \(companies.count) компаниям")
            Text("Карточки объединяют сохранённые результаты Google, Яндекса, Telegram, LinkedIn и VK. Поисковая выдача, полные тексты и сведения из профилей подписаны отдельно.").foregroundStyle(.secondary)
            Text("Данные хранятся локально. Сбор Telegram через браузер выполняется во время работы с Codex.").foregroundStyle(.secondary)
            Text("Версия 0.11.0").font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("Закрыть") { workspace.showAbout = false } }
        }.padding(26).frame(width: 480)
    }
}
