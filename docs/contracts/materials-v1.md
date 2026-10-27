# Materials services v1 — integration contract

Historical broader source-service contract. The approved Zoom rollout uses the
simpler [Zoom plan](zoom-cutover-plan.md): stateless Zoom API, existing collector
inside core, one existing queue. External source jobs/receipts are not part of
that production path; no source principal is configured for it.

Core stores/links/validates data. Source owns provider operations, discovery, downloads,
video policy defaults, transcription calls and result delivery. Transcription API is
owned by the separate `codex/transcription-service` worktree; do not copy its logic.
Every service ships as its own process and pinned source artifact, independently of
`/opt/hr-vision-api/current`. Source has no access to the HR SQLite database.

## Core authentication and bindings

Bearer principals are configured server-side, never supplied by a request. Role
`portal` can bind existing business owners and read dossiers. Role `source` has a
fixed `provider` and `connection_id` and can only read/write its existing bindings.
Only portal may choose/change an owner or increase retention from remote to owned.
Default policy is supplied by the source settings, then pinned into the binding.

POST /v1/bindings (portal):
`{schema_version:1, owner_kind:"interview"|"meeting", owner_id, provider,
connection_id, source_ref:{capture_instance_id,room_id,room_name,...}, storage_policy:"remote"|"owned"}`.
Core verifies owner exists and is completed, and source identity matches the
server-stored room for that owner through the legacy owner bridge (no provider I/O).
Returns `{binding_id,generation,owner_kind,owner_id,provider,connection_id,
source_ref,storage_policy}`. Same identity is idempotent; owner conflicts are 409.
GET /v1/bindings/{id} (portal or owning source) returns that binding.

## Receipts, pure core ingress

POST /v1/bindings/{id}/receipts (owning source only):
`{schema_version:1,generation,receipt_id,assets:[...],
 video_status,transcript_status,acquisition_complete,transcription_status}`.

Status values: video/transcript: processing,ready,error,not_recorded,unsupported;
transcription_status: not_requested,awaiting_configuration,queued,running,succeeded,failed.
Ready is per material/channel; acquisition_complete and transcription status are
independent. No ASR success or complete speech coverage is inferred from file presence.

Asset: `{external_id,kind:"video"|"audio"|"transcript",state,
 metadata:{duration?,format?,session_id?}, locations:[...], transcript?:{raw_ref,normalized,provenance}}`.
Location: `{backend:"provider"|"s3",object_key,state:"available"|"verified",size?,sha256?,content_type?}`.
Provider object_key is its stable recording/transcript ID. Owned keys are content-addressed
under a configured store; core verifies S3 HEAD/metadata/size against configuration.
No caller-selected bucket/endpoint or signed/provider URL in persisted references.
Normalized transcript follows services/transcription/CONTRACT.md, schema_version=1,
segments with nullable start_ms/end_ms/speaker, full_text,text,vtt,warnings.
Provenance preserves origin, model_version/language when known, processing_status,
coverage; does not invent timestamps or speakers. Raw bytes stay in owned S3.

Receipt transaction validates source principal, binding generation and active owner,
then atomically upserts assets/locations/transcript versions plus receipt record.
Idempotency key is (binding_id,generation,receipt_id); exact retry returns prior result;
changed body under same key returns409. Older generations/revocations cannot publish.
An external asset cannot be reassigned to a different owner. Existing catalogue IDs
and transcript revisions must remain readable (additive migration, no history delete).

## Core reads

GET /v1/owners/{kind}/{id}/materials (portal): stable metadata, independent channel states,
transcript text/segments and opaque locations. No Daily calls. GET /v1/candidates/{id}/dossier
(portal): candidate profile, resume metadata, interviews/meetings/material references,
consent records and channel linkage summaries; never passwords/tokens/chat credentials.
GET /v1/assets/{id}/access (portal): owned signed S3 access OR remote descriptor with
binding_id + external_id + source identity. Portal resolves remote through source API.

## Source API (trusted portal caller; provider key lives here)

GET /health/live; GET /health/ready; GET /v1/capabilities (configured, default policy).
POST /v1/calls/room {meeting_id,recordable,screenshare} -> original room shape.
POST /v1/calls/token {room,properties} -> token result. Whitelist supported token properties.
POST /v1/calls/start {room,recording_instance_id,start_recording,start_transcription}
-> separately acknowledged recording/transcription requests. Preserve explicit user consent
checks in portal. ASR provider/model is never silently enabled by the new materials pipeline.
POST /v1/calls/finish {room} -> acknowledged result; provider closure proof remains source-owned.
POST /v1/calls/materials {room,known?} -> legacy material projection for demo/lab only.
POST /v1/jobs {binding_id,generation} ->202 durable job. Source fetches authorized binding
from core; caller cannot pass candidate identity or arbitrary source URL. Idempotent by
binding+generation. Acquisition polls settled provider artifacts with finite retries.
POST /v1/materials/access {binding_id,external_id} -> short-lived provider URL; source resolves
only an artifact proven to belong to this binding and returns no bearer headers to browser.

Source's private DB keeps pipeline progress, attempts, receipts and ASR job identifiers.
Durable state is committed before queue publication; scheduler recovers delivery failures.
Celery uses source queue/vhost; no core DB access. Store receipts before sending and retry
same receipt_id/body. Never mark delivered before a successful core receipt response.

Transcription service: POST /v1/normalize?format=... raw provider VTT/SRT; future ASR via
POST /v1/jobs raw media + Idempotency-Key + X-Filename, then GET status/result. Source
owns input fetch and temporary files even for remote retention. If ASR is not configured,
keep explicit awaiting_configuration; video stays accessible. No production ASR selection.

## Portal transition

Portal keeps existing authenticated routes and CallMaterials response shape. It owns
business consent/authorization; it uses typed source methods instead of raw Daily REST.
Completion writes a durable outbox row in the SAME SQLite transaction as completed and
business event. A separate dispatcher creates core binding and enqueues source job;
network is never executed within the completion transaction. Retries are idempotent.
New mode is feature-gated; live historical catalogue migration and rollback are explicit.
No changes to other chats' services/transcription or their normalization bridge.
