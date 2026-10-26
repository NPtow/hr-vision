# Delivery and recovery for the assessment services

Owner: «Ядро». Runtime readiness remains governed by
`packages/assessment-contracts/ACCEPTANCE.md`; these tools do not establish
production acceptance. Python >=3.10, systemd on Ubuntu. No third party Python
packages. No SSH commands and no HR API restart are performed by these tools.

| key | unit / immutable release root | SQLite | port |
| --- | --- | --- | --- |
| candidate | hr-vision-assessment-core | /var/lib/hr-vision-assessment-core/runs.sqlite3 | 8097 |
| vacancy | hr-vision-vacancy-profile | /var/lib/hr-vision-vacancy-profile/runs.sqlite3 | 8108 |
| recommendations | hr-vision-recommendations | /var/lib/hr-vision-recommendations/results.sqlite3 | 8099 |

Each root is `/opt/<unit>/releases/<40-character-git-commit>`. `current` is an
atomic symlink. Releases and units must be root owned, with no group/other
write permission. Each artifact includes its own `jev_client`, rubric, systemd
unit, `shared-components.json` and `release.json` full SHA256 inventory. No service
reads another service's database or a checkout. The units use DynamicUser,
StateDirectory, MemoryMax, TasksMax and hardening; their source remains in each
service's `deploy/` directory.

Build **after the service/client/catalog/unit changes are committed**. Git archive
uses the specified commit; dirty/untracked files cannot enter the artifact. A
missing committed component fails the build. Run from the repository:

```sh
python3 services/deploy/assessment-services/package.py candidate --commit FULL_COMMIT --output /tmp/candidate.tar.gz
python3 services/deploy/assessment-services/package.py vacancy --commit FULL_COMMIT --output /tmp/vacancy.tar.gz
python3 services/deploy/assessment-services/package.py recommendations --commit FULL_COMMIT --output /tmp/recommendations.tar.gz
```

On the authorized target host, run a reviewed copy of `operate.py` as root.
`FULL_COMMIT` below is replaced by the **observed current** full SHA, not a guessed
revision. Installation is independent: it does not start/stop either other service.
The operator must keep consumers from admitting new jobs for the complete change
window; `--consumers-quiesced` records that assertion, it does not disable admission.
The existing candidate must finish jobs first. For an existing candidate use
`guarded_upgrade.py` below: it establishes the assertion by holding the installed
receiver's API/material-writer pause across the complete operation. No provider
calls are made by the installer or health checks.

```sh
python3 /root/assessment-delivery/operate.py candidate inspect --probe
python3 /root/assessment-delivery/guarded_upgrade.py /root/candidate.tar.gz --expected-current FULL_COMMIT --legacy-candidate --receiver /ABSOLUTE/REVIEWED/INSTALLED/receiver.py --receiver-sha256 REVIEWED_RECEIVER_SHA256
python3 /root/assessment-delivery/operate.py vacancy install /root/vacancy.tar.gz --expected-current absent --consumers-quiesced
python3 /root/assessment-delivery/operate.py recommendations install /root/recommendations.tar.gz --expected-current absent --consumers-quiesced
```

The receiver path and its reviewed SHA256 are explicit host inputs: do not use a
checkout copy or invent an installed path. The wrapper verifies root ownership,
protected mode and digest before import. It acquires `/srv/hr-vision/.deploy.lock`
then `/run/hr-vision-api-deploy.lock` nonblocking, captures the existing API pointer
and UI deployment SHA, and refuses active candidate jobs before causing downtime.
It calls receiver `guard_and_backup`, pauses material writers, stops API through
the receiver, then repeats the live-interview guard/backup against stopped state.
Only inside that window does it invoke candidate install with
`--consumers-quiesced`. In `finally`, receiver `activate_api(original_pointer)`
rechecks durable compatibility, and `check_origin('service', existing_ui_sha)`
must pass before material writers resume. If activation/origin recovery fails,
API is stopped through the receiver and material writers remain paused. Inspect
units and recover through the guarded receiver; do not manually resume writers.
Other direct candidate writers must remain disabled for this operation.

`--legacy-candidate` is only for the first upgrade from an existing unmanifested
candidate release such as b667a…. It records a checked, root-owned file inventory
outside that immutable release, permitting a later verified return. It does not
silently bless a release already carrying an invalid manifest. Subsequent updates
use the same install command without that flag and with the observed SHA.

There are **no credential arguments**. Provider key and explicit loopback SOCKS
proxy are reused on-host exclusively from the existing mode-0600
`/etc/hr-vision/assessment-core.env`, keys `ASSESSMENT_CORE_JEV_API_KEY` and
`ASSESSMENT_CORE_JEV_PROXY`. Candidate/vacancy use model `jev-1.14.0`; recommendations
has no model. Tokens are generated with `secrets.token_hex(32)` and existing valid
64-hex service tokens are retained. Private files:

- `/etc/hr-vision/{assessment-core,vacancy-profile,recommendations}.env`:
  service prefix `ASSESSMENT_CORE`, `VACANCY_PROFILE`, `RECOMMENDATIONS` followed
  by `_SERVICE_TOKEN`, `_PORT`, `_DB`, and for provider services `_JEV_API_KEY`,
  `_JEV_PROXY`, `_MODEL`.
- Matching `*-bridge.env`: `HR_<PREFIX>_{URL,TOKEN,MODEL,RUBRIC,CATALOG}`;
  candidate also has `HR_ASSESSMENT_CORE_ENABLED=1`.
- `/etc/systemd/system/hr-vision-api.service.d/assessment-<service>.conf` loads
  only that bridge file. HR API activation belongs to the guarded HR release
  receiver, which checks active meetings. Do not manually restart HR API.

The installer acquires separate exclusive nonblocking deploy and operation locks,
validates manifests and expected symlink, refuses queued/sent/response_saved jobs,
probes the canonical port (refusing an existing listener), stops gracefully and
repeats the job check before activating. For an update the port probe runs after
stopping only its own unit. It never kills or reconfigures another port owner. Jobs admitted in
that race cause an abort and restart of the original release/config. Locks serialize
operators; they do not lock HTTP submission or worker code. Before activation it
uses SQLite online backup, never copies a live WAL database. Health and authenticated
readiness have a combined 30-second bound. After success it enables this unit. A failed first installation preserves its database
and records an operation marker; an explicit retry with `--expected-current absent`
can reuse only that recorded data, never an unexplained orphan database.

A failed health check automatically stops the failed release, preserves the live
compatible database, restores the previous symlink/unit/private configuration and
checks the old release. Update/rollback forbid schema changes: the SQLite schema
fingerprint must match. The legacy candidate has no `/readyz`; its return check is
explicitly limited to `/healthz` plus database integrity/no-active-job checks.
On incompatible schema or newly active jobs the operation refuses to overwrite
state; inspect and resolve the recorded jobs. This is a safety boundary, not a
successful automatic rollback. Schema migration requires a separately reviewed
procedure; `data_compatibility` v1 is not permission to migrate data.

```sh
python3 /root/assessment-delivery/operate.py vacancy inspect --probe
python3 /root/assessment-delivery/operate.py vacancy inspect --id RUN_ID_64_HEX
python3 /root/assessment-delivery/operate.py vacancy backup
python3 /root/assessment-delivery/operate.py vacancy restore-isolated /var/backups/hr-vision-vacancy-profile/OBSERVED.sqlite3 /srv/assessment-restore/runs.sqlite3
python3 /root/assessment-delivery/operate.py vacancy rollback PREVIOUS_FULL_COMMIT --expected-current CURRENT_FULL_COMMIT --consumers-quiesced
journalctl -u hr-vision-vacancy-profile --since '10 minutes ago' --no-pager
```

Backup writes a mode-0600 SQLite file and metadata/hash under `/var/backups/<unit>/`.
`restore-isolated` requires its matching metadata, checks hash/integrity/schema and
refuses existing destinations and production `/var/lib`, `/opt`, `/etc` paths.
It does not replace the running database. Restoring production data would roll back
accepted work and is deliberately outside this command. `inspect --id` prints only
status/timing/raw-response-present/result-present, never stored texts or secrets.
Unknown provider outcomes require reconciliation, never blind POST retry.

Local evidence (26.10.2026): thirteen delivery tests pass, including an actual isolated
recommendations HTTP process from a committed fixture artifact, `/healthz` + `/readyz`,
update, injected failed-health return and controlled rollback. Other tests cover
five pure receiver-orchestration scenarios (successful ordering, busy core,
failed install, stopped-state live guard and unsafe API/origin recovery),
byte-reproducible packaging despite dirty files, manifest tamper, operator locking,
active/raced jobs during update and controlled rollback, occupied-port refusal,
and live-WAL backup/isolated restore/hash corruption. The six
rubric activation tests also pass, including nested shared-client edits/additions/
removals. Commands:

```sh
python3 -m unittest discover -s services/deploy/assessment-services/tests -v
python3 -m unittest discover -s services/assessment-core/tests -p test_rubric_release.py -v
```

Raw local execution and source SHA256 inventory: `test-evidence.json`.

UNVERIFIED: installed receiver orchestration on the target, target-host systemd/DynamicUser permissions and resource limits, actual
provider egress, production release/health/rollback, end-to-end HR consumption and
live JEV quality/load. Local subprocess tests do not prove these properties.
