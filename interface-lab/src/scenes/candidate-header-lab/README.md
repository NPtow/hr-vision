# Candidate header comparison — 21 October 2026

Three interactive proposals for the employer's candidate review. These are isolated Storybook scenes; the production `AgencyProduct` entry does not import them.

## Decision to review

1. **One line (recommended):** candidate name, a semantic “Резюме” action, and the existing “Ответить” action. Clicking résumé opens a centered preview. Download belongs to the document toolbar. No separate attachment row, filename, size, or redundant “Открыть” button.
2. **Inside the video:** identity and résumé control sit above the video image; “Ответить” sits below the player. This prioritizes media, at the cost of placing identity on variable visual content.
3. **Beside the video:** the same résumé action replaces the transcript panel with a reading panel. The video stays available. A zoom action opens the centered preview when more reading space is needed. Closing returns to the transcript.

“Как сейчас” toggles a reconstruction of the supplied screenshot's header for comparison. It is not a connection to live candidate data.

## Research and reuse

- [Apple Quick Look](https://support.apple.com/guide/mac-help/preview-a-file-mh14119/mac): fast full-size preview without navigating away. This informed option 1. We adapt the interaction to the web; Apple does not prescribe our candidate-specific layout or dimensions.
- [Apple HIG — Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars): select frequent actions deliberately, keep related controls together, reduce competing chrome, preserve access at narrow widths. The official Markdown version was also consulted at `https://developer.apple.com/design/human-interface-guidelines/toolbars.md`.
- [Apple HIG — Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets): keep modal tasks scoped; consider a nonmodal panel for supplementary information used alongside the main task. This informed the alternative in option 3.
- Context7: searched `radix ui`, then read `/websites/radix-ui_primitives` for Dialog composition, accessible title, `Trigger asChild`, Overlay, Close, focus and animation. [Original Radix documentation](https://www.radix-ui.com/primitives/docs/components/dialog).

The implementation reuses the installed `radix-ui` package (1.6.7 range in the project), `lucide-react`, and the existing HR Vision brand asset. No dependencies were added. Dialog provides focus trapping, Escape dismissal, and focus restoration; the prototype supplies the layout and CSS transitions. Reduced motion removes the transitions.

## Demo boundary

The name is from the user's screenshot. Résumé, transcript and video placeholder are **illustrative**, not claims about that person's experience. The file `public/candidate-header-demo/resume.pdf` is a real PDF generated from `ResumeDocument` for exercising Download. The visible document is an HTML facsimile of that same demo content.

Playback is simulated. Comments and decisions exist only in React state. No production API, candidate messages, hiring decisions, recordings, or accounts are accessed or mutated.

## Run and inspect

From `interface-lab`:

```sh
HR_BUILD_TARGET=mockups npx storybook dev -p 6023 --host 127.0.0.1 --no-open --disable-telemetry
```

Verified IDs from the running Storybook `index.json`:

- `hr-vision-candidate-header--inline`
- `hr-vision-candidate-header--overlay`
- `hr-vision-candidate-header--side`

Use `/iframe.html?id=<id>&viewMode=story`. All three variants are also switchable on each page. The standalone résumé print view adds `&document=1`.

UI check and screenshots: `output/playwright/candidate-header-20261021/` in the worktree. Checks cover desktop and mobile widths, popup close paths and focus return, expansion, actual PDF download, side panel opening at the top, locally simulated comments/response, reduced motion, and absence of `/api/hr` requests.

## Decision alternatives — 21 October

The selected video header is implemented separately in the real employer components. These three decision stories are still isolated proposals:

- `hr-vision-candidate-decision--direct`: explicit invitation/refusal actions with an expanding inline feedback form.
- `hr-vision-candidate-decision--dock`: a floating dock morphs into the form and then a receipt.
- `hr-vision-candidate-decision--beside`: the right panel switches between transcript and decision, retaining the draft.

All three require a refusal reason, allow optional missing-skills/future-candidates feedback, and show the reason in the local receipt. Invitations use illustrative slots and require a time before confirmation. No API writes or messages occur. The lab reset clears local React state. Motion uses the installed `motion/react` package, spring layout transitions, short content fades, keyboard focus restoration and reduced-motion support.

Context7 lookup: `/websites/motion_dev`, React accessibility / useReducedMotion / AnimatePresence documentation. https://motion.dev/docs/react-accessibility

Browser checks and screenshots: `output/playwright/candidate-decision-20261021/`.

## Коррекция оформления и поиск по интервью · 21.10.2026

- Согласованная кнопка «Резюме» остаётся в шапке видео: тот же компонент, фон, отступы, радиус и толщина значка. Размеры проверяются сравнением computed styles с историей Overlay.
- Варианты решения используют общую типографику продукта, без крупных служебных заголовков и отдельной фиолетовой палитры. Меняются только место и механика решения: под видео, в плавающем пульте, справа.
- Поле вопроса закреплено под расшифровкой. Помощник предлагает три начальных вопроса, сохраняет историю при переключении вкладок, показывает исходные цитаты. Нажатие на таймкод перематывает видео. Отдельно проверяется отсутствие ответа; это не означает отсутствие навыка у человека.
- Внизу оболочки макета явно выбран режим: «Готовые примеры» либо реальный запрос Jev. Примеры не выдают себя за работающий inference. Произвольный вопрос в режиме примеров не получает придуманного ответа.
- Комментарии к записи остаются отдельной вкладкой.

### Jev: реализованное подключение и точная граница

Локальный `scripts/candidate-search-demo.py` раздаёт статическую Storybook-сборку и принимает `/__demo/search`. Ключ читается только сервером из настроенного `~/.config/typesafe/jev.env`. Сервер привязан к loopback, проверяет Host/Origin, принимает только вопрос, использует фиксированную синтетическую расшифровку. В production entry и API приложения он не включён.

Модель: `typesafe-ai/jev-1.14.0`, TypeSafe-compatible endpoint AI Gateway. Один state содержит вопрос и всю короткую демо-расшифровку; отдельный Score оценивает релевантность каждого ответа кандидата. Вопросы рекрутера используются как контекст, но не выдаются за ответ кандидата. Приложение сортирует фрагменты; цитаты берёт строго из исходного массива, а не из текста модели. Порог демонстрационный и ещё не откалиброван на реальных русских интервью. Для длинных интервью потребуются ограниченные по размеру эпизоды с полным покрытием источника, авторизация работодателя и серверная загрузка только разрешённой записи.

Реальные запросы к версиям 1.14.0 и 1.13.0 получили HTTP 403: `Free tier users do not have access to this model. Upgrade to paid credits ...`. Ключ существует; платный доступ к этим моделям не открыт. Покупки и изменения биллинга не выполнялись. Это не проверка качества Jev. При отказе провайдера интерфейс сообщает о недоступности поиска, а не об отсутствии факта в интервью.

Короткая фраза перед цитатами — интерфейсный текст, а не сгенерированное заключение. Генерация содержательного ответа чата должна быть отдельным шагом поверх выбранных цитат; Jev решает задачу отбора.

Запуск:

```sh
HR_BUILD_TARGET=mockups npm run build
python3 scripts/candidate-search-demo.py --port 6024
```

### Основания решений

- Apple Design: согласованная иерархия текста, выравнивание символов, пространственно понятный возврат, восстановление фокуса, reduced motion. Конкретные веб-радиусы и отступы — решения этого проекта, а не «точные размеры Apple».
- [TypeSafe Score](https://docs.typesafe.ai/primitives/score): описательные уровни релевантности, вероятности, несколько независимых вопросов по общему state.
- [TypeSafe models](https://docs.typesafe.ai/models): ограничения контекста и необходимость отдельной проверки русского языка.
- [Vercel TypeSafe-compatible HTTP](https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev): серверное подключение через Gateway.
- [Motion accessibility](https://motion.dev/docs/react-accessibility): системная настройка reduced motion.

Проверки: `output/playwright/candidate-decision-20261021/` содержит снимки и результаты прохождения трёх вариантов, мобильного окна, резюме и поиска. Прототипы не отправляют работодателю/кандидату решений и не публикуют комментарии в реальный кабинет.
