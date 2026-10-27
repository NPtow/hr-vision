# Архив невыпущенного Docker/source-daily решения

**Текущий путь: [simple/README.md](simple/README.md), systemd + core + stateless
Zoom + normalization API. Этот прежний scaffold сохранён для истории; не запускать.**

Подготовка артефактов и эксплуатационные команды. Эти файлы сами ничего не
выкатывают. Docker daemon на машине разработки отсутствует; локально проверяются
hash-lock, выбранные исходники, воспроизводимость архивов и Compose configuration.
Сборка образов, RabbitMQ, реальные S3/Daily и production acceptance проверяются
отдельно оператором. Не считать health доказательством готового материала.

## Границы и ресурсы

Core и source используют одинаковый `requirements-runtime.lock`: union точных
runtime-зависимостей двух pyproject, Python 3.12.13 и frozen digest базового образа.
Хеши включают Linux CPython wheels; контроль установки сделан для Linux x86_64.
Lock для нормализации принадлежит `services/transcription` и выпускается отдельно.
Обновление любой зависимости требует нового lock и review двух манифестов.

Core получает только `/var/lib/hr-vision-api` для каталога и чтения trusted owner
bridge. Его UID/GID **совпадают с владельцем существующей приватной HR SQLite**:
у WAL-файлов сохраняется доступ при UMask 0077. Не расширять chmod 0700 каталога
или 0600 БД, не менять владельца HR state ради контейнера. Проверить на хосте
`stat -c '%u:%g %a %n' /var/lib/hr-vision-api /var/lib/hr-vision-api/state.sqlite3`
и записать реальные UID/GID в `compose.env`. Это существующий непривилегированный
сервисный пользователь HR; source работает отдельно как 19402:19402.

Source имеет только собственный bind `/var/lib/md-source-daily` → `/state`:
`jobs.sqlite3`, WAL и временные файлы находятся вне release. Доступа к HR SQLite,
каталогу API и Docker socket у source нет. Он обращается к core только по HTTP.
Core API 127.0.0.1:8401, source API 127.0.0.1:8402. Host network выбран только для
Linux, чтобы достичь существующего RabbitMQ на 127.0.0.1 и normalization на 8093.
Портов наружу и нового RabbitMQ контейнера здесь нет.

| Процесс | Память / swap cap | CPU | PID | Пользователь |
| --- | --- | --- | --- | --- |
| core | 256 MiB / 256 MiB | 1 | 128 | существующий HR DB UID:GID |
| source API | 256 MiB / 256 MiB | 1 | 128 | 19402:19402 |
| source worker, concurrency=1 | 512 MiB / 512 MiB | 1 | 192 | 19402:19402 |
| source reconcile | 128 MiB / 128 MiB | 0.25 | 128 | 19402:19402 |
| normalization API | 256 MiB / 256 MiB | 1 | 128 | 10001:10001 |

Все контейнеры: read-only root, cap_drop ALL, no-new-privileges, init, ограниченный
64 MiB tmpfs `/tmp`, json-file logs 3×10 MiB. Лимиты — верхние границы, не
резервирование памяти. На хосте 4 GiB проверить свободную память и swap после
каждого запуска; остановить rollout при OOM/restart вместо повышения лимитов вслепую.

Видео до 2 GiB пишется потоково в `/state/tmp` на диске, не в `/tmp` tmpfs. Перед
запуском проверить `df -h /var/lib/md-source-daily`, выделить дисковую квоту/отдельный
том с запасом минимум на две копии максимального файла плюс state и рост WAL.
Docker bind mount сам не ограничивает объём диска. Не включать tmp-cleaner для
`/state/tmp` при работающих jobs. Нормализация готового VTT не включает ASR.
Будущий ASR по умолчанию принимает только 512 MiB: сначала согласовать ограничения
всех участников и фактическое потребление памяти, затем отдельно разрешить ASR.

## Подготовка воспроизводимых артефактов (без Docker daemon)

Из корня репозитория, Python ≥3.11 (предпочтительно тот же 3.12):

```sh
uv pip compile services/data-core/pyproject.toml services/source-daily/pyproject.toml \
  --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
  --only-binary :all: --generate-hashes --no-header --no-annotate \
  -o services/deploy/requirements-runtime.lock
python3.12 -m unittest discover -s services/deploy/tests -v
python3.12 services/deploy/artifact.py check core
python3.12 services/deploy/artifact.py check source
python3.12 services/deploy/artifact.py prepare core --output-root /srv/md-materials-build
python3.12 services/deploy/artifact.py prepare source --output-root /srv/md-materials-build
```

Повторная компиляция lock — операция обновления зависимостей, не обычный build.
Для обычного build использовать уже reviewed lock. При осознанном изменении
исходников сначала review diff, затем `artifact.py record core` или `record source`
и review diff JSON-манифеста. prepare откажет при изменении/добавлении модуля,
symlink, небезопасном output path или расхождении pins. На macOS использовать
канонический `/private/tmp/...`: `/tmp` является symlink и отклоняется.

Каждый JSON результата содержит `image`, `artifact`, SHA-256 архива и манифеста.
Архив содержит только выбранные pyproject/src, Dockerfile, hash-lock и контрольные
суммы; core дополнительно содержит ровно четыре чистых compatibility-модуля
materials_repository, materials_storage, domain, feedback. Portal runtime,
провайдерские ключи, `.env`, БД, tests и `/opt/hr-vision-api/current` не входят.
Tar/gzip metadata фиксированы; per-service flock предотвращает параллельную
подмену артефактов. Имена образов независимы: `md-materials-core:VERSION-HASH12`
и `md-materials-source:VERSION-HASH12`, не общий HR API release.

## Сборка образов на выделенной build-машине

Подставить **точные** значения из JSON prepare, проверить SHA до Docker build:

```sh
printf '%s  %s\n' '<artifact-sha256>' '<absolute-artifact.tar.gz>' | sha256sum --check --strict
docker build --platform linux/amd64 --tag 'md-materials-core:<version>-<hash12>' - < '<core-artifact.tar.gz>'
docker build --platform linux/amd64 --tag 'md-materials-source:<version>-<hash12>' - < '<source-artifact.tar.gz>'
```

Не использовать `docker build .` из корня HR-проекта. Dockerfile проверяет все
скопированные файлы через SHA256SUMS до установки hash-pinned wheels. Сохранять
образ и его immutable image ID в release inventory, при переносе проверять SHA
файла `docker save` перед `docker load`. Не перезаписывать существующий version/hash
tag другим образом. ASR/normalization образ независимо готовит его packager;
production override принимает `md-transcription:0.1.0-<source-hash12>` и никогда
не делает build и не использует mutable `:local` из development compose.

## Настройка хоста (оператор, секреты вне Git)

Разместить reviewed содержимое этой папки в отдельном control release
`/opt/md-materials/control-releases/<control-id>` и ссылку `/opt/md-materials/deploy`.
Это собственный указатель, независимый от `/opt/hr-vision-api/current`. Старый
control release и образы сохранить для отката. Файлы должны принадлежать root.

```sh
install -d -m 0700 /etc/md-materials
install -d -m 0700 -o 19402 -g 19402 /var/lib/md-source-daily /var/lib/md-source-daily/tmp
install -d -m 0700 -o 10001 -g 10001 /var/lib/md-transcription
```

Подготовить `compose.env`, `core.env`, `source.env`, `transcription.env` по
examples, mode 0600 и owner root. Каталог mode 0700. Генерировать три разных
случайных bearer ключа portal→core, portal→source, source→core; отдельный
source→normalization ключ. Не выводить их в чат/логи и не передавать аргументами
команд. `ctl.py` читает буквальные env-файлы, не shell-source; не принимает
override путей, bind адресов, общей Rabbit vhost или placeholder значений.
Render Compose с секретами не печатать: wrapper всегда использует config --quiet.

RabbitMQ должен **уже** слушать только localhost. Создать только новую identity
`md-source-daily` и vhost `source-daily`, не менять listener/пользователей других
чатов. Передавать пароль через согласованный защищённый операторский канал.
После создания пользователя команды ограничиваются новым vhost:

```sh
rabbitmqctl add_vhost source-daily
rabbitmqctl set_permissions -p source-daily md-source-daily '.*' '.*' '.*'
rabbitmqctl list_permissions -p source-daily
```

Не повторять add_vhost, если он уже существует: сначала проверить владельца и
назначение. URL source: `amqp://md-source-daily:<urlencoded-password>@127.0.0.1:5672/source-daily`.
Пароль не включать в history; root-managed env-файл создаётся приватным редактором.

Normalizing source использует `SOURCE_DAILY_TRANSCRIPTION_URL=http://127.0.0.1:8093`
и собственный `SOURCE_DAILY_TRANSCRIPTION_TOKEN`. Source host network видит
host-loopback, а normalization API публикует только 127.0.0.1:8093. Начальные
`SOURCE_DAILY_ENABLE_ASR=false` и пустой DAILY_TRANSCRIPTION_MODEL сохраняются.
В normalization env разрешён только TRANSCRIPTION_API_KEYS: worker/scheduler,
ASR URL/model/key и broker для первого запуска отсутствуют.

При переносе существующего Daily webhook передать прежний HMAC secret как
`SOURCE_DAILY_DAILY_WEBHOOK_HMAC` в source.env и направить исходное raw тело и
подпись в `/v1/webhooks/daily` через авторизованный portal proxy. Сам endpoint
source остаётся loopback; старый обработчик не должен параллельно обрабатывать
то же событие новым и legacy capture путями. HMAC не заменяет service bearer.

```sh
python3 /opt/md-materials/deploy/ctl.py validate
python3 /opt/md-materials/deploy/ctl.py config
python3 /opt/md-materials/deploy/ctl.py --stack transcription validate
python3 /opt/md-materials/deploy/ctl.py --stack transcription config
install -m 0644 /opt/md-materials/deploy/hr-vision-data-services.service /etc/systemd/system/
install -m 0644 /opt/md-materials/deploy/hr-vision-transcription.service /etc/systemd/system/
systemctl daemon-reload
```

## Безопасная остановка, backup и rollout

Перед копированием HR SQLite остановить **всех** писателей. Одной остановки
старого materials-worker недостаточно: core добавляет catalogue rows, portal
записывает завершения/outbox, dispatcher создаёт bindings. Использовать общий
drain receiver-пакета HR API: он знает фактические unit names и остановит portal
API, старый worker, outbox dispatcher и `hr-vision-data-services.service`.
До drain перевести входящий трафик/активные звонки в согласованное maintenance
окно. Не выключать consent и не считать активный звонок завершённым ради релиза.

Если выполняется ручной drain, порядок такой: остановить portal producers и
outbox dispatcher, затем старые workers; остановить source-api/reconcile,
дождаться warm shutdown source-worker, только потом core:

```sh
python3 /opt/md-materials/deploy/ctl.py stop source-api source-reconcile
python3 /opt/md-materials/deploy/ctl.py stop source-worker
python3 /opt/md-materials/deploy/ctl.py stop core
python3 /opt/md-materials/deploy/ctl.py status
```

Убедиться, что API/legacy-worker/outbox units остановлены и нет SQLite writers.
Затем сделать SQLite online backup в отдельный backup-каталог с уникальным
именем (не поверх предыдущего). Сохранять HR DB, source private DB и конфигурацию
согласованно. При остановленных source процессах использовать SQLite `.backup`
для `/var/lib/md-source-daily/jobs.sqlite3` и отдельную копию retained disk files.
Normalization API остановить отдельным unit и сохранить его state-каталог, если
нужна согласованная полная точка восстановления. Не удалять WAL/SHM вручную.
Не делать `cp` одной работающей SQLite БД без WAL и не использовать `down -v`.

После backup выбрать exact immutable images в приватном compose.env. Запуск:

```sh
systemctl start hr-vision-transcription.service
python3 /opt/md-materials/deploy/ctl.py up core source-api
curl --fail http://127.0.0.1:8401/health/ready
curl --fail http://127.0.0.1:8402/health/live
curl --fail http://127.0.0.1:8093/health/live
python3 /opt/md-materials/deploy/ctl.py up source-worker source-reconcile
python3 /opt/md-materials/deploy/ctl.py status
```

Source ready endpoint требует bearer. Проверить его, normalization ready и
synthetic VTT→normalize→core реальным authenticated клиентом без echo ключей.
Проверить readable historical catalogue IDs/версии, новый owner binding,
durable source job, receipt retry, независимость video/transcript состояний и
S3/remote access. Только после этого возобновить portal/outbox через общий
receiver; feature flags включает отдельный HR API release. Outbox хранится в
`portal_materials_outbox`, не создавать второй completion транзакционный путь.
После полной проверки `systemctl start hr-vision-data-services.service` закрепляет
активное состояние systemd wrapper; enable units выполнять только после acceptance.

При обычном независимом обновлении core изменить только CORE_IMAGE и выполнить
`ctl.py up core`. Для source обновить SOURCE_IMAGE и выполнить `ctl.py up
source-api source-worker source-reconcile`. Для normalize изменить только
TRANSCRIPTION_IMAGE и выполнить `ctl.py --stack transcription up api`.
Перед image update сверять backup/migration совместимость; image rollback не
равен восстановлению БД. Не возвращать прежний legacy worker, пока новый source
или outbox публикует ту же работу.

SIGTERM даёт Uvicorn 90s, Celery warm shutdown до 300s. При принудительном kill
durable jobs и pending receipt остаются в private SQLite; reconcile повторяет
доставку с теми же receipt_id после lease expiry (по умолчанию 600s). Проверить
очередь и состояния до ручного retry; не утверждать ASR success после uncertain
ответа. Не чистить `/state/tmp` или очередь, чтобы «разблокировать» работу.
При откате сначала остановить dispatcher/source workers/core, выбрать прежние
образы и control release, проверить совместимость схемы; восстанавливать
согласованный backup только в maintenance окне со всеми писателями остановленными.
