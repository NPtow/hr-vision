# Candidate Profile Preview v1 — live read-only viewer

Separate service and URL `/candidate-preview/` on existing HR Vision origin. The preview does not replace the cabinet or change its API. Directory, readiness and required fields use the existing manager API. Interviews use a separate canonical Core/Source reader after validating the caller and candidate through that manager API. No direct databases, model calls, re-scoring or grants issuance. Caller bearer goes only to the manager API; server-only Core/Source credentials go only to their configured services and never to the browser. Existing session issuance via `/api/hr/session` stays owned by production app and requires its invitation cookie. No candidate data persisted by preview.

Backend prefix `/candidate-preview/api`:
- GET `/candidates?offset=0&limit=500` loads the complete authorized manager directory, verifies canonical dossiers with bounded concurrency, retains only completed interviews with available active-source recordings, rechecks current membership, then paginates the filtered list. Any verification failure invalidates the response; no partial silent exclusion.
- GET `/candidates/:id/readiness`
- GET `/candidates/:id/interviews`
- GET `/candidates/:id/interviews/:iid/materials`
- GET `/candidates/:id/required-fields`
Readiness/required-fields map to `/api/hr/selection/...`; directory uses the filtered canonical scan above. Interview routes use `server/adapter.py`, `adapter_reader.py` and `adapter_core.py`: exact candidate/interview/recording/transcript identities, selected ready transcript version, source generation and revocation checks. Authorization is checked again after slow reads. The initially deployed manager API did not provide these interview routes. No POST is exposed by preview. The server-only Source client can POST `/v1/materials/access` to resolve existing media access. Readiness can refresh upstream lifecycle metadata; no model/ASR/invitation start.
- GET `/health` no private information.
Host same-origin + explicit safe local development origin, no arbitrary upstream URL, no redirects, timeout/body/concurrency limits, redacted logs, no-store, no-referrer, path allowlist. Upstream manager auth remains source of truth on each request. Serve web/dist at `/candidate-preview/` only.

Frontend modules:
`types.ts` shared loose-but-validated types supplied by data agent.
`api.ts` exports `getDirectory(signal?)`, `getReadiness(id,signal?)`, `getInterviews(id,signal?)`, `getMaterials(cid,iid,signal?)`, `signIn()` using existing browser invitation. Token only sessionStorage using current production key or module memory. Never URL token.
`App.tsx` orchestrates directory -> selected candidate -> details/assessments/interviews; stale request fencing and errors; no synthetic fallback.
`components/AssessmentPanel.tsx` props `{assessment: unknown}`. Use `coreRun.assessment` completed/ready only, level descriptions returned by saved result. Insufficient data distinct, no fake percentages or criterion quotes.
`components/InterviewPanel.tsx` imports getMaterials; own selected interview/recording, video/audio, source-bound transcript, expiring URL renewal preserving position and seek. Transcript parsing helper standalone; `start_ms/end_ms` are converted to seconds. Auth failures clear materials and propagate to the parent.
`components/ProfileHeader.tsx` props `{candidate: Record<string,any>, readiness?:unknown}`; real about/role/location, missing explicit, no editing buttons without write API.
`components/SkillsMap.tsx` props `{assessment:unknown,onSelect?:(id:string)=>void}` optional compact accessible skill visualization of actual selected levels.
CSS owned by design agent; each root/component predictable classes `cp-*`, and CSS agent coordinates with authors.

No public share mode yet: use existing candidate access flow separately later. Do not label manager preview public or generate grants. No fabricated interview clips/quotes. Preserve all paginated candidates, do not hardcode three. Test screen for unknowns, unauthorized, no transcript, unavailable assessment, audio, multiple recordings, refresh/error and keyboard/mobile.
