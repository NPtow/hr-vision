# Materials runtime

The catalog belongs to HR Vision. Provider IDs identify capture inputs; they are
not material IDs or access capabilities. Capture adapters discover metadata,
workers fetch files and store transcript revisions, and authenticated application
routes resolve access. Video and transcript readiness are independent.

The service extension point is `MaterialsService`: `list_materials(owner_kind,
owner_id, access_check=...)` reads the local catalog without provider I/O;
`get_transcript(...)` returns an owned immutable revision; `get_asset_access(...)`
checks access, resolves fresh provider access or a short S3 URL, then rechecks the
owner/generation. Existing portal materials routes keep their legacy response
projection. A new materials provider registers a `Capture` adapter with
`discover`, `inspect`, and `resolve` under its provider/account binding. Every
artifact carries a `Source` with verified provider/account, room and session
identity; ambiguous room-only history stays blocked. The catalog, storage and
portal projection can use the same interfaces. Call creation/transport integration
is a separate concern. Transcription imports existing provider text only; no ASR
provider has been selected or enabled.

This change is prepared locally. It does not provision a broker, bucket, keys,
webhook subscription, production Python, or enable a production feature.

## Dependencies and isolated Python

Use the isolated **CPython 3.12** runtime for both the materials worker and the
materials-enabled API. The API drop-in switches only that service's executable to
the same venv. The currently installed production API Python version has not been
verified. Do not change `/usr/bin/python3` or install these packages globally.

From the repository root, using a working Python 3.12 interpreter:

```sh
python3.12 -m venv output/materials-implementation-2026-10-25/venv
output/materials-implementation-2026-10-25/venv/bin/python -m pip install \
  --require-hashes --only-binary=:all: \
  -r interface-lab/server/requirements-materials.lock \
  -r interface-lab/server/requirements-api-extras.lock
output/materials-implementation-2026-10-25/venv/bin/python -m pip check
```

`requirements-materials.txt` records four direct dependencies. The generated
`requirements-materials.lock` pins all 27 packages and distribution SHA-256 hashes,
resolved for Linux x86-64 / Python 3.12. `requirements-api-extras.lock` separately
pins Pillow 13.0.0 to preserve the existing API image/OCR path when the API moves
to this venv; Ubuntu's `python3-pil` is not visible to the setup-python/venv
interpreter. Both CI and the enabled API install both locks. Static import review
found no additional API Python dependency beyond Pillow and the materials graph.
The existing `poppler-utils`, Tesseract language packs and `antiword` executables
remain OS-level requirements. Recreate locks deliberately, review their diff,
and rerun dependency/security checks before accepting an update:

```sh
uv pip compile --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
  --no-python-downloads --generate-hashes --no-emit-index-url --no-emit-find-links \
  --output-file interface-lab/server/requirements-materials.lock \
  interface-lab/server/requirements-materials.txt
```

On 2026-10-25 requests 2.34.2, boto3 1.43.118, celery 5.6.3, and pysubs2 1.9.0
were actually installed and imported in isolated CPython 3.12.13 on macOS ARM64.
The same lock installed successfully and `uv pip check` passed. OSV's batch API
returned HTTP 200 with no known advisories for all 27 exact versions. `pip-audit`
was not available; an advisory scan does not establish absence of vulnerabilities.
An actual Celery solo worker also consumed a synthetic JSON task through the
in-process memory transport and returned `SUCCESS` within a bounded test. This
checks the installed queue libraries, not the materials workflow end to end.
This is not a Linux/systemd or RabbitMQ deployment test; `systemd-analyze` is not
available on the macOS validation host.

The Homebrew Python on this workstation failed `ensurepip` with a `pyexpat`
dynamic-library error. Validation therefore used a private uv-managed Python under
`output/materials-implementation-2026-10-25/python`, without repairing Homebrew.
When doing the same, use `uv python install --no-bin` with an explicit
`UV_PYTHON_INSTALL_DIR`; no executable needs to be added to the global PATH.

## Configuration and first local enable

Keep credentials out of source, command arguments, frontend settings and logs.
The API and worker must point to the **same local SQLite file**. A different
`HR_MATERIALS_DB_PATH` creates a separate catalog; it is not replication.
The production API normally stores its database at
`/var/lib/hr-vision-api/journey.sqlite3`; confirm the actual `HR_DATA_DIR` first.

Required operator choices:

| Setting | Meaning |
| --- | --- |
| `HR_MATERIALS_ENABLED=1` | Explicit feature enable in the API and runtime environment. |
| `HR_MATERIALS_DB_PATH` | Absolute path to the existing application SQLite database. |
| `HR_MATERIALS_BACKFILL_ENABLED=1` | Explicitly permit automatic historic-owner backfill in reconciliation. Default is off for a single-interview rollout. The manual `backfill` command remains available. |
| `CELERY_BROKER_URL` | Private RabbitMQ AMQP URL, with a dedicated user/vhost; use TLS or a trusted local transport. |
| `HR_MATERIALS_VIDEO_POLICY=remote` | Keep a provider reference; access depends on the provider's retention and availability. |
| `HR_MATERIALS_VIDEO_POLICY=owned` | Download to the configured private store; readiness requires verified storage metadata. |
| `HR_MATERIALS_STORAGE_ROOT` | Private filesystem root, outside static/release directories. |
| `HR_MATERIALS_STORAGE_BACKEND=filesystem` | Local private files; choose `s3` only after provisioning a compatible private bucket. |
| `HR_MATERIALS_S3_BUCKET`, `HR_MATERIALS_S3_PREFIX` | Bucket and namespace (`materials` by default). |
| `HR_MATERIALS_S3_ENDPOINT`, `HR_MATERIALS_S3_REGION` | Explicit compatible endpoint and region; omit endpoint for AWS defaults. |
| `HR_MATERIALS_CONNECTION_ID` | Stable provider-account identifier, not an API key. |
| `DAILY_API_KEY` | Existing Daily adapter credentials, required only when using that provider. The API's call capture separately uses `HR_VIDEO_ENABLED=1`. |
| `HR_MATERIALS_DAILY_WEBHOOK_HMAC` | Optional webhook verification secret. Leave absent when webhook ingestion is disabled. |
| `HR_MATERIALS_MAX_DOWNLOAD_BYTES`, `HR_MATERIALS_TRANSCRIPT_MAX_BYTES` | Download limits: 2 GiB video and 20 MiB transcript by default. |
| `HR_MATERIALS_DOWNLOAD_TIMEOUT_SECONDS` | Total download bound, default 300 seconds. |
| `HR_MATERIALS_LEASE_SECONDS` | Renewable job lease, default 60 seconds. |
| `HR_MATERIALS_RECONCILE_SECONDS` | Dispatch/reconciliation interval, default 10 seconds. Provider rediscovery uses 60-second buckets. |
| `HR_MATERIALS_SETTLE_SECONDS` | Provider settling window from source creation, default 86400 seconds. Polling stops after it expires; waiting states remain explicit. |
| `HR_MATERIALS_BEAT_PATH` | Writable scheduler state file, default `/var/lib/hr-vision-api/materials-celerybeat`. |

Daily and Zoom are separate capture adapters, not the catalog's identity. Adding
capture requires an adapter and account binding, not rewriting stored material IDs.
Do not put signed URLs in source references, jobs, transcript provenance or logs.
Resolve them only when needed. `remote` does not create an owned video backup.

### Zoom capture alongside Daily

`materials_zoom.ZoomCapture` registers alongside Daily when Zoom server-to-server
credentials are configured. It uses `HR_ZOOM_CONNECTION_ID` (default `zoom`), separate
from the existing Daily `HR_MATERIALS_CONNECTION_ID`. Disabling Zoom selection for
new meetings does not disable access to existing Zoom recording metadata. The worker
and API must receive the same explicit Zoom connection identity and S2S configuration;
Meeting SDK frontend keys are not necessary for the materials worker.

Only a server-stored Zoom room with `provider: zoom`, `id`/`meetingNumber`,
`meetingSessionId` (immutable session UUID), and `connectionId` can be registered or
backfilled. The source reference keeps the numeric meeting ID and exact UUID, never
room credentials or temporary URLs. Discovery queries this UUID, validates the
returned numeric ID/UUID, and rejects cross-session files. It never retries a lookup
using the reusable numeric meeting ID. Missing bindings remain blocked; a restart
that creates another Zoom instance needs its separately verified UUID recorded by
the call integration, not inferred from the latest recording or its title.

The adapter imports MP4 metadata and existing `audio_transcript` VTT files. Zoom's
processing/failed/deleted/unknown statuses stay distinct. Empty recording lists do
not prove that recording was never started: bounded reconciliation can discover
later files, including transcripts that arrive after video. No new transcription or
summary service is called. The current materials worker supports video and
transcript only: Zoom M4A audio, chats, captions and AI summaries are not imported.
Zoom webhook ingestion is not enabled; this variant uses the existing scheduler.

Zoom file download requires a server-side Authorization header. The resolver obtains
fresh private access only inside the download attempt, and no token or download/play
URL is stored in the catalog. **Remote Zoom video cannot be played directly in the
browser**: the existing access method returns HTTP 409 requiring a server copy, rather
than publishing a provider bearer token. For portal playback select `owned` for that
meeting and configure private S3 playback; the filesystem backend supports private
ingest but has no signed playback URLs. No global storage policy is changed for Daily.
Authenticated policy changes and existing owner/generation checks remain in force.

The Zoom REST contract used here is documented at
https://developers.zoom.us/docs/api/meetings/ (cloud recordings). Adapter tests use
synthetic metadata/files; they do not establish real-account retention, transcription
availability, or S3 playback acceptance.

Prepare a synthetic local database through the application's existing initialization
path, set `HR_DATA_DIR` and `HR_MATERIALS_DB_PATH` to that same temporary directory,
and keep production credentials absent. Run the material unit tests first. A local
`memory://` Celery transport is only useful inside one test process: it cannot
connect two separately started worker/scheduler processes and is not durable.

For an explicitly configured development RabbitMQ, from `interface-lab/server`:

```sh
../../output/materials-implementation-2026-10-25/venv/bin/python -m materials_runtime worker
# In a separate terminal, with the same private environment:
../../output/materials-implementation-2026-10-25/venv/bin/python -m materials_runtime scheduler
```

The HTTP API commits jobs locally with a no-op publisher; it never waits for a
broker connection when registering materials. The scheduler dispatches due jobs
on the next reconciliation cycle (default 10 seconds); provider metadata polling
is deduplicated into 60-second buckets. The worker consumes queue `materials`.
The catalog preserves jobs across broker failures. A broker message by itself
is not proof that a material is complete.
Automatic provider polling is bounded by the settling window, including late
transcripts after video readiness. When it expires, missing/waiting transcripts
stay explicit; the scheduler does not fabricate output or reset exhausted retry
budgets. A verified new webhook can wake an existing source after the window.
Use exactly one scheduler for this deployment. Its beat state must be writable by
`hr-vision`. Worker concurrency is one (Celery solo pool) to keep SQLite writes and
downloads bounded. RabbitMQ task messages carry JSON; no pickle inputs are accepted.

Existing authenticated recruiter `*/materials` POST routes accept optional
`storagePolicy: "remote" | "owned"` in their existing body. This changes the policy
for that interview/meeting's root and session sources. Candidate requests that
include this field receive HTTP 403. Employer routes do not expose a policy-change
operation. The same owner/access checks still govern reading materials; this is
not a public catalog endpoint. Do not add policy changes to an unauthenticated
page or treat a provider room name as authorization.

## Storage, capture and transcripts

For filesystem storage, reserve capacity for the configured download bound and
concurrent temporary copies, make the root readable only by the service user,
and include that directory in encrypted private backups. Never use `public/`,
Storybook output or a release directory as the store.
Filesystem storage is for development/headless import and owned transcripts in
this variant. It has no public/signed URL facility and does **not** support owned
video playback in the portal. Keep video `remote` with that backend. Owned video
playback requires the private S3 backend, which issues temporary playback URLs.
Do not treat a filesystem path as a URL.

For S3-compatible storage, provision a **private** bucket separately. Confirm the
endpoint, region, bucket, prefix, service-account write/read rights, lifecycle,
encryption and versioning/backup policy. Disable anonymous reads. Use a scoped
service account; the worker does not need bucket administration or deletion rights.
A presigned URL is a temporary bearer capability and must not be treated as a
permanent stored location. API download authorization still applies before issuing
one. The adapter uses one conditional `PutObject` (`If-None-Match: *`) with a
SHA-256 checksum, followed by a metadata/size check. Verify that the chosen
S3-compatible service supports those operations; otherwise keep it disabled.
The adapter's hard single-upload limit is 5 GiB; the worker defaults to a lower
2 GiB download limit. Bucket creation and credential distribution are not
implemented by this code.
Locations identify the backend, object key and a nonsecret storage connection
identity derived from the configured namespace. Access checks reject a changed
connection rather than resolving an old key against a different bucket/root.
A bucket/root migration is not implemented. Preserve the corresponding nonsecret
storage configuration with backups; changing it is not a migration of locations.

Before a durable Put, the worker records a fenced pending write intent containing
the object identity, size and SHA-256. A retry can verify/adopt an object whose Put
finished before a crash, and normalize an already owned raw transcript without
fetching it again. A pending intent does not mean the copy is ready. If an owner
is suspended or a lease is lost, publication remains blocked even if bytes reached
storage. There is no automatic object erasure or garbage collector: preserve these
objects and review pending intents/orphans before any separately authorized cleanup.

Transcript import preserves the raw file and a parsed revision. `pysubs2` supports
SRT and basic WebVTT; styling, speaker identifiers, overlap semantics and exact
round trips are not guaranteed. Preserve raw VTT when interpreting speaker data.
No ASR provider or automatic retranscription is enabled by this implementation;
missing provider transcription remains an explicit missing/pending/error state.
Do not send recordings or transcripts to a new external ASR/LLM service to fill it.

`run-once --job-id ID` claims only an existing due pending/retry job; it does not
revive failed/cancelled/completed jobs or bypass their backoff/attempt limit.
There is no CLI reset or public retry endpoint. A separately reviewed operator
integration can explicitly enqueue discovery with a new idempotency token through
`runtime.enqueue_discovery(source_id, token=...)`; reuse that token on replay.
This preserves old attempts. A later discovery can create fresh follow-up work
after failed imports when no related task is active or already succeeded. Source
generation, owner suspension and storage connection checks still apply. Merely
running `reconcile` after the settling window is not a forced provider refresh.

## Backfill and recovery

Before enabling automatic backfill or running manual backfill against real data, review
candidate owners and exact room/session bindings and take a backup. The current
runtime's reconciliation invokes backfill only with `HR_MATERIALS_BACKFILL_ENABLED=1`;
leave it off for the first-interview acceptance. Completion hooks and authorized
reads can still register that interview. The `backfill` command accepts
`--dry-run --limit 10` (limit is per owner kind, so up to 20 eligible records).
Validate first on a private DB copy, without provider credentials or a broker:

```sh
../../output/materials-implementation-2026-10-25/venv/bin/python \
  -m materials_runtime backfill --dry-run --limit 10
```

This previews eligibility counts, not per-owner identities or a complete production
approval list. `build_runtime` still initializes the catalog schema and private
storage root, so use a copy for a preview without changes to the original DB.
There is no CLI owner selector. Review source records separately before a real
bounded batch; `--limit` does not limit the scheduler's later reconciliation cycles.
An ordinary reusable room name alone is insufficient evidence for
assigning recordings to a person or interview. Ambiguous historic records must
remain blocked for review. Do not bulk repair ownership by guessing.

Backfill must preserve original provider IDs and existing transcript versions,
reuse idempotency keys, and avoid changing hiring state or notification outboxes.
Check counts and explicit error states after a small batch before expanding.
Retries may rediscover metadata or copy files; do not automatically re-run old
recruiter actions or call-provider creation/start commands.

Create a consistent SQLite backup using the SQLite backup API, not `cp` of an
active database while WAL writers run. For example, on a controlled local copy:

```sh
python3.12 - <<'PY'
import os, sqlite3
from pathlib import Path
source = Path(os.environ['HR_MATERIALS_DB_PATH']).resolve(strict=True)
target = Path(os.environ['MATERIALS_BACKUP_PATH'])
if target.exists():
    raise SystemExit('Choose a new backup path')
target.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone() == ('ok',)
PY
```

Back up owned objects together with their catalog/checksums; a database backup
alone cannot restore missing files. Test restore into another private directory,
run `integrity_check` and `foreign_key_check`, and verify referenced objects before
switching anything. For a production restore, stop scheduler, worker and **all**
API/database writers in a planned maintenance window. Preserve the current DB,
WAL/SHM and object inventory as recovery material. Restore a consistent snapshot
with its matching objects, set ownership/mode, then start a compatible runtime.
Never overlay a backup onto a live database or discard newer hiring state merely
to roll back code. Use a compatible forward fix when possible.

## Service installation boundary

Templates: `deploy/hr-vision-materials-worker.service` and
`deploy/hr-vision-materials-scheduler.service`. They expect a private Python 3.12
venv at `/opt/hr-vision-materials/venv`, complete modules at
`/opt/hr-vision-api/current`, and a root-owned private
`/etc/hr-vision/materials.env`. Both run as `hr-vision`; writable state is limited
to `/var/lib/hr-vision-api`. Keep a local storage root under that path or explicitly
review the systemd sandbox before using another location. Do not embed credentials
in unit files. There is no dependency on a host-local RabbitMQ unit because the
broker may be external; runtime connection failures must remain visible.

For the enabled API, install the separately prepared
`deploy/hr-vision-api-materials.conf` as
`/etc/systemd/system/hr-vision-api.service.d/materials.conf`. It keeps the base
unit's `/etc/hr-vision/api.env`, adds `/etc/hr-vision/materials.env` afterwards,
and overrides `ExecStart` to use `/opt/hr-vision-materials/venv/bin/python`.
The later environment file wins for duplicate keys; do not use it to accidentally
override unrelated API settings. Worker/scheduler units load only `materials.env`,
so that private file must contain their complete required configuration, including
the selected provider's key. It must be readable only by the service administrator
and service account. Removing the drop-in or restarting the API is a deployment
operation, not part of this local preparation.

Install the updated `deploy/ci/receiver.py` **before** publishing the first materials
release. Its complete runtime includes all ten `materials_*.py` modules and agrees
with `scripts/package-release.mjs`; partial material layouts are rejected.
Requirements/lock, documentation, tests, environment files and credentials are not
part of the API runtime archive. Provision the venv and private configuration
separately through the administrator-controlled installation procedure. Merely adding
these files to the repository does not install packages or start either service.
The CI Python test job uses Python 3.12 with both hash-locked dependency files
installed before full server test discovery; runtime archive allowlists do not
provision the CI interpreter or its packages.
Before enabling, verify Python/imports, SQLite access, private bucket access,
RabbitMQ durability/authentication, `systemd-analyze verify`, resource limits,
graceful shutdown and reconnect/reconciliation with synthetic data. Review live
call protection and DB backup requirements in `deploy/ci/README.md`.

On API code changes, the updated CI receiver stops the materials scheduler and
worker and waits for in-flight tasks to drain before its SQLite backup and pointer
switch; the API release symlink alone cannot reload already imported worker
modules. It restarts units that were active before the release after successful
activation or a compatible fallback. If drain fails, release stops; if fallback is
unsafe, workers stay stopped for forward recovery. Preserve
the previous runtime and venv for rollback; do not roll the shared DB backwards.
The receiver still accepts the complete pre-materials archive, but activation
refuses that old runtime when `media_sources` contains any row, including terminal
or suspended sources. An absent or empty catalog permits the earlier layout; stop
the materials worker/scheduler first. The guard runs under a SQLite write
reservation and is checked again for an automatic fallback after health failure.
Do not delete catalog rows or owned objects to bypass it; use compatible forward
recovery when a populated catalog makes rollback unavailable.

Production acceptance still needs the real Linux/RabbitMQ/S3 configuration,
permission checks through authenticated HTTP, expired-link refresh, bounded large
file copy and disk-pressure tests, and a crash/lease recovery drill. No public
URL health check, eager task test or in-process memory worker proves those paths.
