# Запись → независимый ASR → транскрипт в карточке кандидата

Статус: реализация и приёмка, 25.10.2026. Первый рабочий провайдер переключён на Deepgram по прямому решению заказчика; SpeechKit сохранён как альтернативный адаптер. База кода: `f0478bc0`.
Заказчик разрешил реализацию двумя воркерами и независимым ревьюером; архитектор интегрирует и проверяет результат.

## Результат и границы

Готовая запись конкретного интервью получает отдельную, сохранённую у нас расшифровку выбранного ASR. Она читается через существующие `materials` / `dossier` API рядом с исходным Zoom-текстом. Наличие текста, достоверность говорящего и качество распознавания — отдельные факты.

1. **Источник / координатор доставки** знает интервью и `binding_id`, получает запись, запускает ASR, сохраняет исходный ответ в нашем S3 и отправляет receipt в ядро. Его состояние живёт отдельно от HR-БД. Для текущего collector добавляется небольшой HTTP-вызов о готовой записи; координатор выпускается отдельно.
2. **Сервис транскрибации** знает байты, параметры распознавания и владельца API-задания. Извлекает звук, запускает выбранный адаптер, сохраняет состояние запроса и результат. Не знает кандидата, Zoom/Daily, HR-БД или ключей ядра.
3. **Ядро данных** проверяет источник, поколение binding, S3-ссылку и единый формат; сохраняет неизменяемую версию. Не запускает ASR и не скачивает запись. Используем существующий `POST /v1/bindings/{id}/receipts`.

Не входит: переоценка кандидатов, LLM-исправление речи, выбор «победившего» ASR, новая видеосвязь, переделка кабинетов, массовая обработка истории. Исторические интервью запускаются адресно. Автоматический режим ограничивается явно заданными источниками и датой включения; по умолчанию выключен.

## Проверенные исходные факты

- `services/transcription` уже содержит API загрузки байтов, SQLite/SQLAlchemy, RabbitMQ/Celery, idempotency и fenced leases. Текущий backend поддерживает только синхронный OpenAI-compatible HTTP ASR.
- На YC работает normalizer: deployment entrypoint обнуляет ASR/broker. Отдельного ASR worker нет. Новую ASR-службу разворачиваем независимо; существующий normalizer остаётся доступным.
- Core уже хранит `media_sources → media_assets → transcript_versions`; owner интервью связан с `cp_candidates`. Повтор receipt идемпотентен. ASR-текст публикуется отдельным asset, исходный transcript не заменяется.
- SpeechKit v3: `POST /stt/v3/recognizeFileAsync`, GET operation, `GET /stt/v3/getRecognition?operationId=…`. Для длинных файлов используется приватный S3 и обычный object URI; **подпись URL в query SpeechKit игнорирует**. Сервисный аккаунт распознавания должен иметь право читать staging-объект. API принимает также bytes, но body ограничен 60 MB. Ограничение асинхронного режима: 4 часа / 1 GB в бакете. Результат у провайдера хранится 3 дня, поэтому копируем его сразу.
- Разметка дикторов: только mono/FULL_DATA, максимум два голоса. Метка голоса не означает роль или имя. Для интервью с тремя участниками и без отдельных дорожек роль остаётся неизвестной. Двухголосный режим включается явно, не применяется ко всему корпусу.

## Контракты

**ASR API (сохраняем существующий):** `POST /v1/jobs?language=ru`, raw media bytes, Bearer, `Idempotency-Key`, `X-Filename`; HTTP 202 + job ID. GET status/result. `/v1/normalize` совместим с прежними клиентами. Снаружи провайдер не выбирается произвольным URL: backend настраивает оператор.

**Deepgram, выбран сейчас:** direct prerecorded REST `/v1/listen`, Nova-3, русский `ru`, `diarize_model=v2`, `utterances=true`, `punctuate=true`. `smart_format=false`, `numerals=false`, `profanity_filter=false`; без суммаризации/редакции. Платный POST выполняется один раз; исходный ответ сохраняется до разбора. После рестарта уже сохранённый ответ разбирается без повторного запроса. Неизвестный исход POST остаётся `uncertain`. `speaker_N` — только метка провайдера, имя/роль не угадываются. Смена ключа того же провайдера — конфигурация; смена провайдера — выбор адаптера и новая revision, не простая подстановка чужого ключа.

**SpeechKit, альтернативный адаптер:** `backend_kind=yandex_speechkit`, модель `general`, язык `ru-RU`, FULL_DATA. FFmpeg/ffprobe работают только с локальным входом и запретом сетевых протоколов. MP4/WebM/M4A/MP3/WAV/FLAC/OGG → PCM WAV 16 kHz; ограничение длительности проверяется до обращения к провайдеру; никаких обрезок до лимита. Один первый audio stream, это явно отражается в provenance; отдельные participant tracks — следующий контракт. Speaker labeling выключен по умолчанию. Никакой литературной переработки/суммаризации.

В результате сохраняются текст, сегменты с миллисекундами, provider channel labels (не имена), warnings, исходный provider response, модель/конфигурация, SHA записи. `coverage=not_independently_verified`. Формат сегментов совместим с существующим `NormalizedTranscript`; provider raw response доступен через отдельный защищённый result/raw endpoint либо явно документированное поле envelope, не ломающее normalize.

**Источник:** отдельный пакет `services/transcription-source` с API/worker и собственной SQLite. `POST /v1/recordings` принимает только owner kind/id, binding id/generation и asset id, с Bearer/idempotency. Никаких пользовательских URL, candidate ID из тела не является основанием связи. Координатор проверяет материалы через Core, сверяет принадлежность asset источнику/владельцу и актуальное поколение. Получает bytes из нашего S3; remote-источники получают bytes через явно настроенный source resolver, без общего URL-прокси. Неподдерживаемый remote resolver возвращает понятную ошибку, не меняет storage policy.

Повтор одинакового события использует стабильный ключ по binding/generation/asset/config revision. Намеренная новая версия требует отдельной revision. GET job показывает отдельно `transcription_status` и `delivery_status`, после доставки — `transcript_ids` из receipt.

**Доставка:** после ASR сохраняем raw response в configured materials S3 через content-addressed key и SHA/size, затем receipt с `origin=deepgram` либо `yandex_speechkit`, моделью, языком, input asset/job reference (в пределах согласованного wire-контракта), `processing_status=completed`. External ID нового transcript детерминированный, привязан к input asset/job. Повтор receipt не добавляет версию. После записи выполняем Core GET и проверяем, что нужная версия видна у нужного интервью/кандидата. Ошибка Core не перезапускает ASR. Результат не подменяет Zoom и не становится автоматически входом assessment.

## Надёжность

- Durable job до публикации в RabbitMQ; scheduler восстанавливает потерянную доставку. Раздельные очереди ASR и источника, concurrency=1 сначала.
- Deepgram submit: намерение фиксируется до POST; полный raw response — до canonical parser. Рестарт с raw продолжает локальную обработку; неизвестный исход платного запроса не повторяется автоматически.
- SpeechKit submit: сохранить намерение до сетевого POST, operation ID сразу после ответа. Известный operation ID переживает рестарт: только polling/result GET, без повторного submit. Недоказанный исход POST → `uncertain`, автоматического повторного списания нет. Универсальную exactly-once семантику провайдера не предполагаем.
- Таймауты/429 при polling повторяем с backoff; истёкшая lease с известной операцией возобновляется, старый worker не может записать результат. Нельзя терять поздний operation ID и молча повторять платный POST.
- Upload SHA/size, bounded streaming, защищённые temp files, лимит диска/длительности/ответа. Логи не содержат текста, ключей и подписанных URL.
- Private S3; staging prefix отдельно от постоянных транскриптов. Очистка временного аудио после сохранённого результата с безопасным повтором; никаких удалений исходного видео. Секреты вне Git. API слушает loopback; authenticated health отдельно сообщает API/config/worker.
- Отмена/изменённое поколение/revoked binding не допускают публикацию результата старому или чужому кандидату.
- В v1 один хост и SQLite WAL; restart-safe не означает отказоустойчивый кластер.

## Что берём готовым

| Функция | Решение / точка замены | Цена |
|---|---|---|
| HTTP/auth/body limits | существующие FastAPI/Starlette/Pydantic | сохраняем контракт и pinned versions |
| Durable jobs/idempotency | существующий Repository / SQLAlchemy | добавляем состояние provider operation |
| Очередь/reconciliation | существующие Celery/RabbitMQ | обязательны worker + scheduler |
| Извлечение audio | системные FFmpeg/ffprobe; `audio.py` | отдельный процесс, CPU/disk limits |
| S3 I/O | boto3 1.43.118 уже в materials lock | новый сервис использует тот же проверенный пакет |
| Единый транскрипт | наш существующий normalizer/contract | не изобретать новый формат |
| Привязка/версии/ACL | существующий Core receipt | HTTP, без прямого SQL из ASR |
| Deepgram protocol | официальный prerecorded REST + уже установленный aiohttp | маленький адаптер, никаких новых SDK |
| SpeechKit protocol | официальный REST v3 + aiohttp | небольшой адаптер и parser NDJSON/events |
| Возобновление операции | свой glue к Repository | submit/poll/result checkpoints |
| Связь записи и кандидата | свой source-side coordinator | собственное состояние доставки |
| Автозапуск при готовой записи | свой маленький collector hook | feature flag и ограничение истории |

Основные компоненты HTTP, очереди, S3 и Core повторно используются. Новый код — адаптеры провайдеров и надёжная стыковка состояния; собственного ASR-движка не пишем. Проверен SDK `yandex-ai-studio-sdk` 0.22.1: паспорт сообщает non-standard license, API <1.0, и SDK существенно шире нужных трёх запросов. Не добавляем его; используем уже установленный HTTP-клиент и официальный контракт. Донор [yc-speechkit-async-recognizer](https://github.com/yandex-cloud-examples/yc-speechkit-async-recognizer) подтверждает схему S3 → operation ID → polling → собственное сохранение; его старый API v2 и инфраструктуру не копируем.

## Задания и приёмка

**Воркер 1:** только `services/transcription/**`: Deepgram и SpeechKit REST adapters, безопасный FFmpeg, durable provider state/migration, raw result, tests, env/compose/docs. Существующий HTTP backend и normalize не сломать.

**Воркер 2:** только `services/transcription-source/**`, `services/recruiting-api/materials_asr_dispatch.py`, узкий hook `materials_runtime.py` и его новые тесты: source job → media → ASR → S3 → Core receipt/readback. Не менять Core, ASR-пакет и существующий deploy. Зафиксировать точный HTTP контракт для архитектора.

**Ревьюер:** независимый анализ specs/code/tests обоих; проверить границы, ложную полноту, роли, дубли платных запросов, восстановление, сохранение результата и принадлежность кандидату. Без собственных feature edits.

**Архитектор:** API стыковка, схема deploy (отдельные ASR API/worker/scheduler + source coordinator), проверка credentials/readiness, интеграционный тест и выпускной отчёт.

Приёмка: upload → persisted job → actual worker → async provider fixture → result → S3 → настоящий Core API/test DB → dossier нужного кандидата. Повтор входа и сбой Core не вызывают второе распознавание. Рестарт после operation ID и после receipt ack-loss восстанавливается. Чужой binding/asset, revoked/stale generation, повреждённый файл, 429/timeout, неизвестный POST исход покрыты тестами. Отдельно настоящий Deepgram на одном разрешённом интервью с локальным ключом; SpeechKit требует своего сервисного аккаунта и остаётся непроверенным на живом аудио; без этого честно называем проверку интеграционной, а не проверкой качества ASR. Массовый backfill выключен.

## Источники исследования

- Context7: `/yandex-cloud/docs`, `/yandex-cloud/yandex-ai-studio-sdk`; запросы про async SpeechKit, `run_deferred`, `get_recognition_result`. SDK сверён с primary docs ниже.
- [Async recognize API](https://aistudio.yandex.ru/ru/docs/speechkit/stt-v3/api-ref/AsyncRecognizer/recognizeFile)
- [Result API](https://aistudio.yandex.ru/ru/docs/speechkit/stt-v3/api-ref/AsyncRecognizer/getRecognition)
- [Официальный пример WAV](https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/transcribation-api-v3)
- [Лимиты](https://aistudio.yandex.ru/ru/docs/speechkit/concepts/limits)
- [Разметка дикторов](https://aistudio.yandex.ru/ru/docs/speechkit/stt/speaker-labeling)
- [FFmpeg](https://ffmpeg.org/ffmpeg.html)

- Context7 `/websites/developers_deepgram`: prerecorded binary upload и result/callback contract.
- [Deepgram prerecorded API](https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded)
- [Deepgram languages](https://developers.deepgram.com/docs/models-languages-overview/)
- [Deepgram speaker diarization](https://developers.deepgram.com/docs/diarization)
