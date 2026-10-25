# Запуск и передача в эксплуатацию

## Объём первого запуска

Первый запуск — только `/v1/normalize`: готовый транскрипт → единый формат. ASR URL/model/key остаются пустыми. После проверки источник переключается на этот API. Для этого запуска RabbitMQ не нужен.

Все команды ниже выполняются из `services/transcription`. Нужны Docker Compose и защищённый env-файл на целевой машине. Пример ниже использует `/etc/hr-vision/transcription.env`; этот файл создаёт оператор вне Git. Не копировать в него ключи Daily, Zoom или БД ядра.

Обязательная конфигурация:

```dotenv
TRANSCRIPTION_API_KEYS={"daily-source":"<случайный уникальный ключ длиной не менее 24 символов>"}
TRANSCRIPTION_PORT=8093
```

Для каждого источника нужен отдельный ключ. Env-файл должен быть доступен только оператору (`chmod 600`). Источнику выдаются его ключ и адрес API через его собственный секретный env-файл; значения не передаются в чат и не записываются в логи.

```sh
docker compose --env-file /etc/hr-vision/transcription.env config --quiet
docker compose --env-file /etc/hr-vision/transcription.env build api
docker compose --env-file /etc/hr-vision/transcription.env up -d api
curl --fail http://127.0.0.1:8093/health/live
```

`/health/live` проверяет HTTP-процесс. Источник дополнительно вызывает `/health/ready` со своим Bearer-ключом и делает реальный POST `/v1/normalize` на синтетическом VTT: ожидаются 200, точный текст и таймкоды. Затем сохраняет результат через core ingest. Проверка health сама по себе не подтверждает весь этот цикл.

API слушает host localhost. Для клиента в другом контейнере `127.0.0.1` означает сам этот контейнер: подключить контейнеры к отдельной внутренней сети и использовать имя `api` с уникальным network alias, либо настроить приватный gateway. Публичного порта по умолчанию нет. При межмашинной связи нужен HTTPS; токены не передавать по открытому HTTP.

## Переключение старого collector

Сначала запустить и проверить новый API. Затем добавить в окружение collector:

```dotenv
HR_TRANSCRIPTION_URL=http://127.0.0.1:8093
HR_TRANSCRIPTION_TOKEN=<ключ именно этого collector>
```

Адрес выше подходит collector на хосте. Для контейнера использовать приватный адрес из предыдущего раздела. После этого выпускать замену `materials_transcripts.py`. Без сервиса/ключа нормализация откладывается как retryable; исходник уже сохранён, а чтение ранее сохранённых транскриптов продолжает работать. Нельзя считать такую конфигурацию завершённой выкаткой.

Старый файл — переходный клиент. Новый независимый source-service сразу использует [CONTRACT.md](CONTRACT.md), получает готовый результат и доставляет его в ядро. Ядро не получает ASR ключи и не запускает транскрибацию.

## После отдельного выбора ASR

В тот же защищённый env-файл добавить полный URL совместимого `/audio/transcriptions`, явно выбранную модель и ключ, если он нужен:

```dotenv
TRANSCRIPTION_ASR_URL=https://<approved-inference-host>/v1/audio/transcriptions
TRANSCRIPTION_ASR_MODEL=<выбранная после сравнения модель>
TRANSCRIPTION_ASR_API_KEY=<ключ движка, если требуется>
TRANSCRIPTION_ASR_RESPONSE_FORMAT=verbose_json
TRANSCRIPTION_BROKER_URL=amqp://<user>:<password>@<private-rabbit-host>:5672/<dedicated-vhost>
```

Доступ RabbitMQ — только к отдельному vhost сервиса. Вне доверенной частной сети использовать AMQPS. Адрес движка фиксируется оператором: клиент не может передать произвольный URL. HTTP для локального inference допускается только явным `TRANSCRIPTION_ALLOW_INSECURE_ASR_HTTP=true` и только для внутренних адресов. Это не выбирает движок автоматически.

```sh
docker compose --env-file /etc/hr-vision/transcription.env --profile asr config --quiet
docker compose --env-file /etc/hr-vision/transcription.env --profile asr up -d api worker scheduler
```

API и worker используют один образ, свою БД и свои файлы. Scheduler каждые 30 секунд инициирует восстановление незавершённых загрузок/публикаций. RabbitMQ доставляет задания, но первичное состояние хранится в SQL. Просроченный running job становится uncertain: автоматического повторного ASR-запроса нет. `/retry` — отдельное решение источника; оно может повторить уже выполненное распознавание при неизвестном исходе прошлого запроса.

Изменение URL/модели не подменяет движок старого задания: потребуется новый ключ запроса и новая загрузка. Ранее сохранённые результаты остаются читаемыми.

## Состояние, ограничения и восстановление

- Compose создаёт собственный volume `transcription-data`, смонтированный в `/var/lib/transcription`; процесс работает как UID/GID 10001. В нём SQLite, загруженные байты и расписание Celery. Текст результата хранится в SQLite.
- Это первая конфигурация для **одного хоста**, не кластер высокой доступности. API, worker и scheduler должны видеть один локальный volume. SQLite/WAL не размещать на сетевом диске. Перезапуск контейнера сохраняет данные; потеря хоста без резервной копии — нет.
- Для согласованной резервной копии остановить все три процесса и скопировать volume целиком. При восстановлении вернуть volume с владельцем 10001, затем запустить прежний образ и конфигурацию. Не использовать `down -v` для обычного обновления.
- Автоматическое удаление исходников и результатов пока не включено. Контролировать свободное место; перед введением retention согласовать срок и удаление связанных jobs/blobs. Оборванный процесс загрузки может оставить файл в `staging`; уборка возможна только при остановленном API, чтобы не затронуть текущий upload.
- Вход до 512 MiB, готовый текст до 16 MiB, результат до 20 MiB по умолчанию. Текущие настройки задаются переменными `TRANSCRIPTION_MAX_INPUT_BYTES`, `TRANSCRIPTION_MAX_RESULT_BYTES`, `TRANSCRIPTION_ASR_TIMEOUT_SECONDS`, `TRANSCRIPTION_LEASE_SECONDS`, `TRANSCRIPTION_MAX_ATTEMPTS`; при необходимости добавить их в Compose environment одинаково для API и worker.
- Нет встроенной проверки качества/полноты речи, GPU inference, автоматического выбора провайдера, chunking длинных записей или распознавания ролей. Unknown остаётся unknown; результат не объявляется полным по последнему таймкоду.

Перед live-переключением остаются: сборка образа на целевом хосте, выдача отдельных ключей, проверка source → normalize → core. Перед включением ASR дополнительно нужны реальный RabbitMQ-цикл и сравнение движков на разрешённых интервью. Локальные проверки с тестовым ASR не заменяют эти шаги.

## Отдельный SpeechKit deployment

Существующий normalizer не переключается автоматически. Поднять отдельный Compose project/volume/порт для ASR API, worker и scheduler; broker vhost/очередь выделить этому deployment. Не монтировать его SQLite volume в normalizer. Пример отдельного env (`chmod 600`, значения вне Git):

```dotenv
TRANSCRIPTION_BACKEND_KIND=yandex_speechkit
TRANSCRIPTION_PORT=8094
TRANSCRIPTION_API_KEYS={"transcription-source":"<уникальный ключ>"}
TRANSCRIPTION_BROKER_URL=amqp://<dedicated-vhost-credentials>
TRANSCRIPTION_SPEECHKIT_API_KEY=<service-account-api-key>
TRANSCRIPTION_SPEECHKIT_FOLDER_ID=<folder-id>
TRANSCRIPTION_SPEECHKIT_MODEL=general
TRANSCRIPTION_SPEECHKIT_LANGUAGE=ru-RU
TRANSCRIPTION_SPEECHKIT_SPEAKER_LABELING=false
TRANSCRIPTION_SPEECHKIT_CONFIG_REVISION=1
TRANSCRIPTION_STAGING_S3_BUCKET=<private-staging-bucket>
TRANSCRIPTION_STAGING_S3_PREFIX=speechkit-staging/
TRANSCRIPTION_STAGING_S3_ACCESS_KEY=<object-storage-upload-key>
TRANSCRIPTION_STAGING_S3_SECRET_KEY=<object-storage-upload-secret>
```

Используется boto3 1.43.118 с pinned transitive dependencies. Пустые staging credentials допускают стандартную AWS credential chain; Compose отключает IMDS. Ключ SpeechKit принадлежит сервисному аккаунту с `ai.speechkit-stt.user` и доступом чтения staging объектов (`storage.viewer`); upload/delete credentials имеют доступ только к staging prefix. Бакет приватный: публичное чтение не включать. По [официальной инструкции](https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/transcribation-api-v3) SpeechKit игнорирует query-параметры подписанных ссылок; используется object URI и права сервисного аккаунта, подпись сама по себе доступ не даёт.

```sh
docker compose -p md-speechkit --env-file /etc/hr-vision/speechkit.env --profile asr config --quiet
docker compose -p md-speechkit --env-file /etc/hr-vision/speechkit.env --profile asr build
docker compose -p md-speechkit --env-file /etc/hr-vision/speechkit.env --profile asr up -d api worker scheduler
```

Compose задаёт concurrency=1, CPU/memory/PID limits; образ устанавливает FFmpeg/ffprobe. Требуется место для исходной записи, WAV и минимум полного WAV budget +64 MiB свободного места перед преобразованием. Входные файлы и результаты не удаляются автоматически. Локальный WAV удаляется при выходе из преобразования; после kill временную папку можно убрать при остановленном worker. S3 staging удаляется только после сохранённого `succeeded` с raw+normalized result. Scheduler повторяет неудачное удаление; исходное видео этот код не удаляет. Для abandoned/failed/uncertain staging назначить ограниченный lifecycle отдельно, сохраняя окно разбирательства неопределённых операций. Результат провайдера забирается сразу после done, поскольку он хранится там 3 дня.

При первом запуске автоматически добавляются nullable provider columns в существующую jobs table; данные и HTTP backend сохраняются. Выполнять миграцию до параллельного старта worker/API и делать согласованную backup volume. Известный operation ID переживает expired lease, используя только GET; неизвестный POST outcome остаётся заблокированным. Для его разбора оператор проверяет provider operations/billing и не запускает новую revision без отдельного решения.

`/health/ready` с ключом источника проверяет DB/API и отдельно показывает конфигурацию и свежесть worker heartbeat. Эти поля не подтверждают cloud permissions: до разрешённого теста на одном интервью они остаются непроверенными. Ни один тест этого пакета не отправляет аудио провайдеру и не создаёт платную операцию. Полная приёмка также требует RabbitMQ/reconciliation и source → S3 → Core readback; она выполняется архитектором отдельно.

## Первый live provider: Deepgram

Для отдельного ASR deployment из предыдущего раздела выбрать `TRANSCRIPTION_BACKEND_KIND=deepgram`, задать `TRANSCRIPTION_DEEPGRAM_API_KEY` через защищённый env, `TRANSCRIPTION_DEEPGRAM_MODEL=nova-3`, `TRANSCRIPTION_DEEPGRAM_LANGUAGE=ru`, `TRANSCRIPTION_DEEPGRAM_DIARIZE=true`, `TRANSCRIPTION_DEEPGRAM_DIARIZE_MODEL=v2`, `TRANSCRIPTION_DEEPGRAM_CONFIG_REVISION=1`. S3 staging и SpeechKit credentials этому backend не нужны; локальное безопасное FFmpeg извлечение и оригинальное хранилище остаются общими. Endpoint фиксирован `https://api.deepgram.com/v1/listen`, пользователь не может подменить его URL.

Один POST передаёт WAV и ждёт полный ответ до `TRANSCRIPTION_ASR_TIMEOUT_SECONDS` (по умолчанию 300 секунд). Для разрешённой длинной записи оператор может заранее увеличить timeout. При таймауте, 5xx или потере ответа job становится uncertain: повтор автоматически не отправляется, а `/retry` не снимает блокировку. Ошибки 4xx также не вызывают автоматического POST. После сохранённого raw повторный запуск только разбирает сохранённые данные. Real acceptance с доступным ключом и одним разрешённым интервью выполняет архитектор; fixture тесты не доказывают качество Nova-3 или диаризации.

[Deepgram prerecorded API](https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded), [diarization](https://developers.deepgram.com/docs/diarization), [models/languages](https://developers.deepgram.com/docs/models-languages-overview) — источники протокола и явного выбора параметров. `diarize_model=v2` не отправляется вместе с устаревшим `diarize`; smart formatting, numerals и profanity filter отключены; redaction/entities/summarization не запрашиваются.
