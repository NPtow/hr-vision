# Главное из интервью — отдельный макет профиля

Story: `hr-vision-candidate-highlights--profile`

http://127.0.0.1:6006/iframe.html?id=hr-vision-candidate-highlights--profile&viewMode=story

## Что исследовано и перенесено

Исходная карточка: BrightHire · фрагменты в существующей галерее
`hr-vision-candidate-research--gallery`.

- [BrightHire Clips](https://brighthire.com/blog/share-great-interview-moments-with-brighthire-clips/): тематические отрезки разговора, передача конкретных моментов, просмотр контекста.
- [Sourcegraph: guide to using BrightHire](https://github.com/sourcegraph/handbook/blob/main/content/departments/people-talent/talent/tools/guide_to_using_brighthire.md): связь заметок с записью и расшифровкой, работа с материалами кандидата.

Заимствована механика, не чужие кандидаты, видео или визуальный бренд.
Наша гипотеза: профиль → интересующий ответ → вопрос и цитата → связанный навык → полный разговор с той же позиции.

## Сцена

- 4 тематических карточки, фильтр тем, просмотр по одному и последовательно.
- Плеер: пауза, перемотка с клавиатуры/ползунком, скорость, ограничение фрагмента, автоматическое продолжение в режиме подборки.
- Полный разговор сохраняет позицию; можно переключиться между темами и вернуться к фрагменту.
- Описание поведения связано с конкретным ответом. Блок навыков раскрывается и возвращает к видео.
- Диалог с Escape, возвратом фокуса, мобильной компоновкой, reduced motion.

## Честная граница макета

Анна Морозова, цитаты и уровни — вымышленные UI fixtures, а не данные JEV/ASR.
Все четыре уровня намеренно заданы примером; это не новая методика и не подключение каталога ядра.
Видео — наш 32-секундный тихий аниматик с четырьмя 8-секундными главами. Оно проверяет работу плеера и не является подтверждением цитат.
Локальные исходные видео и шрифты взяты из
`/Users/NIKITA/.codex/worktrees/employer-task-states/HR Vision/interface-lab/public/candidate-public-profile/`.
Лицензия Inter лежит рядом с копией шрифтов в `public/media/candidate-interview-highlights/`.

Нет API-запросов к HR Vision, загрузки реальных интервью, генерации публичных токенов, изменения разрешений или публикации.
Для будущего подключения нужны сохранённые highlight IDs, interview IDs и точные временные границы; доступ к полному интервью должен проверяться отдельно от права на отдельный фрагмент.

## Где находится существующий публичный профиль

В рабочем дереве `employer-task-states/HR Vision`:
- `interface-lab/src/scenes/candidate-portal/PublicCandidateProfile.tsx`
- `interface-lab/server/public_profiles.py`
- `interface-lab/server/PUBLIC_PROFILES.md`

Эти файлы и существующая галерея не изменялись. Новый компонент можно рассматривать как блок для будущего включения в публичный профиль и кабинет, после согласования макета.

## Проверка

`npm run typecheck`

`./node_modules/.bin/storybook build --output-dir output/interview-highlights-20261026/storybook-static --disable-telemetry --quiet`

Снимки и результаты ручного прохождения: `output/interview-highlights-20261026/`.
