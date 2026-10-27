# Product recording ingestion and canonical multipart transcripts

## Boundary

The HR API authenticates the owner, records a stable upload intent and a durable assessment intent. It streams files to the private transcription-source API. This independently deployed source probes media, writes private S3 objects, and publishes an immutable Core receipt. Core stores ownership, asset metadata and selected transcript versions; it never starts ASR.

The source scheduler polls the authorized, bounded `/v1/assets` catalogue after an explicitly persisted activation time. Durable per-asset enrollment and the ASR idempotency key prevent duplicate paid work after retries/restarts. The existing provider adapter uses Deepgram; provider choice remains outside Core. Original bytes and transcript history are retained.

Every recording in an owner inventory must have a ready selected transcript before canonical text or an assessment is published. Each part retains its recording/transcript identity and local timestamps. The portal, public candidate profile, assistant and assessment adapters read the same selected versions. New or changed parts invalidate earlier assessments. Existing full-corpus Workbench assessment remains a separate consumer of the same Core selection API.

## Ordinary product action

In an employer vacancy, add/open an intake candidate, choose the interview audio/video files, arrange their order, and click “Добавить запись”. The upload supports up to 10 files, 1 GiB each and 2 GiB total. No fake call room or candidate is created. After private S3 registration the scheduler handles transcription automatically. A browser retry reuses the upload and repeats its product acknowledgement, including when the source has already recovered a lost response.

Aggregate source staging has a durable 2 GiB reservation budget and two raw stream slots. Incomplete abandoned uploads expire after 24 hours; published S3 objects/history and unresolved receipt checkpoints are not deleted. Assessment intent exists before upload completion, so a closed tab does not suppress later assessment processing.

## Release protocol

1. Verify no live interview or active material/ASR/assessment operation. Preserve the latest main branch, protected receiver guards and exact package allowlist.
2. Package the reviewed Core manifest and source image, then verify their SHA256 on the host.
3. Under the existing deployment lock stop HR writers, back up the HR database, explicitly migrate `media_sources` to the strict third owner type (`intake_id` FK), start the new Core and verify readiness. Migration is never performed by Core startup.
4. Update only source containers; retain the ASR containers and `deepgram-v1` revision. Enable the catalogue watcher with a current activation floor; disable the old Zoom-specific ASR dispatch. No historical paid replay.
5. Update the isolated assessment engine with multipart evidence fields. Configure the portal's private source-upload credential. Deploy the portal through the protected CI receiver.
6. Acceptance: normal browser sign-in, synthetic intake, two small speech files via the file picker, automatic S3 → source → ASR → primary → UI/assessment. Inspect server metadata read-only to verify exact recording/transcript identities and no extra paid jobs after refresh. A manual `/v1/recordings` request is not acceptance.

## Validation scope

Tests cover exact owner/workspace authorization, upload ACK loss, immutable receipt retry, typed FK migration rollback with populated children/views/triggers, S3 integrity, bounded streams and disk reservations, catalogue cursor restart, primary selection recovery, complete multipart projection and evidence provenance. Live acceptance evidence is recorded separately by the operator; passing unit tests alone does not prove a production cycle.
