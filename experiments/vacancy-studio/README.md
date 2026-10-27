# Vacancy Studio

Рабочий макет создания вакансии и исследование вариантов профиля. Перенесён из
отдельной локальной Storybook-лаборатории 28 октября 2026 года. Сервер, UI,
каталог навыков, промпты и тесты сохранены; список исходников с хешами находится
в `source-provenance.json`. Оригинальная лаборатория не изменена.

```sh
npm ci
npm run typecheck
npm run build
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes --only-binary=:all: -r server/vacancy-studio/requirements.lock
python -m unittest discover -s server/vacancy-studio -q
python server/vacancy-studio/run.py
```

Откройте `http://127.0.0.1:4321/iframe.html?id=hr-vision-vacancy-studio--live`.
Для разработки UI выполните `npm run storybook`: порт 6006, API на порту 4321.

Для реальных ответов нужны `OPENAI_API_KEY` и совместимый `OPENAI_BASE_URL`,
для голоса — `DEEPGRAM_API_KEY`. Модель задаётся через `STUDIO_MODEL`.
Без этих настроек UI и изолированные тесты доступны, вызовы провайдеров нет.
Launcher принимает переменные окружения и не извлекает ключи с других серверов.

Черновики по умолчанию находятся в игнорируемом `output/`; путь можно изменить
через `STUDIO_STATE`. Это отдельный эксперимент. Публикация вакансии в основной
продукт пока не реализована. Подробности исходной реализации находятся в
[README сервера](server/vacancy-studio/README.md), предложение интеграции — в
[CORE-INTEGRATION.md](server/vacancy-studio/CORE-INTEGRATION.md).
