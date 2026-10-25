# Transcription API v1 — контракт для сервисов источников

Владелец API — отдельный процесс transcription. Сервис источника получает видео/аудио или готовый текст, вызывает этот API и затем доставляет результат в ядро. Transcription не обращается к БД кандидатов, API ядра, Zoom/Daily или временным URL. Вход — байты, не URL. Ядро не должно вызывать ASR.

## Доступ

Все `/v1/*` и `/health/ready` требуют `Authorization: Bearer <service-token>`. Сервер связывает ключ с `client_id` через `TRANSCRIPTION_API_KEYS` (JSON). Ключи разных сервисов различаются; статус и результат чужого задания возвращают 404. Ключи, URL скачивания и provider credentials не входят в job DTO. `/health/live` доступен без ключа. OpenAPI `/openapi.json` содержит только схему API.

## Готовый VTT/SRT/JSON

`POST /v1/normalize?format=vtt` (`srt`, `json`, `canonical_json` также допустимы).

Тело: исходные UTF-8 байты, `Content-Type: application/octet-stream`. Это детерминированный синхронный импорт, без распознавания и без задания. Повтор безопасен. Успех 200 возвращает непосредственно canonical transcript ниже.

Лимит входа 16 MiB; результата по умолчанию 20 MiB. Некорректный/частично повреждённый файл — 422 `invalid_transcript`, превышение входа/выхода — 413. Нельзя заменять такой ответ пустым успешным транскриптом. Нормализация подтверждает корректность переданного текста, не полноту всего интервью.

## Аудио/видео на распознавание

`POST /v1/jobs?language=ru` — необязательный ISO-код языка из 2–3 строчных букв.

- Тело: поток исходного файла, `Content-Type: application/octet-stream`.
- `X-Filename: recording.mp4` — нужен только поддерживаемый суффикс: mp4/m4a/mp3/wav/webm/ogg/flac/mpeg/mpga. В сервисе сохраняется обезличенное `input.<ext>`; пригодность содержимого проверяет выбранный ASR.
- `Idempotency-Key: <stable-source-request-id>` — обязателен, 1–128 ASCII-символов из `A-Z a-z 0-9 _ . : -`. Не использовать имя кандидата. Область ключа — вызывающий сервис.
- Лимит тела по умолчанию 512 MiB, настраивается оператором. Сначала проверяется реальный размер потока и вычисляется SHA-256.

Ответ 202: `id`, `status`, `created`, `attempts`, `created_at`, `updated_at`, `input_sha256`, `input_size`, `language`, `error_code`. Времена — Unix seconds UTC.

Один ключ + одинаковые байты, суффикс, язык и конфигурация движка возвращают прежнее задание; изменённый запрос с прежним ключом — 409 `idempotency_conflict`. Для новой версии распознавания нужен новый ключ. Конфликт не сохраняет новый входной blob.

ASR не выбран по умолчанию. Без настроенных endpoint и model — 503 `asr_not_configured`; задания с выдуманным результатом не создаются. Endpoint задаётся оператором, не клиентом API.

## Состояние и результат

`GET /v1/jobs/{id}` возвращает публичное состояние без текста и внутренних путей:

| status | Значение / действие источника |
|---|---|
| uploading | Намерение принято в БД, сохранение файла ещё не подтверждено. Повторить тот же upload с тем же ключом при незавершённой передаче. |
| queued | Вход сохранён, ожидает worker. Недоступность RabbitMQ не теряет задание; reconciler повторяет публикацию. |
| running | Worker обрабатывает запись под lease. |
| succeeded | Обработка закончилась, нормализованный результат сохранён. |
| failed | Известная ошибка; `error_code` содержит безопасный машинный код. |
| uncertain | Worker исчез или исход внешнего ASR POST неизвестен. Автоматического повторного платного вызова нет. |

`GET /v1/jobs/{id}/result`: 409 `result_not_ready` до succeeded; затем 200:

```json
{
  "job_id": "uuid",
  "input_sha256": "sha256",
  "processing_status": "completed",
  "coverage": "not_independently_verified",
  "language": "ru",
  "model": "configured-model-id",
  "backend_identity": "sha256-of-config",
  "transcript": {
    "schema_version": 1,
    "format": "json",
    "segments": [{"start_ms": 0, "end_ms": 1500, "text": "Пример", "speaker": null}],
    "full_text": "Пример",
    "text": "Пример",
    "vtt": "WEBVTT\n\n...",
    "warnings": ["missing_speakers"]
  }
}
```

`language` может быть null: это известный запросу язык, а не выдуманное автоопределение. Model фиксируется для задания. `processing_status=completed` означает завершённую обработку входа, но не гарантирует распознавание каждого произнесённого слова. `coverage` честно остаётся непроверенным. Последний таймкод речи не используется как доказательство полноты видео.

Canonical transcript: времена — целые миллисекунды, начало не позже конца; неизвестные времена — оба null, неизвестный говорящий — null. Текст не пустой, segments не пустой. Сервис не угадывает роли кандидат/рекрутер. `full_text`/`text` соответствуют segments. Warnings сохраняются при доставке в ядро. Пустая/повреждённая выдача ASR не считается успешной.

`POST /v1/jobs/{id}/retry` — явное решение вызывающего сервиса повторить failed/uncertain; 202 либо 409. Для SpeechKit/Deepgram с сохранённым submit intent без operation ID или сохранённого raw ответа повтор запрещён (409), включая явный API retry. Для известного operation ID retry выполняет только GET. Для uncertain прошлый ASR мог выполнить запрос, поэтому повтор не обещает exactly-once. Изменённая конфигурация движка — 409; использовать новый ключ и новую загрузку.

## Ошибки и повтор

401 — неверный ключ; 404 — нет своего задания; 413 — превышен лимит; 422 — исправить вход; 409 — конфликт/неподходящее состояние. 5xx — инфраструктурная проблема, можно повторить HTTP-запрос с тем же ключом. При потере ответа upload используется тот же ключ. Текст исключений и ответы провайдера не выдаются наружу.

При ASR 429 действует ограниченный backoff. Таймаут или неоднозначный 5xx после отправки в ASR помечается uncertain; Idempotency-Key передаётся движку, но сервис не предполагает, что любой движок его поддерживает.

## Граница интеграции

Рабочий клиент источника: upload → poll → result → core ingest с собственным idempotency key. Готовый provider transcript: normalize → core ingest. Получение нормализованного результата не означает его доставку в ядро — эту доставку и повтор контролирует сервис источника. Прямых SQL-записей в candidate DB из transcription нет.

В этой ветке есть временный HTTP bridge старого provider collector (`interface-lab/server/materials_transcripts.py`). Для нового независимого source-service использовать этот контракт; импортировать пакет transcription в ядро не требуется.

## SpeechKit result/raw

Существующий `/result` сохраняет `transcript` без новых полей внутри NormalizedTranscript. Для SpeechKit в envelope добавлены:

```json
{
  "provider": "yandex_speechkit",
  "provider_metadata": {
    "configuration": {"model": "general", "language": "ru-RU", "speaker_labeling": false, "config_revision": "1"},
    "provenance": {"input_sha256": "sha256", "derived_audio_sha256": "sha256", "duration_ms": 1000, "timing_offset_ms": null, "timing_reference": "decoded_first_audio_stream", "timing_alignment_to_original_media": "not_verified"},
    "provider_channel_labels": ["0"]
  },
  "raw_result_url": "/v1/jobs/uuid/result/raw"
}
```

`provider_channel_labels` соответствует порядку нормализованных сегментов, может содержать null; метки не преобразуются в роли или имена. Наличие двух меток не доказывает отсутствие третьего участника. `coverage` всегда `not_independently_verified`.

`GET /v1/jobs/{id}/result/raw` использует тот же Bearer/owner ACL, возвращает 409 до готовности, 404 при чужом job или отсутствии raw у прежнего HTTP backend. JSON envelope:

```json
{
  "schema_version": 1,
  "provider": "yandex_speechkit",
  "job_id": "uuid",
  "input_sha256": "original-media-sha256",
  "operation_id": "provider-operation-id",
  "model": "general",
  "language": "ru-RU",
  "configuration": {},
  "provenance": {},
  "warnings": ["speaker_labeling_disabled"],
  "provider_channel_labels": ["0"],
  "coverage": "not_independently_verified",
  "events": [{"result": {"final": {"alternatives": []}}}]
}
```

`events` сохраняет исходные JSON-объекты provider NDJSON (включая исходную обёртку `result`), а не только выбранный текст. Source сохраняет этот JSON целиком в свой materials S3. Envelope не содержит staging URI, подписей или ключей. `/result/raw` является чувствительным содержимым интервью, как и `/result`, и не используется в логах. Provider WARNING становится ограниченным `speechkit_provider_warning`; исходное сообщение остаётся только в raw. Ошибка в stream после finals не публикует частичный результат как успех.

Статус `/health/ready` сообщает отдельно `api`, `asr_configuration`, `worker.status`/`last_seen_at`. `worker.status=observed` означает heartbeat процесса за последние 90 секунд, без доказательства успешного распознавания. `run-once` не имитирует постоянно работающий worker.

## Deepgram result/raw

Envelope результата одинаков по базовым полям. `provider=deepgram`, модель `nova-3`, язык исходного запроса `ru`; `provider_metadata.configuration` фиксирует все параметры запроса, revision, модель diarization. `provider_metadata.provider_model_info` и `provider_diarize_info` сохраняют metadata провайдера, при отсутствии — null. Изменение модели, языка, diarization или revision изменяет backend identity.

Raw envelope содержит `provider=deepgram`, `operation_id=null` (синхронный prerecorded API), `provider_request_id` при наличии, `events=[<исходный Deepgram JSON>]`, `configuration`, `provenance`, warnings и `coverage=not_independently_verified`. Исходные channels/words/utterances/model_info/diarize_info не выбрасываются из raw. Raw доступен после durable checkpoint даже если canonical parsing затем завершился ошибкой: этот материал можно исследовать и повторно разобрать без нового POST. При невалидном JSON оригинальные bytes хранятся в `unparsed_response_base64`, `events=[]`; это не успешная расшифровка.

В canonical transcript Deepgram использует слова основной alternative, проверяет, что текст полностью соответствует их проекции, сохраняет `punctuated_word` и миллисекунды, группирует по speaker transitions и ограниченным фрагментам. `speaker_N` — метка модели, не имя/роль. `deepgram_speaker_labels_missing` означает отсутствие части запрошенных speaker labels; `speaker=null` остаётся unknown. Это не подтверждение точности диаризации или полноты распознанной речи.
