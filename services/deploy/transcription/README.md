# Независимый ASR и доставка в ядро

Это дополнение к действующим Core8093 / normalizer8094 / Zoom8095. Оно не меняет
их код, не превращает normalizer в платный ASR и не требует общего выпуска UI.
Текущий выбранный провайдер — Deepgram; SpeechKit остаётся отдельным адаптером.

Два сервиса, у каждого API, worker и scheduler. Compose использует один проверенный
образ с двумя пакетами; любой процесс можно выпускать отдельно, API-контракты
независимы. Приватные API слушают loopback8106 и8107. Контейнеры без root/capabilities,
с memory/CPU/PID/log limits. Host networking нужен для существующих loopback Core
и RabbitMQ. ASR не получает S3/HR/Core credentials при Deepgram; координатор не
получает ключ Deepgram или доступ к HR SQLite.

## Подготовка

1. В отдельном окружении установить lock и запустить unit/integration tests.
   `uv pip compile services/deploy/transcription/requirements.in --generate-hashes
   --python-version 3.12 -o services/deploy/transcription/requirements.lock`
2. Build context — только Dockerfile, requirements.lock, `asr/` из
   `services/transcription/src` и `source/` из `services/transcription-source/src`.
   Добавить SHA256SUMS и записать image ID. Ни .env, ни интервью в образ не попадают.
3. На outreach проверить свободные8106/8107, минимум4GiB диска, RAM и существующий
   RabbitMQ. Создать отдельный vhost/user для двух очередей. Пароль через stdin/
   защищённый файл; не включать secret в argv или журналы.
4. `/etc/hr-vision-asr` root0700, env0600. Состояние `/var/lib/hr-vision-asr` и
   `/var/lib/hr-transcription-source` UID/GID10001, mode0700. Сохранять вне release.
5. В Core добавить отдельные source credentials с точными provider/connection и
   отдельный portal credential координатора, сохранив существующие principals.
   Смена env требует перезапуска Core; до него проверить отсутствие активных встреч
   и jobs, сделать SQLite online backup и сохранить предыдущий env. Не менять
   `/opt/hr-vision-api/current` и не обходить live-meeting guard.

## Конфигурация

ASR env: `TRANSCRIPTION_BACKEND_KIND=deepgram`, `TRANSCRIPTION_DEEPGRAM_API_KEY`,
`TRANSCRIPTION_DEEPGRAM_MODEL=nova-3`, `TRANSCRIPTION_DEEPGRAM_LANGUAGE=ru`,
`TRANSCRIPTION_DEEPGRAM_DIARIZE=true`, `TRANSCRIPTION_DEEPGRAM_DIARIZE_MODEL=v2`,
`TRANSCRIPTION_API_KEYS={"source":"unique bearer"}`, `TRANSCRIPTION_BROKER_URL`,
`TRANSCRIPTION_DATA_DIR=/var/lib/transcription`, `TRANSCRIPTION_MAX_INPUT_BYTES=1073741824`,
`TRANSCRIPTION_ASR_TIMEOUT_SECONDS=900`. Другие параметры — в ASR.env example.

Source env: все поля из `services/transcription-source/README.md`; Core URL8093,
ASR URL8106, `TRANSCRIPTION_SOURCE_ASR_PROVIDER=deepgram`,
`TRANSCRIPTION_SOURCE_CONFIG_REVISION=deepgram-v1`, exact S3 hostname allowlist,
материальный S3 bucket/prefix как у Core. Отдельные AWS credentials и broker URL.
Ключ source ingress отличается от source→ASR и source→Core credentials.

Смена API-ключа того же провайдера не меняет идентичность задания. Переход на
другого провайдера — смена адаптера/config revision, после завершения старых jobs
или в независимом ASR экземпляре. Нельзя просто заменить Deepgram key на SpeechKit
key: API-протоколы разные. Исходные транскрипты остаются неизменяемыми версиями.

## Приёмка и включение

Сначала поднять новые процессы без автоматического backfill. Проверить API,
аутентифицированные ready и свежие heartbeats обоих workers. Через source API
отправить IDs одного разрешённого готового интервью. Дождаться отдельно ASR
`succeeded` и доставки `succeeded`; прочитать Core materials/dossier, проверить
новый transcript ID, raw S3 SHA/size, таймкоды и сохранность исходного Zoom текста.
Повтор того же события должен вернуть тот же job и те же transcript IDs.

Для выбранного существующего интервью на сервере есть API-клиент `interview.py`:
`sudo python3 interview.py start interview_ID` возвращает job ID;
`sudo python3 interview.py status JOB_ID` показывает состояние доставки;
`sudo python3 interview.py materials interview_ID` читает сохранённые версии.
Если готовых записей несколько, указать `--asset-id`. Клиент берёт credentials
из защищённых env, не читает HR DB и не вызывает ASR напрямую. Вывод `materials`
содержит персональные данные: хранить приватно, не добавлять в Git или журналы.

Автоматические новые записи: узкий `materials_asr_dispatch` hook; выпускать его
только в существующий materials worker после сверки актуального release, без
отката чужих изменений. `HR_MATERIALS_ASR_ENABLED=1`, URL8107, отдельный ingress
token, provider allowlist, `HR_MATERIALS_ASR_ACTIVATED_AT` равен времени включения.
Это не запуск всей истории. Hook хранит durable подтверждения в отдельной SQLite,
до3уведомлений/tick, timeout3s. Координатор ждёт завершённого сбора перед оплатой.

Откат: выключить hook, дождаться текущих оплаченных запросов и доставки; остановить
только новые процессы. Сохранить обе DB и raw S3. В `uncertain` не повторять платный
POST автоматически: сначала проверить исход у провайдера. Откат image не удаляет
уже сохранённые transcript_versions. Старые Core/Zoom/normalizer продолжают работу.

Ограничения v1: один хост/SQLite, без кластерного failover; remote-only видео требует
конкретный source resolver (универсальный HTTP-прокси намеренно отсутствует).
Первый audio stream сводится в mono; исходные дорожки и привязка имён говорящих не
восстанавливаются догадками. Проверка качества ASR отдельно от технической доставки.
