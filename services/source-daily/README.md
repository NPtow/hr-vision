# Daily source service

Independent FastAPI process, Celery worker and reconciler. This package has no HR
SQLite client, no domain model imports and no import/path dependency on a deployed
portal release. Its private SQLite database contains only source job checkpoints,
immutable receipt bodies/delivery acknowledgments, approved artifact evidence and
verified webhook deduplication hints. Core and transcription are HTTP boundaries.

## Process commands

Python 3.12, dependencies pinned in `pyproject.toml` to the transcription service
versions. The shared deployment directory supplies the hash-locked distribution.

```sh
md-source-daily serve --host 127.0.0.1 --port 8402
md-source-daily worker --concurrency 1
md-source-daily reconcile
md-source-daily reconcile --once
```

The API commits a private job before queue publication. The separate reconciler
publishes due jobs to the dedicated RabbitMQ queue every 15 seconds, recovering a
failed API publication or an expired worker lease. Worker messages are harmless
when duplicated: only one current lease can checkpoint/publish. Celery acknowledges
late, rejects lost workers, uses JSON, publisher confirmation and one-message
prefetch. Its hard limit equals the lease. Reconciler deletes only its own stale
`source-*`, `asr-*`, `result-*` temporary directories after at least an hour and
longer than every worker lease. No media input survives normal context cleanup.

## Configuration

All variables begin with `SOURCE_DAILY_`; no credentials belong in command arguments.

| Suffix | Meaning / default |
| --- | --- |
| `DB_PATH` | Private SQLite, default `/var/lib/md-source-daily/jobs.sqlite3` |
| `TEMP_ROOT` | Private bounded-download scratch, default `/var/lib/md-source-daily/tmp` |
| `API_KEYS` | JSON object of distinct bearer token -> trusted client ID; empty disables authenticated access |
| `DAILY_KEY` | Provider REST credential; source process only |
| `DAILY_WEBHOOK_HMAC` | Daily base64 HMAC secret; absent disables webhook verification |
| `CONNECTION_ID` | Fixed Daily account identity, default `daily` |
| `DEFAULT_STORAGE_POLICY` | Provider policy advertised for portal binding creation, `remote` (default) or `owned` |
| `CORE_URL`, `CORE_TOKEN` | Configured core endpoint and fixed source bearer principal |
| `TRANSCRIPTION_URL`, `TRANSCRIPTION_TOKEN` | Separate transcription HTTP endpoint / service token, used for deterministic normalize and optional ASR |
| `ENABLE_ASR` | Explicit operator opt-in, `false` by default; this service selects no ASR provider/model |
| `DAILY_TRANSCRIPTION_MODEL` | Explicit existing Daily live transcription model; empty default does not start provider transcription |
| `BROKER_URL` | Dedicated `amqp://` or `amqps://` RabbitMQ vhost URL; no in-memory broker fallback |
| `QUEUE` | Dedicated queue, `source-daily` default |
| `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT`, `S3_REGION` | Configured owned store; prefix `materials`; boto3 standard credential chain |
| `MAX_ATTEMPTS`, `SETTLE_ATTEMPTS` | Acquisition rounds 8 / minimum 3 before absence is settled |
| `MAX_ASR_POLLS` | Bounded status polls, 120 default |
| `RETRY_SECONDS`, `LEASE_SECONDS` | Work poll spacing 30s / lease 600s |
| `MAX_VIDEO_BYTES` | Bounded fetched input, 512 MiB default, at most S3 single-upload 5 GiB |
| `MAX_TRANSCRIPT_BYTES` | Raw provider text limit, 16 MiB default and maximum |
| `MAX_BODY_BYTES`, `MAX_ARTIFACTS` | HTTP request 256 KiB / discovery 100 defaults |
| `LEGACY_MATERIALS` | `false` default; explicitly enable room-oriented legacy projection only for demo/lab |

Readiness checks private DB readability and reports provider/core configuration;
its `ready` response does not claim real end-to-end Daily/S3/RabbitMQ availability.
`/health/live` is public. `/health/ready` and `/v1/*` require configured bearer auth.

## API and recovery contract

The shared authoritative contract is `../contracts/materials-v1.md`. Jobs accept
only `{binding_id,generation}`. Source fetches that approved binding from core,
verifies its fixed provider/account and pinned retention policy, then discovers
exact provider sessions. A room without a session requires core's trusted
exclusive-owner assertion; a caller cannot supply that assertion to the job API.
No arbitrary download URL, HR candidate identity, bucket or endpoint is accepted.

Typed call endpoints are `/v1/calls/room`, `/token`, `/start`, `/finish`, `/materials`.
There is no REST forwarding endpoint. Token properties are explicitly whitelisted;
implicit recording/transcription in tokens is rejected. Start acknowledges recording
and transcription independently with `recording_requested` and
`transcription_requested` plus safe channel error codes. An empty configured Daily
transcription model returns `daily_transcription_not_configured` for that request.
Finish returns `{acknowledged:true}` only after the reused adapter's independent
presence/eject/expiry proof; a room 404 alone is insufficient.

`POST /v1/jobs` returns 202 and the same durable job for the same binding generation.
`GET /v1/jobs/{id}` returns independent channel states, `acquisition_complete` and
`transcription_status`, without URLs, transcript bodies or credentials.
`POST /v1/jobs/{id}/resume` explicitly requeues waiting/failed/completed work after
configuration repair. Reenqueuing an awaiting-configuration job also wakes it when
relevant transcription settings are now available. Resume never clears ASR job IDs
or retries an uncertain/failed paid ASR call. Such a retry requires a separate,
explicit transcription-service decision; source never invokes its `/retry` route.

Acquisition rounds and ASR polls are bounded. Temporary delivery failures for an
already saved receipt have a separate, durable retry loop with exponential backoff
capped at one hour; they never consume acquisition attempts or mark delivered.
401/403/404/409 at the core boundary fence the job for authorization/generation
review. Every receipt is stored before sending, keyed by binding+canonical body;
response loss retries exactly that body and key. A crash between terminal checkpoint
and receipt insertion rebuilds the same receipt on recovery. Multiple receipts are
used when ASR expands a capture beyond core's 100-assets-per-request limit.

Remote video keeps a stable provider object reference after fresh exact inspection
and access-link resolution. Owned video is downloaded through the pinned, public
HTTPS downloader and copied to content-addressed S3 with checksum/size verification.
Raw provider transcript bytes always go to owned S3, then HTTP `/v1/normalize`.
Raw acquisition can complete while normalization awaits configuration or retries.
Canonical output is validated, never synthesized; missing time/speaker information
and warnings remain unchanged. Provenance never asserts complete speech coverage.

ASR is opt-in and source uploads original bytes, polls and retrieves a canonical
result through HTTP. Idempotency is based on immutable recording identity and does
not change on remote-to-owned retention upgrades. Previous ASR IDs/results/uncertain
states survive those generation upgrades. A provider transcript covers only its
exact session; another recording session still needs ASR or explicit
`awaiting_configuration`. HTTP 503 infrastructure failure retries; documented
`asr_not_configured` remains explicit. `uploading` reuploads the same bytes/key.
Video readiness remains independent when ASR is disabled, unavailable or failed.

`POST /v1/materials/access` takes only binding/external ID, refetches the authorized
core binding, uses private exact-session evidence and re-inspects Daily before
returning `{url,expires,expires_at,provider}`. Historical core-approved bindings
without private evidence recover it with exact provider discovery. No bearer
headers are returned. Signed URLs are transient responses, never job/evidence fields.

`POST /v1/webhooks/daily` receives original raw bytes plus Daily timestamp/signature
headers through the trusted portal bearer. Reused Daily HMAC verification precedes
durable URL-free hint deduplication. Matching room/session jobs are woken; ready
hints can rescan a settled job. Hints never prove an artifact's owner or readiness;
all provider data is inspected again. Unknown/invalid signatures fail safely.

## Reusable adapter boundary and checks

`src/source_daily/_vendor/manifest.json` records original SHA-256 and vendored
SHA-256 for DailyMedia, DailyCapture, provider-neutral DTOs, safe HTTPS downloader,
S3 storage and provider deadline support. `build_vendor.py` refreshes their snapshot
from the selected repository artifact, changing imports to relative standalone
primitives. Minimal support/operation hooks import no HR business modules.

```sh
PYTHONPATH=services/source-daily/src /tmp/md-services-venv/bin/python -m unittest discover -s services/source-daily/tests -v
```

Tests use private SQLite, real local HTTP core/normalizer/ASR fakes and controlled
provider/storage adapters. They cover scoped artifacts, remote/owned modes, denied
recordings, receipt response loss, both crash windows, leases/fencing, repair after
configuration, multi-session transcription, retention upgrades, provider URL
metadata rejection, verified webhook dedupe and independent call acknowledgments.
External provider production availability is outside these deterministic checks.
