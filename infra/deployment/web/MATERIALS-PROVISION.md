# Private materials preparation and one-interview acceptance

The reviewed source is `infra/deployment/web/provision-materials.py`. Run it on
outreach from a complete checkout as root, after copying the three hash-locked
requirements files and the deployment templates. It only prepares infrastructure:
it does not switch `/opt/hr-vision-api/current`, add the API drop-in, enable the
materials feature, or start the materials worker/scheduler.

```sh
sudo python3 infra/deployment/web/provision-materials.py --python /usr/bin/python3.12
```

It requires the existing `hr-vision` user and `/etc/hr-vision/api.env` plus its
actual `HR_DATA_DIR/journey.sqlite3`. It creates a private named Docker volume,
the pinned `rabbitmq:4.3.6-alpine` image container, `hr_materials` user and
`/materials` vhost, and a durable `materials` queue. Docker publishes only
`127.0.0.1:5672`. It caps container memory at 384 MiB, triggers the RabbitMQ
memory alarm at 192 MiB and disk alarm at 512 MiB, and bounds Docker logs to
three 10 MiB files. RabbitMQ definitions contain a salted SHA-256 password hash;
the random plaintext appears only in root-owned/group-readable
`/etc/hr-vision/materials.env` and never in command arguments or output.
The script installs both hash-locked dependency sets into
`/opt/hr-vision-materials/venv`, checks packages, and installs three inactive
systemd units. Repeating the script before enablement verifies the same image,
volume, files and environment; it does not rotate credentials or replace a
different container or venv.

Provisioned `materials.env` has `HR_MATERIALS_ENABLED=0`,
`HR_MATERIALS_BACKFILL_ENABLED=0`, `HR_MATERIALS_VIDEO_POLICY=remote`, and the
existing API SQLite path. The API and worker units load `api.env` first, so Daily
credentials do not need a second copy. Keep both units stopped until the reviewed
materials API release is installed, the private S3 configuration has passed an
exact `PutObject`/`HeadObject`/`GetObject`/presigned GET probe, and the API
drop-in uses the same venv. Edit the private env file as root to add
`HR_MATERIALS_STORAGE_BACKEND=s3` and scoped S3 settings/credentials; set
`HR_MATERIALS_ENABLED=1` only at the activation step. Keep backfill at `0`.
The image and lock versions are pinned; package updates require a reviewed
replacement, not an in-place pip upgrade. A systemd or Docker startup failure
must be investigated on outreach before any interview import.

After the API release and private S3 probe, these are the exact acceptance
commands for **only** `interview_6f011d66502349266a5a402b`. The oneshot unit
validates the stored completed Daily room and prints only catalog counts and
states. It obtains a short manager session through the existing team grant and
calls the authorized recruiter materials endpoint on `127.0.0.1:8391`. The API
commits jobs, then the scheduler publishes them to the broker for the worker.
Assertions wait up to 12 minutes and check successful worker attempts. The unit
never starts a call, runs ASR, or scans other owners.
Do not run the next phase until the previous assertion succeeds.
For a large recording, set `HR_MATERIALS_MAX_DOWNLOAD_BYTES` above its actual
size and budget enough private staging space on the worker filesystem. The
2026-10-25 rollout uses a 2 GiB limit and
`HR_MATERIALS_DOWNLOAD_TIMEOUT_SECONDS=900`; restart the worker after changing
these settings. A transfer streams to one temporary file, then uploads to S3
and removes the file. Interrupted downloads restart from the beginning.
The 12-minute assertion wait may finish before a transfer using the full
15-minute download budget; inspect the existing job and rerun only the assertion
after it settles, rather than starting another policy upgrade.

```sh
sudo systemctl start hr-vision-materials-worker.service
sudo systemctl start hr-vision-materials-scheduler.service
sudo systemctl start hr-vision-materials-acceptance@register-remote.service
sudo systemctl start hr-vision-materials-acceptance@assert-remote.service
sudo journalctl -u hr-vision-materials-acceptance@assert-remote.service -n 10 --no-pager
sudo systemctl start hr-vision-materials-acceptance@upgrade-owned.service
sudo systemctl start hr-vision-materials-acceptance@assert-owned.service
sudo journalctl -u hr-vision-materials-acceptance@assert-owned.service -n 10 --no-pager
```

`assert-remote` requires a ready provider video, no owned video, and an owned
provider transcript revision. It recomputes the raw transcript SHA-256 from S3
and reads the normalized text in a separate process without Daily.
`upgrade-owned` changes only this interview's storage policy, causing a copy
job. `assert-owned` streams the full owned video from S3 in bounded chunks
without writing a local file, recomputes its SHA-256 and size, and recomputes
the raw transcript SHA-256. It performs a 64-byte signed Range GET, requires
an unsigned public GET to return 403, and obtains the owned video link and
retained transcript in a process with Daily disabled. This reads the full owned
recording from S3 for verification. If provider files or worker jobs have not settled
after 12 minutes, inspect the read-only status command:

```sh
sudo systemctl start hr-vision-materials-acceptance@status.service
sudo journalctl -u hr-vision-materials-acceptance@status.service -n 10 --no-pager
```

Finally, use an authorized recruiter or candidate session in the actual portal
to play remote video before the upgrade and owned video after it, read the
transcript, and refresh an expired playback URL. The oneshot assertions cover
the HTTP route, durable jobs, owned storage and signed access; they do not prove
browser playback. Record only status, revision and storage type in acceptance evidence,
not recording bytes, transcript text, signed URLs, cookies or tokens.

Configuration rationale: [RabbitMQ's documented boot-time definitions](https://www.rabbitmq.com/docs/definitions),
[password hash format](https://www.rabbitmq.com/docs/4.3/passwords),
[absolute memory watermark](https://www.rabbitmq.com/docs/memory), and
[disk limit](https://www.rabbitmq.com/docs/configure).
