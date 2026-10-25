# Recording source coordinator

Independent process, independent SQLite WAL (`source.sqlite3`), no HR database
path, imports or SQL. Core authorizes every event using a configured **portal
read credential** plus the provider/connection **source credential**. Candidate
IDs and provider URLs are never accepted by this API. Keep portal credentials
scoped to the callers' candidates when appropriate; a broad Core portal token
intentionally grants its configured scope to that ingress principal.

## Processes

Install `pip install -e 'services/transcription-source[test]'` in the service venv.
Run these three separate processes with the same protected environment file:

```sh
python -m hr_transcription_source api --port 8093
python -m hr_transcription_source worker
python -m hr_transcription_source scheduler
```

API binds **127.0.0.1**. Worker uses Celery solo/concurrency 1, a dedicated durable
`hr_transcription_source` queue and persistent RabbitMQ delivery. Scheduler
reconciles committed due SQL jobs every 30 seconds. It also removes orphaned
staging files older than two leases. A failed broker publication never loses
the committed job. Single host/SQLite only. For inspection without RabbitMQ:
`python -m hr_transcription_source run-once --job-id ID` or `reconcile` (the latter
only republishes; does not execute jobs). Do not run manual workers concurrently
with a deployed worker except for deliberate recovery.

`GET /health/live` is public; authenticated `GET /health/ready` reports API,
configuration and recent real worker heartbeat separately. A healthy API does
not imply a running worker. No secret or transcript is in public job responses.

## Configuration

Required environment values (secrets must live outside Git):

| Variable | Value |
|---|---|
| `TRANSCRIPTION_SOURCE_DATA_DIR` | private independent service state directory; default `/var/lib/hr-transcription-source` |
| `TRANSCRIPTION_SOURCE_CORE_URL` | configured Core origin, e.g. `http://127.0.0.1:8092` |
| `TRANSCRIPTION_SOURCE_ASR_URL` | independent ASR origin, e.g. `http://127.0.0.1:8091` |
| `TRANSCRIPTION_SOURCE_ASR_TOKEN` | ASR caller Bearer credential, at least 32 characters |
| `TRANSCRIPTION_SOURCE_CALLERS_JSON` | `{"collector":{"token":"INGRESS_SECRET_32_OR_MORE","core_read_token":"CORE_PORTAL_SECRET_32_OR_MORE"}}` |
| `TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON` | `[{"provider":"zoom","connection_id":"zoom","token":"CORE_SOURCE_SECRET_32_OR_MORE"}]` |
| `TRANSCRIPTION_SOURCE_DOWNLOAD_HOSTS` | exact comma-separated private S3 host allowlist, no wildcards or URLs |
| `TRANSCRIPTION_SOURCE_S3_BUCKET` | same private materials bucket Core verifies for this provider/connection |
| `TRANSCRIPTION_SOURCE_S3_PREFIX` | same content-addressed prefix as Core, default `materials` |
| `TRANSCRIPTION_SOURCE_S3_ENDPOINT` | configured S3 endpoint; optional for AWS |
| `TRANSCRIPTION_SOURCE_S3_REGION` | configured S3 region, optional |
| `TRANSCRIPTION_SOURCE_BROKER_URL` | durable RabbitMQ URL; no `memory:` backend |

Boto3 uses the standard credential chain. Source needs GetObject for recording
delivery via Core-signed URL and PutObject/HeadObject/GetObject for raw immutable
response objects. No public ACL is requested. Do not point at a different prefix
from the Core provider/connection storage registry.

Optional: `TRANSCRIPTION_SOURCE_ASR_PROVIDER=yandex_speechkit` (set explicitly to
`deepgram` for a Deepgram ASR deployment),
`TRANSCRIPTION_SOURCE_CONFIG_REVISION=speechkit-v1`,
`TRANSCRIPTION_SOURCE_MAX_INPUT_BYTES=1073741824` (must match the ASR input
limit), `TRANSCRIPTION_SOURCE_MAX_RESULT_BYTES=25165824`,
`TRANSCRIPTION_SOURCE_LEASE_SECONDS=1800` (minimum 600),
`TRANSCRIPTION_SOURCE_RETRY_SECONDS=30`. Source streaming checks SHA and size,
reserves free disk for the complete input plus 64 MiB and renews its lease on
each downloaded chunk. HTTP endpoints require TLS except explicit loopback.
Network timeout is bounded; redirects are refused, signed URLs are never stored.

## HTTP contract

Authenticated `POST /v1/recordings`, JSON exactly:

```json
{"owner_kind":"interview","owner_id":"OWNER_ID","binding_id":"BINDING_ID","generation":1,"asset_id":"ASSET_ID"}
```

Header `Idempotency-Key` is required (1–128 ASCII alphanumeric or `_.:-`).
Response 202 contains `id`, `transcription_status`, `delivery_status`,
`asr_job_id`, `transcript_ids`, `error_code`, timestamps and `created`.
`GET /v1/jobs/{id}` uses the same caller authentication and reauthorizes Core
access. Different event keys for the same binding/generation/asset/revision
resolve to the same durable job. Another ingress principal's job is not exposed.
A deliberate new ASR version requires a new operator config revision and a new
request key; there is no automatic retry endpoint for a failed/uncertain paid
recognition and no automatic history backfill.

A run waits for actual Core `acquisition_complete=true` before any ASR submit,
then validates the exact asset is ready, its `source_id` equals the requested
binding, both portal/source Core credentials can access that binding, generation
is current, binding isn't revoked and the owner is the requested interview or
meeting. It obtains recording access from Core, downloads only the verified
owned object, checks complete SHA/size, and submits raw bytes to
`POST /v1/jobs?language=ru` with deterministic `Idempotency-Key` and safe
`X-Filename`. Lost ASR submission acknowledgments repeat the same ASR API key;
ASR itself owns the provider submission/uncertainty protocol.

For succeeded ASR the source requests `/v1/jobs/{id}/result` and exact fixed
`/v1/jobs/{id}/result/raw`, both authenticated. The returned raw URL must equal
that relative path. It checks job/input SHA/provider/model, preserves the exact
raw HTTP body bytes in its checkpoint, then stores them as `application/json`
under `materials/sha256/XX/SHA`. S3 upload/HEAD can be retried without ASR.

Receipt assets are separate transcript assets with deterministic external ID
`asr:EXPECTED_PROVIDER:INPUT_ASSET:ASR_JOB:CONFIG_IDENTITY`. Core v1 provenance contains
only `origin`, `model_version`, `language`, `processing_status`, `coverage`.
Input hash, operation/configuration/provider events remain in the raw envelope.
`coverage=not_independently_verified`; provider speaker/channel labels are not
candidate/recruiter names. Existing provider transcript assets remain present.

The application adapters (`CoreMaterialsCatalog.legacy_materials` and local
`MaterialsService.legacy_materials`) keep capture-provider text in the existing
`transcripts` array used by assessment. Assets with the protocol prefix `asr:`
are exposed separately through optional `transcriptVariants`, with the same
latest-per-asset projection plus origin/model. A deliberate future default
selection is required before an ASR variant can replace capture text for
assessment; publishing an ASR receipt does not trigger that choice or a new
assessment. Full Core materials/dossier APIs continue to expose all immutable
versions for quality comparison, including when no capture text exists.

Provider identity is explicitly configured and bound into durable idempotency,
raw/result validation and Core provenance; changing a request cannot select it.
Before publication Core is read again. Delivery waits for the actual Core
`acquisition_complete=true` and a ready video channel for video input.
The receipt preserves observed
`video_status` and `acquisition_complete`, sets transcript ready/ASR succeeded,
and is persisted before POST. It is replayed byte-equivalently after a lost ack,
then read back from the same authorized owner materials: exact transcript IDs,
raw reference, ASR origin and asset IDs must be visible. Source does not select
ASR for assessment. Core's current v1 channels API has no conditional-update
field; a concurrent collector write between the final GET and receipt remains
a Core contract limitation.

Delivery statuses: `pending`, `delivering`, `succeeded`, `rejected`.
Transcription statuses: `queued`, `running`, `uncertain`, `succeeded`, `failed`.
Dependency outages reschedule with a persisted backoff; authorization, stale
binding/generation, corrupt data and unsupported remote resolver reject the job.
A known ASR job always resumes polling; source never requests ASR `/retry`.

Remote recordings fail explicitly with `remote_source_resolver_unconfigured`.
Only an operator-injected provider/connection resolver (`Settings.remote_resolvers`)
can supply a bounded stream from Core-attested provider identities; no generic
URL proxy or automatic remote→owned policy change exists. First acceptance path
is Zoom with an already owned video.

## Collector opt-in

Existing collector adds only a bounded IDs-only notification during reconcile:

```sh
HR_MATERIALS_ASR_ENABLED=1
HR_MATERIALS_ASR_URL=http://127.0.0.1:8093
HR_MATERIALS_ASR_TOKEN=<ingress credential>
HR_MATERIALS_ASR_PROVIDERS=zoom
HR_MATERIALS_ASR_ACTIVATED_AT=<explicit Unix epoch activation time>
HR_MATERIALS_ASR_DISPATCH_DB=/var/lib/hr-vision-api/asr-dispatch.sqlite3
```

Default is disabled. Both source and asset must have been created at or after
activation. Only explicit provider allowlist members are notified; no backfill.
At most three network attempts (3 seconds each) per reconcile; successful 202
notifications are checkpointed in the separate dispatch DB. Outage and lost ack
retry with the stable event key. Existing discovery continues on later ticks;
failed notifications never change recording storage policy. Historical material
can only be submitted manually by explicit IDs after authorization.

## Verification

From repository root:

```sh
PYTHONPATH=services/transcription-source/src:services/data-core/src:interface-lab/server python -m pytest services/transcription-source/tests interface-lab/server/test_materials_asr_dispatch.py -q
```

Tests use actual source API/worker, authenticated ASR HTTP fixture, recording
byte stream, content-addressed store fixture and real Core API with a fixture HR
SQLite database. Owned Zoom verifies separate original-provider and SpeechKit
versions visible in the correct candidate dossier. Other cases cover duplicate
events, ASR/receipt ack loss, restart with known job, S3 retry, corrupt input,
foreign candidate/binding, generation change/revocation and lease fencing.
These do not make a paid SpeechKit request or claim recognition quality.
