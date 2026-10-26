# Saved HR interview viewer

The existing selection manager session authorizes `cp_candidates.id`. Core's
candidate dossier supplies every HR `interview.id`; employer meetings are
excluded. `CoreMaterialsCatalog(..., collect=False)` supplies the same owned or
configured remote playback access and saved selected transcript projection used
by the existing player. This feature does not use employer review generation.

GET `/api/hr/selection/candidates/{cid}/interviews` returns
`{candidateId,state,interviews}`. Each interview has `id,status,createdAt,
completedAt,videoStatus,transcriptStatus,recordingCount,parts`.
An empty canonical inventory returns `state=missing,interviews=[]`.

GET `/api/hr/selection/candidates/{cid}/interviews/{iid}/materials` returns
`{candidateId,interviewId,recordings,transcripts,video,transcription,status,
catalogVersion,checkedAt,parts,partTranscripts}`. Standard recordings carry
`id,url,expires,storage,duration,partIndex,kind,copyStatus`. A new GET reissues
expiring playback links through standard access; links are never persisted here.

Each part carries `id,recordingId,kind,state,partIndex,duration,transcriptStatus,
transcriptId,selectionRevision,generation`; the materials response adds
`mediaAvailable,selectedTranscriptAvailable`. Missing and failed assets remain
in this inventory even when they cannot enter the player.

`partTranscripts` contains only exact saved ready primary selections. Match
`recordingId` AND `id==part.transcriptId` AND `part.transcriptStatus==ready`.
Text carries `text` (saved VTT when available), `fullText`, `segments`,
`selectionRevision`, `generation`, and `sha256`; timestamps remain local to that
recording. Never join recording parts or select another transcript variant.
This popup-specific projection permits reading an already ready part while
another part is pending, without changing aggregate transcript readiness.

Normal manager access is checked before and after external reads. Candidate
membership, source generations/revocation and primary selection identity are
rechecked before publishing links or text. A foreign interview ID returns 404;
noncompleted interview 409; revoked materials 410. Core errors remain ordinary
safe API errors. Responses use `Cache-Control: no-store` and no-referrer.

No collection registration, capture, ASR, assessment, meeting, invitation,
notification or SQLite mutation occurs on this path. Source identities and
provider references are omitted from public inventory. Access URLs and source
text are not logged by the adapter or existing HTTP handler.

## Isolated acceptance (2026-10-26)

Run from `interface-lab/server`:
`/opt/homebrew/bin/python3 -m unittest test_selection_interviews test_selection test_core_source_integration`

| Required scenario | Expected result | Evidence / status |
|---|---|---|
| All interviews and parts | Two HR owners, multipart video, exact saved text, no employer meeting | PASS: real synthetic Core HTTP + reader |
| Partial/missing recording | Missing part stays visible; only exact ready primary text exposed; newer alternate excluded | PASS: reader test |
| Playback access fails | Safe media error; saved selected text remains readable | PASS: real HTTP 503 fixture |
| No interview / foreign interview | Honest empty inventory / 404 before link access | PASS: reader test |
| Wrong candidate/owner/revocation | 502/410; no returned video/text | PASS: reader tests |
| Access revoked mid-request | 403 before publication | PASS: real link-read race fixture |
| Primary selection changes mid-request | 409 before publication | PASS: real link-read race fixture |
| Expired link refresh | New link; same saved text and immutable source inventory | PASS: repeated real Core HTTP access |
| Normal manager HTTP access | Anonymous/candidate/employer/cross-site rejected; POST rejected; no-store response | PASS: existing app HTTP handler |
| No processing/delivery | Only allowed Core GETs; reader works with query-only SQLite; outbox unchanged | PASS: isolated tests |
| Actual browser playback and production access | Existing player renders real deployed materials | UNVERIFIED: no deployment or production access in this backend task |

83 tests passed in the combined isolated run. Provider responses are explicit
synthetic fixtures, not evidence of production playback or external delivery.
Backend implementation is ready for independent integration acceptance; full
feature production readiness remains UNVERIFIED until the browser scenario.
