# HR Vision

Один репозиторий приложений и сервисов HR Vision / МашаДаша. Исходники,
зависимости и инструкции каждого компонента находятся рядом с ним.

## Карта кода

| Каталог | Назначение |
| --- | --- |
| [apps/web](apps/web) | Интерфейс работодателя, кандидата, рекрутера и Верстак. Общая React / Storybook-сборка с режимами `service` и `mockups`. |
| [apps/website](apps/website) | Публичный сайт и галерея анимаций, собственные npm-зависимости. |
| [apps/candidate-preview](apps/candidate-preview) | Отдельный просмотр кандидата: React и Python BFF. |
| [apps/native-signals](apps/native-signals) | Нативное macOS-приложение, локальный web-компонент и RPA. |
| [services](services/README.md) | Серверные компоненты и границы ответственности. |
| [packages](packages/README.md) | Общие контракты, UI-код и клиент JEV. |
| [infra](infra/README.md) | Выпуск, окружения и наблюдение. |
| [tests/integration](tests/integration) | Межсервисные сценарии на изолированных данных. |
| [experiments](experiments/README.md) | Прототипы вне рабочей сборки. |
| [vendor](vendor/README.md) | Dograh и закреплённый Pipecat, происхождение и лицензии. |
| [docs](docs/README.md) | Архитектура, эксплуатация, планы, CJM и история. |

## Быстрый запуск web

Node.js 24, npm и Python 3.12 для общей проверки. У отдельных сервисов могут
быть более низкие минимальные версии; см. их README. Web, сайт и `packages/web-shared` образуют npm workspace с корневым lock-файлом.
Остальные приложения устанавливаются независимо со своими lock-файлами.

```sh
npm ci
npm run typecheck --prefix apps/web
HR_BUILD_TARGET=service npm run build --prefix apps/web

# Локальные макеты без рабочей базы и провайдеров
HR_BUILD_TARGET=mockups npm run storybook --prefix apps/web
```

Рабочим экранам нужен [recruiting-api](services/recruiting-api/README.md).
Секреты и путь к данным задаются через окружение; пример лежит рядом с сервисом.
Для локальной проверки используйте тесты с временным состоянием.

## Проверки

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: \
  -r services/recruiting-api/requirements-materials.lock \
  -r services/recruiting-api/requirements-api-extras.lock \
  -r infra/deployment/services/requirements-tests.lock \
  -r infra/deployment/services/transcription/requirements-tests.lock
python scripts/check.py web
python scripts/check.py api
python scripts/check.py services
```

`python scripts/check.py --help` показывает остальные группы. Зависимости
`site`, `preview` и `native` устанавливаются по README соответствующих приложений.
Проверки PR определены в [.github/workflows/checks.yml](.github/workflows/checks.yml).
Ответы внешних провайдеров в них подменены; это не приёмка production.

## Работа с Git и выпуск

Актуальная общая версия — `main`. Для задачи создаётся короткая ветка
`codex/<задача>`, проверяется PR и после слияния удаляется рабочая ветка.
Параллельные изменения выполняются в отдельных worktree. [Правила](docs/repository/workflow.md).

Push и проверка PR не публикуют продукт. Выпуск web/API запускается явно через
`Deploy HR Vision` для `main` либо `codex/mockups`; сохраняются проверка назначения,
ограниченный deploy receiver и проверка опубликованной ревизии. Отдельные сервисы
сохраняют свои упаковщики. [Инфраструктура](infra/README.md).

Старые локальные варианты сохранены в [реестре исходников](docs/repository/source-archives.md).
Они не заменяют `main` и не считаются проверенными релизами. [Состав переноса и проверки](docs/repository/organization.md).

`output/`, локальные базы, записи интервью, выгрузки и ключи не добавляются в Git.
Ранее опубликованные исторические иллюстрации в `output/` сохранены для существующих ссылок.
