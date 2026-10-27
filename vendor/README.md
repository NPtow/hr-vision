# Сторонний код

`dograh/` — сохранённый локальный fork Dograh, включая HR Vision-интеграцию в
`deploy/mashadasha/`. `dograh/pipecat/` — закреплённый submodule, включённый
обычными файлами: одного клонирования HR Vision достаточно.

Точные upstream-адреса и коммиты: [sources.json](sources.json). Лицензии и notices
сохранены в исходных каталогах. История вложенных Git-репозиториев не импортируется;
оригинальные локальные репозитории сохранены.

Исключены upstream GitHub workflows, `.gitmodules` и локальные JSON-отчёты
проверки реальных сессий. Общедоступные значения upstream PostHog/Chatwoot
очищены; интеграция настраивается явно. Исходный код, тесты, Docker/Compose
и HR workflow-конфигурации сохранены.

Основной pipeline HR Vision не собирает весь сторонний стек. Его команды
находятся в `dograh/README.md` и `dograh/AGENTS.md`.
