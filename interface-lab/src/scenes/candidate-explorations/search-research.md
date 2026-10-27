# Search variants — implementation notes

2026-10-13. Только локальная исследовательская сцена. State, lab controls и уведомление о симуляции принадлежат root shell. Данные передаются через неизменённый SearchProps. Нет запросов API, внешних отправок и самостоятельного изменения этапов работодателя.

## Десять разных компоновок

0. Focus: одна широкая детальная вакансия, вертикальная история произошедших событий справа, остальные предложения ниже.
1. Master/detail: постоянный список слева и переключаемый инспектор справа; на узком экране список становится горизонтальным.
2. Mosaic: карточная сетка со смещённой средней колонкой; выбранная карточка раскрывается на своём месте.
3. Ledger: компактный список с колонками условий и следующего шага, детали внутри строки.
4. Routes: горизонтальная история каждой вакансии; раскрываются условия и следующий шаг параллельно. Число событий не ограничено четырьмя; встречи могут повторяться.
5. Board: колонки только для существующих состояний вакансий. Пустых заранее заданных этапов нет. Кандидат не перемещает вакансии между этапами от имени работодателя.
6. Accordion: крупные нумерованные заголовки, внутри две колонки «контекст / решение».
7. Editorial: выразительный вводный заголовок, выбранная вакансия как основной материал, справа оглавление предложений.
8. Rail: горизонтальная лента со scroll-snap, рабочие стрелки, отдельный контекст выбранной вакансии под лентой.
9. Next actions: группы «Нужен ваш ответ», «Всё идёт своим чередом», «История решений».

## Переходы и контроль кандидата

new: accept / decline; applied: ожидание; proposed: confirm / reschedule; confirmed: join / openCalendar; completed: continue / decline; followup: ожидание; offer: просмотр подтверждения → acceptOffer или decline; accepted / declined: сохранённый результат. Действия вызывают переданный act, не меняют родительское состояние самостоятельно. Подтверждение оффера выполнено отдельным локальным шагом с условиями; кнопка возврата не отправляет ответа. Причины совпадения не выдумываются из description.

Во всех десяти вариантах ровно три controlled вкладки `Новые / В процессе / История` через SearchProps.filter/setFilter. `archived` имеет приоритет над stage и всегда попадает в историю; там активные действия скрыты. Accepted/declined также находятся в истории. При accept родитель переключает процесс и выбранную вакансию; навигационный индикатор перемещается к новой вкладке. Journey отображает исключительно Vacancy.history; неизвестные будущие шаги не создаются.

Все карточки открываются нативной кнопкой, действия не вложены в кнопки. Accordion сообщает aria-expanded/aria-controls. Вкладки поддерживают ArrowLeft/ArrowRight/Home/End и roving tabindex. LayoutGroup ограничивает общие идентификаторы каждой сценой. AnimatePresence сохраняет exit раскрывающихся областей. useReducedMotion отключает перемещения и изменение высоты; системная CSS-настройка убирает hover/transitions. Scroll rail имеет кнопочную альтернативу жесту.

## Context7 evidence

Фактически выполнено 13.10.2026:

```text
$ ~/.codex/skills/context7/c7 search 'motion react'
/websites/motion_dev_react  trust=9.7  snippets=314  Motion for React

$ ~/.codex/skills/context7/c7 docs /websites/motion_dev_react \
  'AnimatePresence layout useReducedMotion exit height auto accordion' 1900
### Accordion item with layout animation
Source: https://motion.dev/docs/react-layout-group
### Replace transform animations with opacity transitions
Source: https://motion.dev/docs/react-accessibility
### Namespace layoutId with LayoutGroup id prop
Source: https://motion.dev/docs/react-upgrade-guide
```

Использованы layout/position на карточках, LayoutGroup id, keyed AnimatePresence, reduced-motion hooks из motion/react 13.4.6, установленного в проекте. Не копировались примеры API из других версий. Прочитаны interface-lab/SKILL.md и current-lab.md; историческая Proteus-инфраструктура из reference не применялась к checkout HR Vision.

## Проверка

`npm run typecheck` прошёл 13.10.2026 после интеграции controlled filters и history. Интеграция в Storybook, визуальная проверка в 1120/390 и проверка родительских переходов выполняются root после подключения трёх новых файлов. Визуальная проверка не заявляется как выполненная до открытия реальной истории.

Финальная QA-поправка: compact Detail содержит нативное раскрываемое «О работе» с полным job.description перед кнопками решения. Архивный текст описывает прошлое событие остановки поиска и остаётся корректным после возобновления. Доступное имя Row использует тот же statusLabel, что видимая подпись. После поправок typecheck снова прошёл.
