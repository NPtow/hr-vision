# HH: адаптер существующего браузера

Пакет не запускает Chromium, не читает cookie, не использует HH API. `WorkerBridge.poll(page)` выполняется в единственном Playwright-потоке существующего `hh-daily/worker.py`. Старый парсер и новые команды используют одного владельца. HTTP-потоки кладут команды в очередь, но не получают объект `page`.

## Граница готовности

Протокол, блокировки и два источника проверяются локальными синтетическими страницами. Реальная разметка employer-кабинета HH ещё не откалибрована: серверный профиль на момент аудита 14.10.2026 сообщает `logged_in=false`. `selector-profile.example.json` содержит **только селекторы тестового HTML**, `calibrated=false`; это не готовая карта живого сайта. При такой конфигурации отправка закрыта до любых изменений composer.

Калибровка проводится на авторизованном employer-профиле без отправки: снять точные account/company ID, роль, resume ID, chat ID, список именно исходящих сообщений и устойчивые message ID. Отдельный `fullCVMarker` должен доказывать доступ к полному CV, а не к карточке-превью. Не подставлять ID из задания вместо наблюдаемого DOM. Только после проверки идентичности/состояний и внешней приёмки рабочего профиля выставлять `calibrated=true` в серверном конфиге. Тестовые данные не содержат реальных людей.

## Два входа и один результат

`discover({kind:'direct',accountId,resumeUrls,limit})` читает явно выбранные страницы резюме.

`discover({kind:'vacancy_applicants',accountId,vacancyId,listUrl,limit})` читает существующую страницу откликов конкретной вакансии, до 20 страниц за операцию. `listUrl` берётся из наблюдаемой ссылки HH, не строится предположением из публичной вакансии. `responseId` обязателен.

Результат `{candidates,complete,cursor}`. За одну операцию возвращается максимум 25 кандидатов, даже если requested limit больше. Caller сохраняет `cursor` и передаёт его в следующий `source.cursor` до `complete=true`; курсор помнит страницу и строку, включая остановку посреди страницы. Direct-вход поддерживает до 2000 явно выбранных URL с курсором offset. Кандидат: `accountId,resumeId,chatId,responseId,vacancyId,name,cvText,cvAvailable,resumeUrl,chatUrl,source,discoveryStatus`. Без готового диалога результат `chat_unavailable`: адаптер не покупает контакты и не нажимает «Пригласить» для создания чата. Частичный CV имеет `cvAvailable=false`. Пустые ID необязательных полей caller переводит в `None` при импорте. Дедупликация между кампаниями принадлежит durable engine.

Для фонового импорта `begin_discovery(source)` сразу возвращает `{operationId,status:'queued'}`; `discovery_status(operationId)` читает сохранённое состояние `queued/running/completed/failed/interrupted` и `result` при завершении. Результат хранится в приватной SQLite, перезапуск помечает незавершённую операцию interrupted, не изображает пустую выдачу. Повтор discovery безопасен (не отправляет сообщений); обработку результатов/дедупликацию выполняет caller. Convenience `discover` ждёт до 120 секунд, затем возвращает pending operation, которую надо продолжать опрашивать. Запрос ограничен 256 КиБ, ответ 20 МиБ; лимит 25 CV × 80000 символов учитывает даже JSON escaping, а не только число символов.

## Контракт исполнения

```python
from hh_browser import BrowserBridgeClient

client = BrowserBridgeClient('http://127.0.0.1:8140', private_bridge_token)
prepared = client.prepare(job, job['message'])
result = client.send(prepared['preparedId'], job['attemptId'],
    before_send=lambda attempt_id: engine.start(job['jobId'], job['claimToken']))
```

1. `prepare` проверяет работодателя, кандидата и диалог, перечитывает сообщения после reload, сохраняет исходные message IDs и заполняет composer. Ничего не отправляет. Повтор уже существующего текста требует проверки.
2. Callback `before_send` фиксирует `send_started` в БД кампаний **до** HTTP-вызова send. Исключение/`False` полностью запрещает вызов.
3. Worker фиксирует свой barrier с attempt ID, target, message/hash и baseline в SQLite с `synchronous=FULL`, ещё раз проверяет адресата и текст, нажимает Send один раз.
4. Сначала до 10 секунд ждём предварительный DOM-ack: новый исходящий message ID и точный текст. Навигация сразу после click могла бы прервать запрос сохранения. Затем `observe` перечитывает тот же диалог после навигации/reload. Только новое единственное исходящее сообщение с точным текстом и устойчивым message ID даёт `sent_observed`. Это наблюдение сообщения в переписке, не обещание доставки/прочтения адресатом.
5. Любой неизвестный исход после barrier — `unknown`, очередь на паузу. Повторный `send` с тем же attempt ID никогда не нажимает кнопку снова. `observe(attempt_id)` может подтвердить ранее сохранённую попытку; отсутствие видимого сообщения не доказывает неотправку. Автоматического повторения нет.

`sent_observed` содержит `evidence={accountId,resumeId,chatId,messageHash,messageId,observedAt,persisted:true}`; `observedAt` — Unix seconds. После подтверждения предыдущего uncertain нужна явная `resume()`; непроверенные uncertain не дают возобновить работу. Оператор может явно `acknowledge(attempt_id)` оставить попытку на ручной проверке и разрешить продолжение других заданий. Это не снимает durable barrier и не даёт повторить её отправку. Фоновый `observe` для такой попытки не навигирует и не ставит общую паузу снова; отдельная ручная перепроверка — `observe(attempt_id,recheck=True)`.

## Владение и пауза

`pause()` сохраняется немедленно; текущий запрос браузера может завершаться, но новых отправок не начинается. Проверка паузы выполняется непосредственно перед кликом. Результат уже начавшегося клика определяется журналом, а не флагом паузы.

`manual(True)` подтверждается только после safe checkpoint на главном потоке. Пока работает старый batch, ручной режим ждёт его завершения; timeout означает отсутствие подтверждённого ручного владения. Во время manual/paused нет idle-навигации на главную HH; manual переживает перезапуск и не снимается таймером. `manual(False)` отдаёт управление. Подготовленная отправка резервирует браузер на 90 секунд и блокирует парсер/discovery/manual до отправки или истечения срока. Профиль нельзя одновременно открывать другим Chromium.

Существующий noVNC позволяет человеку взаимодействовать напрямую; человек должен сначала получить подтверждённый manual-режим. Этот пакет не способен запретить внешние клики через неуправляемый VNC-клиент. Доступ к VNC следует ограничить существующим gate. Во время аудита рабочий Caddy-route не содержал исторически заявленный forward_auth; пароль x11vnc присутствовал.

## Подключение к существующему worker

`integration.py` создаёт новый файл для просмотра diff, **не меняет исходник и не выполняет deploy**:

```sh
python3 hh_browser/integration.py /path/hh-daily/worker.py --output /tmp/worker.rpa.py
diff -u /path/hh-daily/worker.py /tmp/worker.rpa.py
```

Точные anchors проверяются: другая версия исходника требует повторного review. `hh_browser` (четыре runtime-файла `__init__,adapter,bridge,store`) размещается рядом с worker.py. Используется уже установленное Python/Playwright окружение `~/hh-rpa/.venv` и тот же запущенный профиль после согласованного перезапуска, без копирования cookie.

Новые настройки отдельным EnvironmentFile 0600: `HH_RPA_ENABLED=1`, `HH_RPA_DATA_DIR=/var/lib/hh-rpa-bridge`, `HH_RPA_SELECTORS=/etc/hh-daily/rpa-selectors.json`, `HH_RPA_TOKEN_FILE=/etc/hh-daily/rpa-token`, `HH_RPA_ACCOUNT_ID=<проверенный HH employer account ID>`. Токен минимум 32 символа, генерируется оператором, не печатается в логах. SQLite содержит персональные тексты/booking-ссылки: каталог 0700, файл 0600; не коммитить.

HTTP `/rpa/*` расширяет существующий listener `127.0.0.1:8140`, каждый путь требует отдельный Bearer. Не публиковать его через Caddy. Между HR API и outreach использовать управляемый SSH-туннель на loopback (существующая схема SSH через dsa). Клиент разрешает только loopback, не следует redirect и не повторяет запросы.

GET `/rpa/status`: `configured,loggedIn,accountId,busy,captcha,error,selectorsCalibrated,paused,pauseReason,manual,queued`. Состояние login — cached чтение DOM на потоке владельца с интервалом 15 секунд между задачами, не новая навигация; длительный старый batch задержит следующий замер. GET `/rpa/operations/<id>` — состояние импорта. POST: `/discover`, `/prepare`, `/send`, `/observe`, `/pause`, `/resume`, `/manual`, `/acknowledge` под тем же `/rpa`. Обычная команда имеет 90 секунд на ответ worker, timeout клиента по умолчанию 95 секунд; discovery использует отдельное асинхронное состояние.

Проверить перед включением: работоспособный gate, employer-сессия, селекторы на текущей HH, отсутствие активного старого задания, остановка отправок при потере входа/капче, корректная выдача booking-ссылки. Рабочие отправки и публикации этот пакет автоматически при установке не запускает.

Официальная документация, использованная через Context7 (resolve → docs, 14.10.2026): https://playwright.dev/python/docs/locators и https://playwright.dev/python/docs/api/class-locator. Селекторы конкретного HH в этих документах отсутствуют.
