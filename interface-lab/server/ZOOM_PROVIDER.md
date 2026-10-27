# Zoom provider: parallel pilot

`zoom_media.py` is a provider adapter, not a new authorization service. Existing
meeting ownership, capability links and durable creation claims remain in the
product. Daily stays the default and its provider implementation is unchanged.

## Environment

Supply these through the server secret environment, never browser config or Git:

```dotenv
HR_ZOOM_ENABLED=0
HR_ZOOM_CONNECTION_ID=zoom
HR_ZOOM_HOST_MAP_JSON={"actual-product-recruiter-id":"licensed-host@your-domain.example"}
ZOOM_MEETING_SDK_CLIENT_ID=
ZOOM_MEETING_SDK_CLIENT_SECRET=
ZOOM_S2S_ACCOUNT_ID=
ZOOM_S2S_CLIENT_ID=
ZOOM_S2S_CLIENT_SECRET=
```

There is no fallback to the PoC's single `ZOOM_HOST_USER_ID`. Map exact product
actor IDs to licensed users in the connected Zoom account explicitly. A renamed
connection ID cannot silently read/end an old connection's rooms. Keep the same
connection ID when rotating credentials for that same account; use a new ID for
a different account and retain the old connection until its meetings/materials
have finished.

- `selection_enabled`: pilot flag, keys and a valid host map; controls **new choices**.
- `configured`: SDK + S2S keys; existing joins still work with the pilot flag off.
- `capture_configured`: S2S keys; existing recordings do not depend on SDK keys,
  new-choice flag, or the current recruiter mapping.

## Required S2S granular scopes

| Operation | Scope |
|---|---|
| Create own-account meeting | `meeting:write:meeting:admin` |
| Host ZAK | `user:read:token:admin` |
| Current instance state | `meeting:read:meeting:admin` |
| Exact ended instance proof | `meeting:read:past_meeting:admin` |
| End meeting | `meeting:update:status:admin` |
| Recording files | `cloud_recording:read:list_recording_files:admin` |

The first PoC had only create, ZAK and recording scopes. On 2026-10-25, after
owner approval, the three additional read/end scopes were enabled in the existing
S2S app. The app remains active. A fresh OAuth token included all required scopes;
a read-only check of the existing completed PoC meeting returned `ended` with its
exact session UUID. No meeting was created or ended during that verification.
See `output/zoom-parallel-2026-10-25/zoom-scope-verification.json` at repository root.
This account permission check does not mean production credentials, the pilot host
map, or the production feature flag have been installed.

## Boundaries

- The caller must durably claim a creation request before calling `room()`, save
  the returned room before issuing credentials, and retain uncertain outcomes.
  Zoom does not promise idempotent create by this product's request ID. The adapter
  performs exactly one create and never falls back to Daily on an ambiguous result.
- Rooms are scheduled, non-PMI, waiting-room enabled, join-before-host disabled,
  camera initially off, automatic recording disabled. Recording stays a native
  Zoom UI action with the product's consent policy; this adapter never starts it.
- `room.password` and `room.zoomHostId` are server-only. The public room URL has
  query parameters removed. Only the authenticated participant receives SDK
  credentials. ZAK is only returned when the server supplies `owner=True`.
- SDK JWT is pinned to version 6.10.0's documented `appKey/mn/role/iat/exp/tokenExp`
  contract. SDK and S2S credentials are separate pairs.
- A numeric Zoom meeting ID is reusable. `get_state()` exposes the observed
  `providerSessionId`; the parent may bind the first live instance after a
  server-authenticated joined signal, before registering a material source.
  If that signal failed and Zoom already ended natively, an **unpinned** room
  can recover only from its current meeting metadata's exact UUID followed by
  `/past_meetings/{that UUID}` proving the same number, UUID and end time. The
  parent persists a first pin only for `started` or proven `ended`, never for
  `waiting` or `unknown`. A pinned room always checks its saved UUID; another
  active instance is refused, and another waiting UUID is never substituted.
  If the current meeting has disappeared and the saved UUID cannot be proved,
  recovery remains unresolved: the adapter does not enumerate history or guess.
  An existing source must never be rebound to a later UUID. `recordings()` only
  queries the exact saved UUID and verifies the number and every provided file
  meeting UUID. It does not look up the latest recording by numeric ID.
- `finish()` refuses to end a different live UUID. `waiting`, empty recordings,
  or HTTP 404 are not proof of a completed call. A successful end command requires
  the documented HTTP 204; a repeated end can succeed from exact past-instance
  evidence. Auth, limit and network errors remain failures.
- `get_state()` returns `started`, `waiting`, `ended`, or `unknown`. Only exact
  past-instance evidence produces `ended`; `unknown` is not success.
- Native Zoom End/Leave is observed through host-only
  `/api/hr/recruiter/{interview,meeting}/reconcile` and
  `/api/hr/employer/call-reconcile`. They return `{completed,status}` and never
  send Zoom's end command. An ordinary host/guest departure leaves a live call
  intact. Only `ended` for the exact pinned UUID commits the existing completed
  transition and registers that session with the shared materials catalogue.
  Missing first joined receipts may be recovered from the same provider proof;
  a changed saved room/provider binding is rejected.
- The existing call-cleanup worker also checks at most five due Zoom owners per
  pass, with durable 30-second due times in `cp_zoom_lifecycle_checks`. It recovers
  native ends when the browser has closed. Completed owners without a materials
  source are retried independently of the optional full backfill. Completed
  reconcile requests retry idempotent catalogue registration as well, repairing
  a crash between business completion and source registration.
- The adapter never returns an authenticated recording URL through legacy
  `materials()`. Catalog ingestion owns recording download and playback.
  `resolve_download()` is private transport metadata with an Authorization
  header; the worker must validate every redirect and never publish that header.
- No raw Zoom error message/body or private URL is put into application errors.
  Only HTTP status and numeric Zoom error code are retained for diagnosis.

## Verification

Run `python3 -m unittest test_zoom_media -v` from `interface-lab/server`.
These are offline boundary tests, including role isolation, no create retry,
finish/current-session identity, exact recording identity, redacted errors,
download origins, token caching, and rollback of the new-choice flag.
They do not establish two-way audio, browser recovery, or account policy.

## Official references checked 2026-10-25

- [Meeting API](https://developers.zoom.us/docs/api/meetings/): create, current/past
  instance state, `PUT /meetings/{meetingId}/status` with `action: end`, recordings.
- [Granular scopes](https://developers.zoom.us/docs/internal-apps/oauth-scopes-granular/).
- [SDK authorization](https://developers.zoom.us/docs/meeting-sdk/auth/).
- [Client View join](https://developers.zoom.us/docs/meeting-sdk/web/client-view/meetings-webinars/).
- [Server-to-server OAuth](https://developers.zoom.us/docs/internal-apps/s2s-oauth/).
- [Download URLs and redirects](https://developers.zoom.us/docs/api/using-zoom-apis/).
