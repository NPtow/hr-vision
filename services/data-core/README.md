# HR materials core

FastAPI catalogue/read service over the existing HR SQLite owner bridge. Optional
collector mode delegates scheduling to the existing materials runtime/repository;
the existing worker and single `materials` Rabbit queue own acquisition and retries.
Collection adds no source database, queue, receipt pipeline, or ASR engine. The API
does not execute media jobs or import the portal application.

The installed artifact must contain the `hr_materials_core` package and pinned
copies of `interface-lab/server/materials_repository.py`, `materials_storage.py`,
`domain.py`, and `feedback.py`. These selected pure modules are dependencies of
the artifact, not runtime imports from the mutable portal deployment directory.
The legacy repository supplies schema/transaction primitives; receipt publication
uses one explicit transaction rather than composing its committing public writes.
Collector mode additionally bundles existing `materials_runtime`,
`materials_service`, `materials_worker`, `materials_capture` and their selected
provider/storage/client dependencies. These remain part of the pinned artifact;
there is no import from a mutable portal directory and no `app.py` donor.

Run `hr-materials-core` (or `python -m hr_materials_core`). Uvicorn serves HTTP and
handles TERM. Startup verifies that the HR database exists with its CP owner
tables, then applies additive catalogue migrations. There is no business-schema
creation, background execution, data deletion, or standalone owner database.

Required environment:

- `CORE_HR_DATABASE`: absolute existing HR SQLite filename.
- `CORE_PRINCIPALS_JSON`: JSON array of server-configured principals. Each entry
  has `token` (at least 32 non-whitespace ASCII characters), `name`, and `role`.
  A source also has fixed `provider` and `connection_id`. A portal may have an
  optional `candidate_ids` list restricting its scope. Omission means trusted
  portal service scope; the portal still authorizes each business user request.

Optional environment:

- `CORE_DEFAULT_CONNECTIONS_JSON` defaults to `{"daily":"daily"}`. It resolves
  legacy room rows without a persisted `connectionId`; it is never request input.
- `CORE_S3_BUCKET`, `CORE_S3_PREFIX` (default `materials`), `CORE_S3_ENDPOINT`,
  `CORE_S3_REGION`. SDK credentials use boto3's standard environment chain.
- `CORE_HOST` defaults to `127.0.0.1`; `CORE_PORT` defaults to `8093`.
- `HR_CORE_COLLECTOR_ENABLED=1` composes `MaterialsService.register_owner` over
  the existing enabled materials runtime using this same HR connection. API
  publication is a no-op; existing worker reconciliation/backfill publishes
  committed pending jobs to the existing queue. Configure that worker separately.
  Existing runtime `HR_MATERIALS_*`/Zoom client settings remain authoritative.
  If `CORE_S3_BUCKET` is absent, runtime S3 storage is reused for configured
  `CORE_DEFAULT_CONNECTIONS_JSON` pairs; source credentials are not needed.
  The default connections include `daily:daily` and `zoom:zoom`.

`POST /v1/owners/{kind}/{identity}/collect` accepts no body, `{}`, or optional
`storage_policy` (`owned`, `remote`, or null). Extra fields are rejected. A scoped
portal principal must own the completed HR record. Room, candidate, provider and
capture identity come only from HR; the trusted reference must validate. The read
transaction closes before calling the injected existing collector. Owner, source
and revocation are checked again before returning 202. Retries reuse existing
source/job IDs. Zoom supports owned retention only; remote returns 400.
The response contains `accepted`, `owner_kind`, `owner_id`, `binding_id`,
`generation`, and catalogue `state`, without claiming acquisition finished.

`GET /health/live` and `/health/ready` are probes. All v1 endpoints require Bearer
service authentication. Source credentials cannot read owners/dossiers or create
bindings, and can access only their fixed provider/connection. Binding requests
must match the completed owner's saved room/capture/session. Core derives the
exclusive-room attestation itself. A portal can increase remote retention to
owned; that invalidates the generation. Repeated remote intents never downgrade.

`POST /v1/bindings/{id}/receipts` is source-only. The receipt key is binding,
generation and receipt ID. An exact retry returns stored IDs and status without
another S3 call; a changed body conflicts. Active owner, current generation and
source authorization are checked again inside `BEGIN IMMEDIATE`, after any S3
HEAD. Receipt, assets, locations, immutable transcript revisions and independent
channel states commit or roll back together. Revoked old generations cannot replay.
Historic assets under a proven child source retain their original source/asset IDs;
an additive ingress association makes access use the publishing root binding that
the source knows. Both original source and root authorization/ancestry are checked.
Unrelated sources or other owners cannot adopt them.

Owned references are accepted only inside a server-configured store using its
content-addressed key scheme. HEAD verifies object existence, size, SHA-256 metadata,
and supplied content type. This is recorded as `verification: head_metadata`, not
independent byte-hash proof. The source owns download/hash verification. Private
bucket immutability and write permissions are deployment requirements. Caller URLs,
buckets/endpoints, credentials, and provider location IDs differing from an asset
external ID are rejected. Access returns a short-lived owned S3 URL or a remote
descriptor, with authorization checked again after signing.

Owner materials return `sources`, `assets`, `transcripts`, and `channels`. Sources
have both `id` and `binding_id`; assets retain `provider_asset_id` and expose its
`external_id` alias. Channel states are independent per binding, with no ASR-success
or complete-speech inference from file presence. Existing worker sources project
their current-generation jobs, discovery completeness, usable catalogue assets,
transcript revisions, and explicit ASR metadata. A stored transcript makes the text
channel ready, not ASR succeeded. Absent complete discovery is `not_recorded`;
pending jobs stay processing, exhausted jobs report error, and contradictory
terminal state without usable material is error rather than perpetual processing.
Revoked sources remain listed with
`revoked: true`, while their material content/access is withheld. Dossiers allowlist
profile fields, return resume metadata, owner/material references and consent records,
and expose channel linkage as booleans without private chat IDs or credentials.

HTTP receipt limit is 24 MiB, enforced before JSON decoding, including chunked
requests. Normalized transcripts support up to 100,000 nonempty segments,
8 Mi characters per segment/text projection, and 20 Mi characters of VTT,
within the aggregate byte limit. Source ASR's 20 MiB result limit fits this envelope.
References are bounded to 500 characters, 100 assets and 10 locations per asset.
Validation errors omit raw input.

Focused verification from repository root:

```sh
python -m pytest services/data-core/tests -q
```

Tests exercise scoped authorization, trusted owner binding, additive history,
root/session families, mixed channel states, exact and concurrent retry, conflicting
receipt bodies, rollback after late receipt failure, S3 mismatch, generation and
revocation races, secret-free dossiers, and transcripts exceeding the old 2 MiB limit.
Collector tests additionally verify scoped authorization, no held writer transaction,
existing job idempotency, Zoom owned-only policy, legacy Zoom access/revisions,
post-scheduling owner changes, read availability after collection failure, and
runtime composition with only portal credentials.
