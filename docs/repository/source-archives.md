# Сохранённые исходники — 28 октября 2026

Ветки содержат снимки исходников до структурного переноса. Изменённые и новые файлы сохранены без смешивания альтернативных реализаций. Исходные рабочие копии и ветки остаются на месте.

CI каждого снимка сохранён в `.archive/github-workflows/` и не запускается при push. История снимка опирается на опубликованный `main`, чтобы не публиковать непроверенные промежуточные локальные коммиты.

| Источник | Снимок | Изменённых файлов | Новых файлов |
| --- | --- | ---: | ---: |
| main / primary-workspace | [68754026abb2](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/primary-workspace) | 37 | 286 |
| (detached) / account-avatar-style | [3d098135e4ab](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/account-avatar-style) | 4 | 2 |
| codex/employer-core-speakers / assessment-orbit-production | [a6da651d28a9](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/assessment-orbit-production) | 0 | 1 |
| codex/candidate-cabinet / candidate-cabinet | [d0e0f2c13160](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/candidate-cabinet) | 0 | 31 |
| codex/hh-browser-outreach / candidate-cv | [6f1f3bf9ce98](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/candidate-cv) | 5 | 1 |
| codex/recruiter-compact-interview / company-site-integration | [2a4bbf56407e](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/company-site-integration) | 2 | 21 |
| fix/daily-bundle-origin / daily-bundle-origin | [e8e82de01227](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/daily-bundle-origin) | 7 | 1 |
| codex/employer-panel / employer-panel | [6dfdf7fdfbc1](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/employer-panel) | 4 | 2 |
| codex/hh-outreach-release / hh-outreach-release | [ddb0d93f7cad](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/hh-outreach-release) | 0 | 4 |
| codex/test-interview-import / materials-production | [8a2c84cf21ef](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/materials-production) | 2 | 43 |
| codex/assessment-interpretation-preview / recruiter-resume-popup | [879c8b191e69](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/recruiter-resume-popup) | 7 | 27 |
| codex/selection-vacancy-ui / selection-vacancy-ui | [f6bb003a798c](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/selection-vacancy-ui) | 0 | 0 |
| codex/transcription-service / transcription-service | [3003fdadc7f8](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/transcription-service) | 0 | 0 |
| codex/candidate-decision-search-lab / candidate-decision-search-lab | [3a768e82da43](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/candidate-decision-search-lab) | 0 | 0 |
| codex/interview-feedback-panel / interview-feedback-panel | [aa51195b9162](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/interview-feedback-panel) | 0 | 0 |
| codex/unified-transcript-preview / unified-transcript-preview | [c0bba46f7010](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/unified-transcript-preview) | 0 | 0 |
| main (concurrent working copy) / primary-workspace-followup | [579da03ef7bd](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/primary-workspace-followup) | 41 | 290 |
| standalone HR Storybook sources / hr-design-lab | [f503177604ea](https://github.com/NPtow/hr-vision/tree/codex/archive/2026-10-28/hr-design-lab) | 0 | 295 |

В снимки не включены окружения, зависимости, сборки, записи интервью, runtime-базы и генерируемые операционные выгрузки. Полный локальный оригинал сохранён. Для возврата функции сравните соответствующий снимок с текущим кодом и перенесите выбранное изменение через новую рабочую ветку.

Дополнительный снимок `primary-workspace-followup` сохраняет изменения Zoom, появившиеся параллельно после первого снимка. `hr-design-lab` содержит HR-сцены отдельной смешанной Storybook-лаборатории и их зависимости в `experiments/hr-design-lab`; другие проекты и видео не включены. Готовый к локальной сборке Vacancy Studio также перенесён в основную ветку, в `experiments/vacancy-studio`.
