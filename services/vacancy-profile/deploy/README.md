# Release and operations

Owner: Ядро. Runtime Python 3.10+ stdlib; optional existing loopback SOCKS
requires curl >=8.4. No access to HR DB or candidate process is required.

Build an artifact from the checkout; this copies the exact shared client and
catalog into the artifact and writes SHA-256 manifests:

```sh
python3 services/vacancy-profile/deploy/build_release.py /absolute/path/to/new-release
cd /absolute/path/to/new-release
python3 -c 'import vacancy_profile.api, jev_client'
```

The whole directory is the release. Never copy only vacancy_profile or point a
release at the live monorepo common module. Archive/retain the preceding release
and its manifest. Do not modify a published release in place.

Provision a private mode0600 environment file at
`/etc/hr-vision/vacancy-profile.env` using an authorized secret store, containing
`VACANCY_PROFILE_SERVICE_TOKEN` (64 hexadecimal characters),
`VACANCY_PROFILE_JEV_API_KEY`, `VACANCY_PROFILE_MODEL=jev-1.14.0`,
`VACANCY_PROFILE_PORT=8108`,
`VACANCY_PROFILE_DB=/var/lib/hr-vision-vacancy-profile/runs.sqlite3` and optional
`VACANCY_PROFILE_JEV_PROXY=socks5h://127.0.0.1:PORT`. No shell argv or log should
contain key/token values. The HR server must separately receive the service URL,
token and expected model/catalog through its private configuration. Browsers
never receive the service token.

For authorized install/update, place the complete artifact at
`/opt/hr-vision-vacancy-profile/releases/COMMIT/`, install the supplied unit,
then atomically select `current`. First stop command acceptance/routing in the
HR adapter, check authenticated `/v1/diagnostics`, drain queued/sent work and
stop with `systemctl stop hr-vision-vacancy-profile`; SIGINT waits for the
current provider request up to its 120s deadline. SQLite belongs only to this
service and one process obtains an exclusive runtime lock. Never run a second
worker/CLI against the same database.

Before changing `current`, snapshot the stopped SQLite into a new private file:

```sh
cd /opt/hr-vision-vacancy-profile/current
python3 -m vacancy_profile.ops backup --db /var/lib/hr-vision-vacancy-profile/runs.sqlite3 --destination /private/backup/new-snapshot.sqlite3
```

The helper obtains the same exclusive owner lock, refuses an existing destination,
uses SQLite backup and integrity_check, and reports only row counts. Restore
into a fresh isolated DB with the same helper using the backup as --db; start
an isolated process against that destination and read the recorded IDs. Keep database backups and
release artifacts private; they contain full vacancy snapshots, raw responses
and manual changes. Select the new release with an atomic symlink rename,
`systemctl daemon-reload` after unit changes, and
`systemctl start hr-vision-vacancy-profile`. Check `/healthz`, authenticated
`/readyz`, the exact saved operation read and authenticated `/v1/diagnostics`.
Then reopen adapter routing. Health is liveness only; readiness verifies local
storage and queue worker and does not claim the external model is available.

Rollback uses the retained preceding artifact, the same graceful stop and
atomic symlink selection, then the same read checks. This version uses only
additive SQLite tables; unchanged schemas preserve existing plans/profiles.
Use a restored database copy only if state compatibility has failed and after
stopping all writers. Test restoration with read/replay first; do not overwrite
new manual revisions or requeue unknown provider calls. A release command or
unit file alone does not confirm rollback on a target server.

Diagnostics: `journalctl -u hr-vision-vacancy-profile` identifies startup/config
failures; `/v1/vacancy-profiles/RUN/diagnostics` returns timestamps, elapsed time,
status, response hash and safe error metadata. `/v1/diagnostics` gives queue and
terminal counts. Provider rejections are retained only in private SQLite table
`provider_rejections`; never expose that body over HTTP or put it in logs.
`outcome_unknown` requires operator/provider reconciliation; no automatic POST
retry exists. `response_saved` is replayed at startup without a model call.
Queued work resumes, interrupted `sent` becomes `outcome_unknown`. GET never
starts work. Startup fails safely for invalid configuration or a locked DB.
