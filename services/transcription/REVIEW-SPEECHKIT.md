# Independent review: SpeechKit pipeline

Reviewer scope: services/transcription, services/transcription-source, narrow collector dispatch integration. No feature edits or production operations. Implementation review completed on 2026-10-25; final verdict and verification limits are below.

## Protocol and integration constraints verified before implementation

- Official SpeechKit documentation explicitly says URI query parameters are ignored for private Object Storage files. A presigned URL alone cannot authorize provider reads. Use the recognition service account's object-read permission and a canonical S3 URI. Evidence: https://aistudio.yandex.ru/ru/docs/speechkit/stt/api/transcribation-api-v3, paragraph under `uri` in cURL example (retrieved 2026-10-25).
- Official speaker labeling: mono, FULL_DATA, at most two labels; channel labels cannot prove a person's name or interview role. Evidence: https://aistudio.yandex.ru/ru/docs/speechkit/stt/speaker-labeling.
- REST result stream can wrap events inside `result`; both final and finalRefinement refer to the same utterance via audioCursors.finalIndex/finalRefinement.finalIndex. Avoid emitting duplicate speech from refinements. Evidence: official WAV example above and https://aistudio.yandex.ru/ru/docs/speechkit/stt-v3/api-ref/AsyncRecognizer/getRecognition.
- Core strict Provenance permits origin/model_version/language/processing_status/coverage only. Extra asset/job fields reject receipt (422); preserve richer provenance in the raw envelope and stable external_id.
- Core verifies raw_ref against the provider/connection's configured storage and exact `storage.object_key(sha256)` key. An arbitrary staging prefix is not accepted for permanent transcripts.
- Core receipt publication overwrites every per-binding channel field; ASR delivery must preserve existing video/acquisition channel facts.
- Existing ASR explicit retry permits uncertain requests. SpeechKit durable submit intent must still prevent another paid POST after an unknown submit outcome.

## Final implementation review

Completed after both workers froze their final implementation and architect supplied the TCP integration/deployment delta.

## Findings addressed during implementation

1. Optional collector dispatch originally reposted every accepted recording forever and could spend 10 seconds per eligible asset on an unavailable coordinator. It now persists accepted event keys in a separate SQLite checkpoint and limits each reconciliation to three attempts with three-second socket timeout. This substantially bounds collector impact; the hook is opt-in and bounded by provider/activation time.
2. Source ASR submit originally buffered an unbounded response and placed its SHA checkpoint outside temp cleanup. It now streams the bounded response and always removes a downloaded recording when that checkpoint loses its lease. Independent regression tests cover these paths.
3. Idle source worker readiness originally expired despite an available worker. A worker-owned heartbeat now runs independently of the scheduler; download chunks renew the job lease.
4. SpeechKit parsing originally ignored a stream error envelope after valid final events, which could accept a partial result. It now rejects error events and records bounded provider warning codes.
5. A new TestClient test depended on unavailable httpx2 in the existing ASR test environment. It now uses actual uvicorn HTTP, retaining authorization/readiness coverage without a new runtime dependency.
6. Core channels can shadow the collector's derived acquisition state after the first receipt. Source now waits for acquisition_complete before the first paid ASR submit and before constructing a new immutable receipt. It copies the current settled video/acquisition channel facts and reuses the exact body after acknowledgement loss. Core remains authoritative for generation/owner changes; later channel mutation outside that settled lifecycle remains a Core contract limitation.

## Independent checks so far

- ASR service: 47 unittest tests passed using the edited checkout on absolute PYTHONPATH, existing transcription venv, real FFmpeg decode, local REST fixture and crash/restart paths (2026-10-25). This run preceded the Deepgram adapter addition.
- Source + collector dispatch: 20 pytest tests passed using /tmp/md-services-venv, real Core API and isolated HR owner schema, including dossier visibility, distinct preserved Zoom transcript, revocation/generation fences, ASR/S3/Core acknowledgement-loss paths and response/temp bounds. Subsequent provider-neutral/acquisition changes need final rerun.
- git diff --check passed during incremental review.

## Expanded provider scope

User steering relayed by architect adds Deepgram as the first live provider. Its synchronous charged POST must retain the durable submit-intent fence, save raw response before parsing and never automatically resubmit an uncertain request. Deepgram provider labels do not establish person identities. Official REST/authentication, Russian language support, utterance fields and diarization semantics verified against https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded, https://developers.deepgram.com/docs/models-languages-overview and https://developers.deepgram.com/docs/diarization. Final review of this delta completed; explicit diarize_model=v2 was selected by the architect from user steering. Speaker labels remain provider labels.

## Source/coordinator final scope verdict

No remaining blocking findings in the source/coordinator and collector hook scope after fixes. Independent final run: 53 passed (22 source/dispatch tests plus existing materials worker/provider-scope checks), 4.24 seconds using /tmp/md-services-venv. Provider-neutral Deepgram/Yandex raw/result identity and acquisition gates are included. Security/correctness: satisfactory for the scoped, configured service boundary; performance: bounded streaming and dispatch; maintainability: explicit HTTP/storage boundaries with separate state. Core transaction fences and immutable Zoom version preservation are positive evidence. Broader Core channel mutations after settled acquisition remain a documented existing wire-contract limitation, not a claim of CAS semantics.

Team-wide code review is complete below; production acceptance and live provider quality remain separate checks owned by the architect. No live provider call, production change, credential access or source upload performed by reviewer.


## Final independent code verdict

**PASS: no remaining blocking implementation findings in the reviewed scope.**

- Security: pass for configured service boundaries. Candidate/owner/asset/generation checks are delegated to authenticated Core reads and repeated before publication; no arbitrary requester media URL, guessed person role, public raw endpoint, HR DB import or secret logging was introduced. Deployment has separate env/state mounts, non-root UID, read-only root, fixed loopback API listeners and no HR database mount.
- Correctness: pass. Paid submit intent precedes POST; unknown outcomes stay locked; known SpeechKit operation resumes via GET, late operation/raw checkpoints survive lease expiry, old workers cannot publish canonical results. Deepgram raw is saved before parsing and reused after restart. Source delivery retries do not resubmit ASR, receipt replay is stable and original Zoom text remains distinct/immutable. The baseline assessment boundary was reviewed against f0478bc0; later mainline Jev consumer compatibility is addressed by the separate projection follow-up below.
- Performance: pass within documented v1 limits. Bounded input/result streams, checked free disk, CPU/file-size decoder limits, single-worker concurrency and durable bounded collector dispatch are present. Full four-hour/max-response memory/load acceptance is not claimed.
- Maintainability: satisfactory. ASR, source coordination and Core publication remain separate contracts and stores; additive SQLite migration is serialized across local process startup. This remains a single-host SQLite design.

Independent final verification against the edited checkout:

1. **58 ASR unittest tests passed** in 18.35 seconds with absolute PYTHONPATH using the existing transcription venv. Includes actual FFmpeg, real local REST/binary request fixtures, exact Deepgram parameters, malformed JSON preservation, rate-limit/timeout/error/size handling, concurrent migration, input corruption, killed subprocess recovery and paid-submit count assertions.
2. **53 source/hook/materials regression pytest tests passed** in 4.24 seconds using /tmp/md-services-venv (22 new source/dispatch checks plus existing worker/provider-scope tests).
3. **1 full TCP pipeline integration test passed** independently in 3.09 seconds: real HTTP source -> ASR -> Deepgram fixture -> object-store fixture -> real Core API/isolated HR schema -> candidate dossier. Actual FFmpeg runs; original Zoom transcript persists; committed Core acknowledgement loss followed by a fresh source Runtime/reopened SQLite replays the same receipt with one provider POST. This test uses a fresh source worker instance, not a second OS process; separate ASR tests do kill/restart OS processes.
4. **git diff --check passed** after the final code delta.

Deployment review: configured six-process memory caps sum to 1408 MiB (about 1.38 GiB), in addition to the existing stack/OS; live available RAM must be checked before startup. The maximum 20-24 MiB parsed JSON memory envelope has not been load-tested. ASR/source credentials and volumes are separate; Deepgram ASR receives neither Core nor S3 secrets. Host networking is explicitly required for existing loopback Core/RabbitMQ and does not mount host HR data.

No live provider call, private credential read, source upload, deployment or production restart was performed by this reviewer. Live ASR quality, actual provider permissions and actual installed worker readiness remain architect acceptance checks. No claim of full interview coverage or person identity follows from these tests.

## Additional provisioning review

Architect added infra/deployment/services/transcription/provision.py after the application review. Reviewed secret stdin handling, credential reuse, protected env files, isolated RabbitMQ definitions, separate source/ASR state, existing Core principal preservation, active-call/material-job guards and online SQLite backup. RabbitMQ primary command source confirms import_definitions with omitted file reads stdin; no command-line secret is required.

Blocking operational finding corrected: systemctl restart success alone does not prove a Type=simple service stayed up. Activation now checks three consecutive systemd-active/HTTP-ready results, authenticates both new principals against a deliberately absent binding (404), and restores/restarts/verifies previous principals if readiness fails. Guard also covers ml_sessions when present. Candidate-scoped prior principals use the same absent-binding probe, avoiding a misleading candidate-scope 403 during rollback verification.

Independent provision tests: 6 passed; git diff --check passed. Auth probes now use an explicit no-proxy/no-redirect opener; final code inspected and six provision tests independently rerun successfully. Reviewer did not execute prepare, activate-core or any live command.

**Final provisioning code verdict: PASS.** No remaining blocking operational code findings in the reviewed provision.py scope. Application, integration, deployment configuration and provisioning review are frozen at this point; installed-host readiness, resource availability, image identity and live ASR remain architect verification tasks. No live provisioning or activation performed by reviewer.


## Latest mainline assessment and transcript projection follow-up

Architect discovered mainline commit 76386691 introduced live Jev assessment_jobs.Bridge.snapshot, which requires exactly one transcript from the legacy materials projection. Publishing an additive ASR asset into that legacy list would therefore block existing assessment even without selecting the new text. The earlier baseline assessment observation applied to f0478bc0 and did not establish compatibility with this newer mainline code.

Both CoreMaterialsCatalog and local MaterialsService now keep capture transcripts in the existing transcripts list and expose reserved external_id/provider_asset_id prefix asr: assets only in optional transcriptVariants. Default transcript IDs/text remain unchanged; variants retain text/segments/warnings plus provider/model. Core materials/dossier still retain all immutable versions. ASR-only data does not become the default transcript automatically.

Independent follow-up: **69 tests passed plus 8 subtests**, 4.40 seconds, covering both adapters, real persisted Zoom+ASR Core receipts, latest-per-asset versions and existing source/service behavior. An additional read-only cross-version probe executed the actual assessment_jobs.py from 76386691 and confirmed Bridge.snapshot returns the identical tuple, model payload and sourceHash before/after an ASR variant is added to the real CoreMaterialsCatalog projection. Projection scope verdict: **PASS**.

Release hook producer/receiver delta on the f047-based checkout: **77 receiver tests passed**, including real producer archive layout, exact hook file/checksum and complete prior runtime acceptance. Final merge must preserve the newer mainline assessment_jobs.py producer entry and PRE_JEV/current Jev receiver layouts before adding the hook. Latest merged runtime-history review remains pending until that merge exists.

New mainline Jev also reserves loopback8096. Root deployment/provision/docs were corrected to separate ASR8106 and source8107; reviewer confirmed those three configuration surfaces agree. Installed-host free-port/readiness verification remains architect-owned. No live ASR receipt or production change was performed by reviewer.
