import Foundation

public enum SeedCatalog {
    public static let companies: [CompanyDossier] = realCompanies + [changeDemo, unknownDemo, conflictDemo]

    private static let parameterDefinitions: [(String, String, String)] = [
        ("sales_model", "Модель B2C / B2B", "Устройство продаж"),
        ("product_type", "Учебный продукт", "Устройство продаж"),
        ("sales_path", "Путь продажи", "Устройство продаж"),
        ("sales_team_size", "Масштаб команды продаж", "Устройство продаж"),
        ("revenue_trend", "Динамика выручки", "Изменение бизнеса"),
        ("product_launch", "Запуск продуктов", "Изменение бизнеса"),
        ("expansion", "Новые сегменты и географии", "Изменение бизнеса"),
        ("contraction", "Сокращение направлений", "Изменение бизнеса"),
        ("lead_shortage", "Заявленный дефицит лидов", "Поток и конверсия"),
        ("acquisition_change", "Изменение каналов привлечения", "Поток и конверсия"),
        ("conversion_problem", "Заявленная проблема конверсии", "Поток и конверсия"),
        ("processing_overload", "Перегрузка обработкой заявок", "Поток и конверсия"),
        ("sales_hiring", "Набор продавцов", "Люди коммерческой функции"),
        ("leadgen_hiring", "Набор лидогенерации", "Люди коммерческой функции"),
        ("sales_manager_hiring", "Набор менеджера отдела продаж", "Люди коммерческой функции"),
        ("sales_leader_change", "Смена ответственного за продажи", "Люди коммерческой функции"),
        ("change_initiative", "Заявленная инициатива изменений", "Готовность менять работу"),
        ("change_action", "Предпринимаемое действие", "Готовность менять работу"),
        ("external_partner", "Опыт внешнего исполнителя", "Готовность менять работу"),
        ("deadline", "Срок решения", "Готовность менять работу"),
        ("profit_trend", "Динамика прибыли", "Финансовые ограничения"),
        ("debt_load", "Долговая нагрузка", "Финансовые ограничения"),
        ("overdue_debt", "Подтверждённая просрочка", "Финансовые ограничения"),
        ("cost_cutting", "Меры экономии", "Финансовые ограничения")
    ]

    static var emptyParameters: [SignalParameter] {
        parameterDefinitions.map { id, title, group in
            SignalParameter(id: id, title: title, group: group, value: "Нет проверенных данных", state: .unknown, evidenceIDs: [])
        }
    }

    static var emptyHypotheses: [Hypothesis] { [
        Hypothesis(id: "lead_flow", title: "Недостаточный поток лидов", summary: "Не установлено, хватает ли компании подходящих обращений.", alternative: "Потока может быть достаточно; ограничение может находиться в обработке заявок.", question: "Сейчас важнее увеличить поток подходящих заявок или лучше обработать существующий?", parameterIDs: ["lead_shortage", "acquisition_change", "leadgen_hiring"], state: .unknown),
        Hypothesis(id: "conversion", title: "Потери на конверсии", summary: "Нет подтверждения, что обращения теряются на пути к покупке.", alternative: "Причина может быть в качестве трафика, сезонности или изменении продукта.", question: "На каком шаге от первого обращения до оплаты вы видите главную потерю?", parameterIDs: ["conversion_problem", "sales_path", "revenue_trend"], state: .unknown),
        Hypothesis(id: "capacity", title: "Нехватка мощности обработки", summary: "Не установлено, превышает ли поток заявок возможности команды.", alternative: "Проблема может быть в процессе и распределении заявок, а не в числе людей.", question: "Бывает ли, что подходящая заявка ждёт ответа дольше, чем хотелось бы?", parameterIDs: ["processing_overload", "sales_team_size", "sales_hiring"], state: .unknown),
        Hypothesis(id: "management", title: "Проблема управления продажами", summary: "Нет оснований утверждать, что результат ограничивает управление продажами.", alternative: "Смена руководителя может быть плановой или связанной с новым направлением.", question: "Кто сейчас отвечает за результат продаж и что этому человеку мешает?", parameterIDs: ["sales_manager_hiring", "sales_leader_change", "conversion_problem"], state: .unknown),
        Hypothesis(id: "transformation", title: "Перестройка коммерческой функции", summary: "Не хватает связанных фактов, чтобы говорить о перестройке продаж.", alternative: "Набор сотрудников может означать замену людей, сезонный набор или новый продукт. Заявленная инициатива изменений поможет уточнить объяснение.", question: "Вы усиливаете продажи под рост потока, заменяете людей или меняете саму работу с заявками?", parameterIDs: ["revenue_trend", "sales_hiring"], state: .unknown),
        Hypothesis(id: "resource_limit", title: "Ограничение ресурсов", summary: "Финансовые ограничения и доступность бюджета пока не установлены.", alternative: "Номинальный долг может быть плановым; снижение выручки не доказывает нехватку средств.", question: "Какие ограничения по бюджету и сроку нужно учесть, чтобы решение было реалистичным?", parameterIDs: ["profit_trend", "debt_load", "overdue_debt", "cost_cutting"], state: .unknown)
    ] }

    private static let realCompanies: [CompanyDossier] = [
        real(
            id: "brunoyam", name: "Бруноям", initials: "Б", website: "https://brunoyam.com/",
            subtitle: "IT, дизайн, маркетинг и управление",
            product: "Курсы по IT, дизайну, маркетингу и управлению; корпоративное обучение",
            sources: [
                ("О школе и команде", "Школа предлагает курсы по IT, дизайну, маркетингу и управлению. Есть отдельное корпоративное обучение.", "https://brunoyam.com/about"),
                ("Блог Бруноям", "Официальный раздел публикаций школы. Свежесть отдельных материалов и состав коммерческой команды пока не установлены.", "https://brunoyam.com/blog")
            ], model: "Индивидуальное и корпоративное обучение", salesPath: "На сайте есть консультационный путь"
        ),
        real(
            id: "purpleschool", name: "PurpleSchool", initials: "P", website: "https://purpleschool.ru/",
            subtitle: "Школа программирования",
            product: "Онлайн-обучение программированию, практика и тренажёры",
            sources: [
                ("Каталог PurpleSchool", "Авторская онлайн-школа программирования с практикой и тренажёрами. Наличие отдела продаж не установлено.", "https://purpleschool.ru/"),
                ("Блог PurpleSchool", "В официальном блоге указаны авторы и даты. Их роль в продажах школы требует отдельного подтверждения.", "https://purpleschool.ru/blog")
            ]
        ),
        real(
            id: "mds", name: "Moscow Digital School", initials: "M", website: "https://mosdigitals.ru/",
            subtitle: "Профессиональное и корпоративное образование",
            product: "Профессиональное образование, в том числе для юридической практики",
            sources: [
                ("Образовательные программы MDS", "Школа предлагает профессиональное и корпоративное образование. Границу бренда, работодателя и холдинга нужно уточнить.", "https://mosdigitals.ru/"),
                ("Кто создаёт MDS", "В материале школы описаны маркетинг, продажи, партнёрства и выпуск курсов. Упомянута принадлежность к холдингу.", "https://mosdigitals.ru/blog/kto-sozdaet-mds-ne-tolko-prepodavateli-a-lyudi-kotorye-prevrashhayut-obrazovanie-v-zhivuyu-sistemu")
            ], model: "Индивидуальное и корпоративное обучение"
        ),
        real(
            id: "changellenge", name: "Changellenge Education", initials: "C", website: "https://changellenge-education.com/",
            subtitle: "Аналитика и профессиональная переподготовка",
            product: "Обучение аналитике и профессиональная переподготовка",
            sources: [
                ("Об образовательной организации", "На официальном сайте представлены сведения об образовательной организации и учебные программы.", "https://changellenge-education.com/about"),
                ("Программа «Аналитик Кампус»", "На странице программы есть консультационная заявка. Учебный набор сам по себе не доказывает проблему выручки.", "https://changellenge-education.com/analytics-campus")
            ], salesPath: "Консультационная заявка по программе"
        ),
        real(
            id: "setters", name: "SETTERS Education", initials: "S", website: "https://setters.education/",
            subtitle: "Управление, маркетинг, бренд и ИИ",
            product: "Курсы, интенсивы и корпоративные программы",
            sources: [
                ("Курсы и интенсивы SETTERS", "Компания предлагает обучение управлению, маркетингу, бренду и ИИ, а также корпоративные программы.", "https://setters.education/"),
                ("Блог и корпоративные кейсы", "В блоге опубликованы материалы и корпоративные кейсы. Ситуация клиента из кейса не является фактом о самой школе.", "https://setters.education/blog/"),
                ("Бренд SETTERS Education", "Официальная страница указывает связь с другими организациями SETTERS. Принадлежность сотрудников требует проверки.", "https://setters.education/brand")
            ], model: "Индивидуальное и корпоративное обучение"
        ),
        real(
            id: "karpov", name: "karpov.courses", initials: "K", website: "https://karpov.courses/",
            subtitle: "Аналитика, Data Science и IT",
            product: "Обучение аналитике, Data Science и IT",
            sources: [
                ("Программы karpov.courses", "Школа предлагает профессиональное обучение аналитике, Data Science и IT. Размер внутренней команды не установлен.", "https://karpov.courses/"),
                ("Блог karpov.courses", "В блоге есть экспертный контент, программы и мероприятия. Приглашённый преподаватель не обязательно является сотрудником школы.", "https://blog.karpov.courses/"),
                ("Официальный Telegram", "Канал подтверждён ссылкой с главного сайта. Полного сбора и оценки публикаций в этом прототипе нет.", "https://t.me/KarpovCourses")
            ]
        ),
        real(
            id: "hexlet", name: "Хекслет", initials: "Х", website: "https://ru.hexlet.io/",
            subtitle: "Онлайн-обучение программированию",
            product: "Онлайн-обучение программированию",
            sources: [
                ("Школа Хекслет", "Онлайн-школа программирования. Связи школы, колледжа и других направлений требуют отдельного разбора.", "https://ru.hexlet.io/"),
                ("Блог Хекслета", "Лента содержит обновления продукта, технические материалы и истории выпускников. Карьерная история студента не означает найм самой школы.", "https://ru.hexlet.io/blog")
            ]
        ),
        real(
            id: "mathshub", name: "Mathshub", initials: "M", website: "https://maths-h.com/ru",
            subtitle: "Анализ данных и программирование",
            product: "Обучение анализу данных и программированию",
            sources: [
                ("Программы Mathshub", "Сайт представляет обучение анализу данных и программированию. Актуальность бизнеса нужно подтвердить свежим датированным материалом.", "https://maths-h.com/ru"),
                ("Блог Mathshub", "В блоге есть авторы с внешними аффилиациями и устаревшие промоматериалы. Старые биографии не подтверждают текущую роль.", "https://maths-h.com/ru/blog")
            ]
        ),
        real(
            id: "slurm", name: "Слёрм", initials: "С", website: "https://slurm.io/",
            subtitle: "Инженеры, разработчики и корпоративные команды",
            product: "Обучение инженеров, разработчиков и корпоративных команд",
            sources: [
                ("Образовательные программы Слёрм", "Компания проводит инженерное обучение и обучение корпоративных команд. Численность внутренней команды пока не проверена.", "https://slurm.io/"),
                ("Спецпроекты Слёрма", "Совместные образовательные проекты позволяют исследовать отношения школы, заказчика, партнёра и приглашённого спикера.", "https://slurm.io/special-projects")
            ], model: "Индивидуальное и корпоративное обучение"
        ),
        real(
            id: "sfeducation", name: "SF Education", initials: "SF", website: "https://sf.education/",
            subtitle: "Финансы, аналитика, менеджмент и IT",
            product: "Обучение финансам, аналитике, менеджменту и IT",
            sources: [
                ("О компании и основателях", "Официальная страница отдельно обозначает основателей и преподавателей. Размер штатной команды и актуальность ролей не установлены.", "https://sf.education/about-us"),
                ("Пример материала в блоге", "Авторский образовательный материал о резюме финансового директора. Тема статьи не является свидетельством вакансии в самой школе.", "https://blog.sf.education/kak-sostavit-rezyume-finansovogo-direktora-chtoby-vas-tochno-zametili/")
            ]
        )
    ]

    private static func real(id: String, name: String, initials: String, website: String, subtitle: String, product: String, sources: [(String, String, String)], model: String? = nil, salesPath: String? = nil) -> CompanyDossier {
        let evidence = sources.enumerated().map { offset, source in
            Evidence(id: "\(id)-source-\(offset + 1)", title: source.0, excerpt: source.1, url: source.2, dateLabel: "Проверено 06.10.2026 · дата публикации не установлена", kind: "Краткое описание первичного источника", isDemo: false)
        }
        var parameters = emptyParameters
        set(&parameters, "product_type", product, .supported, [evidence[0].id])
        if let model { set(&parameters, "sales_model", model, .supported, [evidence[0].id]) }
        if let salesPath {
            let sourceID = id == "changellenge" ? evidence[1].id : evidence[0].id
            set(&parameters, "sales_path", salesPath, .supported, [sourceID])
        }
        return CompanyDossier(id: id, name: name, subtitle: subtitle, website: website, initials: initials, isDemo: false, evidence: evidence, parameters: parameters, hypotheses: emptyHypotheses)
    }

    private static func set(_ parameters: inout [SignalParameter], _ id: String, _ value: String, _ state: FindingState, _ evidenceIDs: [String]) {
        guard let index = parameters.firstIndex(where: { $0.id == id }) else { return }
        parameters[index].value = value
        parameters[index].state = state
        parameters[index].evidenceIDs = evidenceIDs
    }

    private static func demoEvidence(_ id: String, _ title: String, _ excerpt: String, date: String) -> Evidence {
        Evidence(id: id, title: title, excerpt: excerpt, url: nil, dateLabel: date, kind: "Вымышленный учебный материал", isDemo: true)
    }

    private static var changeDemo: CompanyDossier {
        let evidence = [
            demoEvidence("demo-change-revenue", "Сопоставимая выручка снизилась", "Учебный сценарий: у одного и того же бизнеса выручка за сопоставимый период снизилась на 18%. Причина снижения не установлена.", date: "Учебные данные · отчёт за первое полугодие"),
            demoEvidence("demo-change-hiring", "Школа ищет менеджеров по продажам", "Учебный сценарий: в публичном сообщении школа ищет двух менеджеров по продажам. Не сказано, это расширение или замена ушедших сотрудников.", date: "Учебные данные · свежий пост школы")
        ]
        var parameters = emptyParameters
        set(&parameters, "revenue_trend", "Снижение на 18% за сопоставимый период", .supported, [evidence[0].id])
        set(&parameters, "sales_hiring", "Ищут двух менеджеров по продажам", .supported, [evidence[1].id])
        var hypotheses = emptyHypotheses
        let index = hypotheses.firstIndex { $0.id == "transformation" }!
        hypotheses[index].state = .supported
        hypotheses[index].summary = "Снижение выручки вместе с набором продавцов даёт повод проверить, перестраивает ли школа коммерческую функцию. Это гипотеза для разговора, не установленная боль."
        return CompanyDossier(id: "demo-change", name: "Школа «Контур»", subtitle: "Учебный сценарий · два связанных сигнала", website: nil, initials: "К", isDemo: true, evidence: evidence, parameters: parameters, hypotheses: hypotheses)
    }

    private static var unknownDemo: CompanyDossier {
        let evidence = [
            demoEvidence("demo-unknown-catalog", "Есть только каталог обучения", "Учебный сценарий: школа продаёт курсы аналитики. В доступных материалах нет сведений о выручке, команде, конверсии и нехватке лидов.", date: "Учебные данные · недатированный каталог")
        ]
        var parameters = emptyParameters
        set(&parameters, "product_type", "Курсы аналитики", .supported, [evidence[0].id])
        return CompanyDossier(id: "demo-unknown", name: "Школа «Маяк»", subtitle: "Учебный сценарий · недостаточно данных", website: nil, initials: "М", isDemo: true, evidence: evidence, parameters: parameters, hypotheses: emptyHypotheses)
    }

    private static var conflictDemo: CompanyDossier {
        let evidence = [
            demoEvidence("demo-conflict-report", "В отчёте указано снижение выручки", "Учебный сценарий: отчёт одного юридического лица показывает снижение выручки. Граница между этим юрлицом и образовательным брендом ещё не проверена.", date: "Учебные данные · годовой отчёт"),
            demoEvidence("demo-conflict-founder", "Основатель говорит о росте", "Учебный сценарий: основатель говорит, что выручка школы выросла. Период, база сравнения и состав бизнеса в сообщении не уточнены.", date: "Учебные данные · недавнее интервью"),
            demoEvidence("demo-conflict-hiring", "Объявлен набор продавцов", "Учебный сценарий: школа объявила набор менеджеров по продажам. Цель набора не указана.", date: "Учебные данные · пост школы")
        ]
        var parameters = emptyParameters
        set(&parameters, "revenue_trend", "Источники расходятся; нужно сверить периоды и юрлица", .conflict, [evidence[0].id, evidence[1].id])
        set(&parameters, "sales_hiring", "Есть публичный набор продавцов", .supported, [evidence[2].id])
        var hypotheses = emptyHypotheses
        let index = hypotheses.firstIndex { $0.id == "transformation" }!
        hypotheses[index].state = .conflict
        hypotheses[index].summary = "Набор продавцов установлен в рамках сценария, но направление изменения выручки неясно. Связь нельзя трактовать до сверки юрлица и периода."
        hypotheses[index].alternative = "Отчёт и слова основателя могут описывать разные юридические лица, периоды или направления бизнеса."
        hypotheses[index].question = "Эти данные относятся к одному бизнесу и сопоставимому периоду? Под какую задачу вы набираете продавцов?"
        return CompanyDossier(id: "demo-conflict", name: "Школа «Спектр»", subtitle: "Учебный сценарий · источники расходятся", website: nil, initials: "С", isDemo: true, evidence: evidence, parameters: parameters, hypotheses: hypotheses)
    }
}
