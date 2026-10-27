# Zoom Meeting SDK — isolated proof of concept

This is a local integration with an existing paid Zoom Meetings account. It does
not replace Daily, import the production backend, or change candidate/recruiter
routes. All participants use Zoom **Meeting SDK Client View 6.10.0**. Zoom owns
prejoin, camera/microphone permissions, media, reconnection and leaving.

## Run

```sh
cd '/Users/NIKITA/Documents/ChatGPT/HR Vision'
python3 interface-lab/prototypes/zoom-meeting-sdk/backend.py --port 4321
```

Open the private host URL in `.local/host-invite.txt`. The ordinary preview is
<http://127.0.0.1:4321/>. Credentials are read on startup from `.env.local`;
restart the local server after changing them. Both private paths are ignored by
Git. The server binds only to loopback; guest links work on this computer until
an explicitly configured HTTPS deployment/tunnel is added. Do not send localhost
invitations to candidates.

Optional existing Storybook entry:

```sh
cd interface-lab
HR_BUILD_TARGET=mockups npm run storybook -- --port 6025
```

Story ID: `hr-vision-zoom-poc--workspace`. Open
<http://127.0.0.1:6025/iframe.html?id=hr-vision-zoom-poc--workspace&viewMode=story>.
For the actual connection test, prefer the standalone preview to remove the
extra Storybook iframe from the permission chain.

## Account configuration

Use two internal apps in the **same account as the licensed meeting host**:

1. General app → Features → Embed → Meeting SDK. Use its development Client ID
   and Client Secret (not the retired SDK Key/Secret).
2. Server-to-Server OAuth app, activated with these narrowly selected scopes:
   `meeting:write:meeting:admin`, `user:read:token:admin`,
   `cloud_recording:read:list_recording_files:admin`.

Names of local settings are in `.env.example`; secrets never belong in browser
variables, source files, screenshots or logs. The local config file should be
mode `0600`. Host ID can be the existing licensed host's email.

The SDK app and server OAuth app have **different credentials**. Guest access is
role 0 to a meeting created by this backend. Host access is role 1 plus a ZAK
from `GET /users/{configuredHost}/token?type=zak`. Host/guest cookies are distinct;
opening a guest invitation in a browser that already has a host session does
not promote the guest. The explicit mode chooses which authorization to check.

Current Zoom documentation describes some embedded-host ZAK flows using user
OAuth. This PoC must prove actual S2S ZAK host start; a successful guest join or
token request is not enough. If Zoom rejects that route, stop and use documented
user OAuth rather than removing access checks.

## Boundaries

- `backend.py`: local authentication, meeting creation, role-bound SDK JWT,
  host ZAK, recording metadata and private downloads. No production database.
- `web/client.html` / `client.js`: separate SDK document. React 18.3.1 is pinned
  to the SDK peer version, isolated from the host app's React 19 and CSS.
- `web/index.html` / `app.js` / `style.css`: notes, questions and recording
  controls alongside a stable SDK iframe. Optional panel failures and storage
  errors do not gate creating/joining/leaving a meeting.
- `test_backend.py`, `test_client.mjs`, `test_shell.mjs`: offline contract and
  failure-injection checks, not simulated evidence of real media delivery.

Creation is explicit, idempotent and never retried automatically. If Zoom's
creation response is lost, the same request remains unresolved; inspect Zoom
before starting another creation. Notes are local drafts, not synced to the
production candidate profile. Guest invite fragments are consumed and removed
from the URL; provider credentials are never put in the invite.

Recording is off when a meeting is created. Start recording using the Zoom UI
with the participants' consent. The independent recording panel reads provider
metadata; only completed files can be downloaded to `.local/recordings/`. The
download is bounded to 200 MiB / 120 seconds for this short PoC. Processing can
continue after the call; leaving does not wait for it. A provider `ready` status
is distinct from local download and actual playback verification.

## Verification

```sh
python3 -m unittest discover -s interface-lab/prototypes/zoom-meeting-sdk -p 'test_backend.py' -v
node --experimental-vm-modules --test interface-lab/prototypes/zoom-meeting-sdk/test_client.mjs
node --test interface-lab/prototypes/zoom-meeting-sdk/test_shell.mjs
cd interface-lab && npm run typecheck
```

Real acceptance requires two participants to hear and see each other, then a
panel fault during the call, panel recovery without rejoining, normal exit, and
retrieval/playback of a recording containing both sides. SDK `joined` means only
that Zoom acknowledged joining. Local tests cannot establish reliability on the
previously failing candidate computer or network. Browser/process exhaustion
can still affect both iframe and side panel.

## Live check: 2026-10-25

The host started a real Zoom meeting with S2S ZAK. A Chrome guest was admitted;
Zoom showed two participants. Disabling and restoring the notes panel left the
meeting and cloud recording running. Ending the meeting returned to the local
exit screen. After processing, the recording panel downloaded both MP4 and M4A
through the backend (58.688 seconds); both files decoded without errors.

This does **not** pass the audio/video acceptance gate: the in-app-browser host
reported denied microphone access, both cameras were off, and the downloaded
audio was effectively silent throughout (mean -91 dB, peak -84.3 dB). A human
two-device conversation with audible playback remains required. Evidence is in
`output/zoom-poc-2026-10-25/verification.json`; media remains in ignored private
`.local/recordings/`.

A second meeting used Chrome as host with camera and microphone enabled. The
guest joined and acknowledged cloud recording. Both resulting files downloaded
through the backend and fully decoded: MP4 151.280 s / 2,239,629 bytes; M4A
151.274667 s / 284,156 bytes. A frame at 75.640 s contains a very dark camera
image. Audio remained effectively silent (mean -90.3 dBFS, peak -54.1 dBFS; all
151.274667 s below -50 dBFS). No intelligible speech or two-way audio was proved.
The second meeting was ended and Chrome no longer indicated device capture.
Do not treat the successful recording pipeline as passing the full media gate.

## Sources / provenance

- [Official CDN sample](https://github.com/zoom/meetingsdk-web-sample/tree/master/CDN):
  supported ESM import, React peer version and init/join sequence.
- [Client View import and CSS isolation](https://developers.zoom.us/docs/meeting-sdk/web/client-view/import/).
- [Zoom iframe permissions](https://developers.zoom.us/blog/meeting-sdk-iframe/).
- [Meeting SDK authorization](https://developers.zoom.us/docs/meeting-sdk/auth/).
- [User login and own-account anonymous guests](https://developers.zoom.us/docs/meeting-sdk/user-login/).
- [Internal OAuth app setup](https://developers.zoom.us/docs/internal-apps/create/).

The small adapter follows the official SDK lifecycle; it does not copy or replace
Zoom's meeting UI. SDK usage remains subject to Zoom's terms. The NASA skill is
an engineering adaptation for isolation and verification, not certification.
