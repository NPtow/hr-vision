# Native Signals

Нативное macOS-приложение сбора и разбора публичных сигналов. Swift-пакет
содержит `SignalsCore`, `SignalsApp` и тесты. `board-web/` собирает локальный
React-компонент для WKWebView; `rpa/telemetr/` содержит браузерный адаптер.

```sh
npm ci --prefix board-web
npm test --prefix board-web
npm run build --prefix board-web
npm ci --prefix rpa/telemetr --ignore-scripts
swift test
bash scripts/build-app.sh
```

Команды выполняются из этой папки на macOS с Xcode Command Line Tools.
Сборка создаёт `dist/TG Рассылщик.app` с локальной ad-hoc подписью; установленное
приложение не заменяется. Внешний сбор требует отдельной настройки интеграций.
Тесты не должны использовать действующие пользовательские данные.

Состояние хранится вне репозитория, в каталоге поддержки приложения. Ключи
не включены. `Sources/SignalsCore/Resources/discovered-companies.json` сохранён
как встроенный каталог. Исследовательские выгрузки остаются в прежней локальной
рабочей папке: они нужны скриптам пополнения, но не обычной сборке приложения.
