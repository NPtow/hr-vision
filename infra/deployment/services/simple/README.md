# Текущий простой путь: core + stateless Zoom

Использовать эту папку. Родительские Docker/source-daily scaffolds — сохранённая
история невыпущенного решения; их units/Compose/env не устанавливать и не запускать.
Новый путь не создаёт source-daily DB, receipt outbox, queue, worker или scheduler.
Существующие materials worker/scheduler и одна очередь `materials` продолжают
работать из нового HR API release. Новые процессы — только три HTTP API:

| Unit | Адрес | Runtime | Доступ |
| --- | --- | --- | --- |
| hr-vision-core | 127.0.0.1:8093 | существующая materials venv | HR DB/catalogue, storage, HTTP Zoom |
| hr-vision-normalizer | 127.0.0.1:8094 | отдельная transcription venv | upstream normalization state, без ASR/broker |
| hr-vision-zoom | 127.0.0.1:8095 | существующая materials venv | Zoom secrets и временный disk cache |

Core работает как существующий hr-vision (на обследованном хосте UID997/GID987),
с приватными DB0700/0600 и UMask0077. Права HR state не расширять. Zoom DynamicUser
не получает HR DB, AWS или Rabbit credentials. `run.py` очищает окружение по роли:
core не получает Zoom OAuth/SDK secrets, Zoom — HR DB/AWS/broker, normalizer — ASR.
Core collector лишь сохраняет задание в общей HR SQLite; выполняет его прежний
worker. Пакет включает рекурсивное замыкание pure доноров, в том числе lazy imports;
`services/recruiting-api/app.py` запрещён. Импорт не запускает workers.

Нормализатор выпускает существующий компонент `services/transcription` (Верстак2),
только команду API. Его штатная приватная БД/состояние не являются второй source
pipeline. Не запускать ASR worker, beat, новый broker/vhost или выбор модели.

Все units ограничены CPU100%, memory256MiB, swap0, tasks64/128, journal rate limit.
Глобальную политику хранения journal не менять ради этого релиза; проверить её
существующий лимит на хосте. Zoom — один Uvicorn process и один download semaphore;
до2GiB на диск `/var/cache/hr-vision-zoom`, не tmpfs `/tmp`64MiB. До запуска проверить
диск; нужен минимум4GiB свободного места/квота cache-раздела. При аварии могут остаться
незавершённые временные файлы; удалять их только при остановленном Zoom unit.

## Локальная проверка и упаковка

Из корня worktree, после завершения правок доноров:

```sh
python3.12 -m unittest discover -s infra/deployment/services/simple/tests -v
python3.12 infra/deployment/services/simple/package.py record core
python3.12 infra/deployment/services/simple/package.py record zoom
python3.12 infra/deployment/services/simple/package.py record normalizer
git diff -- infra/deployment/services/simple/manifests
python3.12 infra/deployment/services/simple/package.py check core
python3.12 infra/deployment/services/simple/package.py check zoom
python3.12 infra/deployment/services/simple/package.py check normalizer
python3.12 infra/deployment/services/simple/package.py prepare core --output-root /private/tmp/hr-host-services
python3.12 infra/deployment/services/simple/package.py prepare zoom --output-root /private/tmp/hr-host-services
python3.12 infra/deployment/services/simple/package.py prepare normalizer --output-root /private/tmp/hr-host-services
```

JSON результата содержит release ID, путь и SHA256 архива. Архив воспроизводим,
с per-service flock и точным манифестом исходников. `.env`, HR DB, секреты, tests,
portal app и весь repository root не входят. На Linux выбрать другой абсолютный
output вне checkout; symlink/traversal отвергаются. `record` — только после review
кода; обычная упаковка использует check/prepare и не переписывает манифесты.

## Команды оператора: подготовка, без автоматического запуска

Перед переносом подтвердить актуальный HR API release и совместимость доноров.
На целевом хосте проверить, что /opt/hr-vision-materials/venv доступна DynamicUser
на чтение/исполнение. Для core/Zoom использовать существующий runtime lock
`infra/deployment/services/requirements-runtime.lock`. Для normalization — собственный
`services/transcription/requirements.lock` и venv `/opt/hr-vision-transcription/venv`.
Не устанавливать оба lock в одну venv: python-dateutil pins расходятся
(2.9.0.post0 и3.0.0). Оператор проверяет exact versions/pip check до rollout;
не переустанавливать работающую materials venv вслепую.

Для каждого скачанного архива выполнить trusted `verify.py` с SHA из локального
JSON, затем распаковать в **новый** release directory, не в работающий current:

```sh
python3 infra/deployment/services/simple/verify.py '<archive.tar.gz>' '<sha256>'
install -d -m 0755 '/opt/hr-vision-materials/services/<service>/releases/<release-id>'
tar -xzf '<archive.tar.gz>' --no-same-owner -C '/opt/hr-vision-materials/services/<service>/releases/<release-id>'
cd '/opt/hr-vision-materials/services/<service>/releases/<release-id>'
sha256sum --strict --check SHA256SUMS
```

Создать `/etc/hr-vision-services`0700 и четыре отдельные root-owned env-файла0600
по examples: core.env, zoom.env, normalizer.env и runtime.env. Секреты генерировать/
переносить приватным каналом, не через argv/logs/chat. В Zoom сохранить имеющиеся
OAuth/SDK значения; **убрать эти ключи из прежнего API/worker окружения**. Drop-in
дополнительно unset этих пяти переменных. Zoom selection включать отдельным
HR_ZOOM_ENABLED только после проверки hosts/credentials; шаблон оставляет0.

Runtime flags: `HR_CORE_COLLECTOR_ENABLED=1`, `HR_CORE_CATALOG_ENABLED=1`,
`HR_MATERIALS_PROVIDERS=daily,zoom` для существующих общих workers,
`HR_MATERIALS_BACKFILL_ENABLED=1`, URL/token для core,
Zoom8095 и normalizer8094. Это **не** `HR_MATERIALS_SERVICES_ENABLED`. Сохранить
прежний broker/queue workers, параметры Daily и весь HR DB state. Root integration
проверяет provider scope: отдельный core.env имеет `HR_MATERIALS_PROVIDERS=zoom`,
общий worker сохраняет оба провайдера, чтение/обработка Daily продолжаются.
Новый Zoom collector не запускает параллельный Daily acquisition.

После финальной остановки писателей, SQLite backup и проверки runtime оператор
атомарно переключает independent current ссылку каждого сервиса на reviewed release.
Указатели `/opt/hr-vision-api/current` и service current меняются только в рамках
общего согласованного rollout. Установить три unit из этой папки; `runtime-env.conf`
ставится drop-in **только** в фактические существующие API/worker/scheduler units.
Не создавать ещё один materials-worker. Проверить units перед daemon-reload:

```sh
systemd-analyze verify /etc/systemd/system/hr-vision-core.service /etc/systemd/system/hr-vision-zoom.service /etc/systemd/system/hr-vision-normalizer.service
systemctl daemon-reload
```

При одобренном rollout и согласованном окне запуска:

```sh
systemctl start hr-vision-normalizer.service hr-vision-zoom.service hr-vision-core.service
curl --fail http://127.0.0.1:8093/health/ready
curl --fail http://127.0.0.1:8094/health/live
curl --fail http://127.0.0.1:8095/health/live
ss -ltnp '( sport = :8093 or sport = :8094 or sport = :8095 )'
```

Проверить bearer ready/config без вывода ключей, synthetic VTT→normalize, authenticated
core collect→existing durable material job, затем один worker→Zoom→storage→read.
Health не доказывает запись/транскрипцию; реальные Zoom OAuth permissions, host
licence/SDK join и исторический каталог проверяются отдельно root acceptance.
Наблюдать RSS/OOM/диск, не расширять лимиты автоматически. После успеха возобновить
существующие portal/worker/scheduler и включить units на загрузку.

Перед backup/rollback остановить portal producers, существующий worker/scheduler
и core (он тоже писатель HR DB), дождаться завершения работ; только затем SQLite
backup. Новый Zoom API не хранит workflow и не требует backup DB. Нормализатор
имеет своё штатное состояние; сохранять его отдельно при полном восстановлении.
Не возвращать legacy collector одновременно с новым. При откате вернуть прежние
service/API current ссылки и env под остановленными писателями; image/source
rollback не равен SQLite restore. Не менять текущие релизы и не стартовать units
в рамках одной лишь подготовки пакета.
