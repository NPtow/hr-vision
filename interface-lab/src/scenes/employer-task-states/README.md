# Employer task states — prototype history and production QA

Initial prototype baseline: b2f7d1c691d56cac0fcf965b5283e2fc8f20c294 (2026-10-25). The production change preserves the later deployed 0caa6e49 baseline.
This is a local Storybook prototype. No service requests, real call, messages or candidate mutations.

## Scope

- Completed meeting, scheduled meeting and sent offer use the existing CandidateStageCard, EmployerShell, CandidateOfferForm and recording UI.
- Meeting connection is a local simulation; recordings are intentionally not substituted from other interviews.
- The next-step view is new and separate from the old dropdown. User confirmed: open after finishing a meeting AND from +; show a short last-meeting summary; offer meeting / offer / rejection actions.
- The prototype introduced onOpenNextStep; the approved production workspace now uses it too.
- Scheduling and rejection forms are local transition sketches, not connected product implementations.

## Routes

Fresh Storybook index verified on http://127.0.0.1:6036:
- hr-vision-employer-task-states--completed
- hr-vision-employer-task-states--scheduled
- hr-vision-employer-task-states--next-step
- hr-vision-employer-task-states--offer

Port 6036 serves the separately built mockup directory: python3 -m http.server 6036 --bind 127.0.0.1 --directory interface-lab/storybook-task-static.
Build mockups separately from the service: HR_BUILD_TARGET=mockups storybook build --output-dir storybook-task-static (working directory interface-lab).

## Existing offer semantics, not redesign decisions

- Draft -> sent -> accepted / declined.
- Editing sent immediately creates a new sent version; previous version becomes superseded.
- Accepted offer closes the vacancy and locks further actions.
- Active meeting blocks offer save/send.
- Production currently permits a further meeting after a sent offer; this conflicts with the desired final-stage model and remains a product discussion.
- Production cannot issue another offer to the same candidate after decline, although the old + menu still offers the action.
- Each offer version is currently a separate timeline stage.
- Mandatory employer FeedbackGate is disabled in production. No new mandatory feedback is added here.

## Validation

Typecheck and mockups build passed. Browser flows and screenshots are in output/playwright/employer-task-states/.
Verified desktop 1440x1000 and narrow 390x844, all four routes; note save/reset; + as a page; scheduling/confirmation; simulated completion -> next step; rescheduling preserves history; offer draft/send/edit/accept; rejection; no horizontal document overflow, browser runtime errors or API requests in those flows.

## Simplified upcoming meeting (2026-10-25)

User requested only date/time + connection action. Prototype now opts into compactUpcomingMeetings: pending/confirmed headings and timeline labels use «Предстоящая встреча», completed uses «Встреча». Duplicate person, notes, status text, consent checkbox and rescheduling action are removed from the upcoming card. Connection is still only a local simulation. Production call recording/consent was not changed. Verified the compact card at desktop/narrow widths, pending state and completion transition; see qa-upcoming-result.txt.

## Minimal next step (2026-10-25, subsequent feedback)

The latest user correction supersedes the initial summary request: only three buttons remain in next-step (schedule / offer / reject). No heading, summary, icons, descriptions or separate back button. Existing stage tabs provide return navigation.
# Варианты завершённой встречи · 25 октября

Три локальные композиции с записью в начале и материалами под ней:

- `hr-vision-employer-task-states--completed-journal`: комментарии и личная заметка в открытых колонках, фидбек ниже.
- `hr-vision-employer-task-states--completed-workbench`: одна область с вкладками «Комментарии / Фидбек / Мои заметки».
- `hr-vision-employer-task-states--completed-outcome`: сначала итог; комментарии и заметки раскрываются ниже.

Внешняя оболочка позволяет переключать варианты без потери введённых данных. `TaskScene` хранит материалы отдельно для каждого этапа до сброса/смены экрана лабы. Текст заметок, вердикт, обоснование и опубликованные локально комментарии сохраняются при переходе «Следующий шаг» → обратно к этапу. Закрытый процесс не позволяет начать новый шаг.

Использован существующий `CandidateInterviewPlayer`. Подключён **синтетический немой 20-секундный клип**, поверх него — условные участники без камеры. Это проверка работы плеера/таймкодов, не запись интервью и не источник вымышленных высказываний. Комментарии и данные примера вымышленные. Никаких API-вызовов, отправок, запуска камер или AI.

Контроль: typecheck, production Storybook build, 30 браузерных проверок в `output/playwright/employer-task-states/qa-completed-result.txt`, снимки 1440 и 390 px. Проверены таймкоды, добавление комментария, фидбек и заметки, возврат с следующего шага, сохранение черновика при смене вкладок, reduced motion, блокировка следующего шага после принятия оффера, отсутствие горизонтального переполнения и API-запросов.

Движение: выбранный сегмент перемещается на пружине без отскока; раскрытия открываются на месте и допускают немедленное обратное действие. Принципы: [Apple — Designing Fluid Interfaces](https://developer.apple.com/videos/play/wwdc2018/803/). Численные настройки и CSS — адаптация для веба, не спецификация Apple.


## Approved production composition

The latest instruction supersedes all three completed-meeting variants: use the existing interview UI unchanged, backed by that meeting's own materials. `CandidateStageWorkspace` now composes the common player with meeting-scoped comments. Stored notes, documents and feedback remain in the existing menu. Upcoming cards stay compact, prejoin preserves consent, and next-step has only three actions.

`ProductionTaskStates.tsx` renders the actual production workspace with synthetic media and intercepted API requests. No production API, camera, messages or candidate decisions are used. Stories: `hr-vision-employer-task-production--completed`, `--scheduled`, `--scheduled-zoom`, `--next-step`, `--offer`, `--read-only`. The six stories are excluded from service builds.

Server validation: 1,290 tests passed (2 skipped), including separate real HTTP meeting-comment tests. Player/workflow checks: 60; receiver: 68; Zoom participant component: 10; receipt/reconciliation: 13. Local initial runs without the material-runtime dependencies failed at environment setup; the complete run used the existing materials virtual environment and passed. Browser evidence is stored under `output/playwright/employer-task-states/`. No two-party call was created for this UI release.
