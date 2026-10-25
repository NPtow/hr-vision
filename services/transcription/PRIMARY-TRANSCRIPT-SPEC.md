# Основная расшифровка записи

Потребители читают выбранную версию из Core по ID интервью/встречи. Провайдер распознавания остаётся настройкой ASR; в Core нет правил «предпочитать Deepgram». Все исходные и производные версии сохраняются.

## Контракт

`PUT /v1/bindings/{binding_id}/recordings/{recording_id}/transcript-selection` (source credential):

```
{generation, request_id, expected_revision, status, transcript_id, error_code}
```

- generation — актуальное поколение binding; request_id — стабильный идентификатор задания источника.
- expected_revision — ревизия предыдущего выбора, прочитанная один раз и сохранённая источником ДО первого PUT. При отсутствии выбора — 0.
- status: pending / ready / failed / uncertain. transcript_id обязателен только для ready. error_code — ограниченный машинный код, без внешнего текста.
- Новый request_id разрешён только через CAS expected_revision == current.revision; начинает с pending, увеличивает revision на 1 и снимает прежний основной текст. Повтор того же pending не сбрасывает terminal state.
- Завершение требует тот же request_id, generation и исходный expected_revision. Для завершения ревизия не увеличивается; повтор идентичного terminal состояния идемпотентен. Другой terminal ответ — conflict. Старый request не может заменить новый выбор.
- Запись должна быть video/audio и принадлежать binding; готовый transcript_version должен принадлежать тому же binding и быть подтверждён Core. Проверяются owner, provider/connection principal, revoke/generation. Чужой/устаревший выбор не публикуется.

Ответ и `materials.transcript_selections[]`:

```
{binding_id, generation, recording_id, request_id, revision, status,
 transcript_id, error_code, updated_at}
```

В materials/dossier возвращаются только актуальные неотозванные выборы. GET `/v1/owners/{kind}/{id}/primary-transcripts` возвращает `{owner_kind, owner_id, status, selections, transcripts}`: ровно выбранные immutable versions, без угадывания по имени провайдера. Статусы: not_requested, processing, ready, error; failed/uncertain даёт error, pending — processing. При неизвестной/устаревшей ссылке fail closed.

## Координатор

Новый флаг `TRANSCRIPTION_SOURCE_SELECT_PRIMARY` по умолчанию false, включаем после выпуска Core и потребителей. Собственная job DB хранит selection_requested и selection_revision; новая конфигурация не меняет identity/ASR revision и не запускает повторную оплату.

Повтор `POST /v1/recordings` при включённом флаге может включить выбор для уже завершённого задания: возобновляется только доставка/выбор, сохранённые ASR result/raw/receipt используются повторно. До ASR зарегистрировать pending в Core, после существующего receipt/readback выбрать точный transcript_id. После ошибки ASR публиковать failed/uncertain без автоматического платного повтора. Ошибка доставки повторяет публикацию, а не ASR. Все checkpoints перезапускаемы; новая CAS-ревизия не перечитывается при retry.

## Потребители

CoreMaterialsCatalog отдаёт выбранные версии в основном `transcripts`, альтернативы в `transcriptVariants`; исходный capture текст не подставляется вместо pending/failed выбранной обработки. Источники без явного выбора сохраняют legacy поведение для совместимости; новые задания и семь выбранных интервью переходят на новый контракт. Локальная legacy проекция не становится вторым источником решения: production Zoom использует Core.

Фактическая оценка должна ссылаться на ID/хеш использованной версии. Смена выбранного текста делает прежнюю оценку устаревшей до пересчёта; нельзя показывать её как результат по новой расшифровке. Голоса speaker_0/1 не превращаются в «кандидат/рекрутер» по догадке.

## Приёмка

Unit/API: ownership, revoke/generation, чужая запись/текст, CAS, ACK-loss, поздний ответ старого задания, pending/failed без Zoom fallback. Интеграция: одно paid распознавание, повторная доставка и выбор после рестарта, реальный Core readback.

Живая проверка: семь реальных интервью из текущего набора; готовый Deepgram Багина переиспользуется. Для каждого status/result, raw S3, owner/candidate, выбранный transcript ID, отсутствие повторного paid POST. Новые записи автоматически. Широкий исторический backfill остаётся выключен.
