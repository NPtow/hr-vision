# Daily / Zoom provider switch

Zoom is a second provider for initial interviews and employer meetings. Daily
remains the default for meetings without a saved choice. Every authorized
organizer uses the same switch; there is no pilot cohort. The candidate follows
the provider saved for that meeting. Selection locks when room creation starts,
so concurrent participants cannot create or enter different providers' rooms.
There is no automatic fallback from an uncertain Zoom create to a new Daily room.

## Configuration

Keep SDK and server credentials in the private server environment, outside Git,
frontend configuration, logs and URLs:

```dotenv
HR_ZOOM_ENABLED=0
HR_ZOOM_CONNECTION_ID=zoom
HR_ZOOM_DEFAULT_HOST_ID=licensed-host@your-domain.example
HR_ZOOM_HOST_MAP_JSON={}
ZOOM_MEETING_SDK_CLIENT_ID=
ZOOM_MEETING_SDK_CLIENT_SECRET=
ZOOM_S2S_ACCOUNT_ID=
ZOOM_S2S_CLIENT_ID=
ZOOM_S2S_CLIENT_SECRET=
HR_ZOOM_RECORDING_RETENTION_ENABLED=0
```

`HR_ZOOM_DEFAULT_HOST_ID` explicitly assigns a licensed Zoom host to organizers
without an entry in `HR_ZOOM_HOST_MAP_JSON`. An exact product actor-ID mapping
takes precedence. The adapter never inherits the PoC's `ZOOM_HOST_USER_ID`.
This makes the switch available to all authorized organizers without individual
cohorts; it does not increase the Zoom subscription's capacity. No additional
participant limit is imposed by this adapter. Zoom's licensed-host concurrency
limits still apply: Pro allows one concurrent meeting per host, while licensed
Business, Education and Enterprise hosts can normally run two. Additional
capacity requires the appropriate licensed hosts or Zoom entitlement.
[Zoom concurrency documentation](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0082195).

- `selection_enabled`: feature flag, SDK/S2S credentials and a valid host mapping
  or explicit default; controls new selections.
- `configured`: SDK/S2S credentials; an existing Zoom room remains joinable if
  new selection is disabled.
- `capture_configured`: S2S credentials; copying existing recordings does not
  depend on SDK keys, selection enablement or the current host map.

The selected host and connection are persisted with the room. Changing a default
does not reassign existing rooms. Keep the same connection ID for credential
rotation within the same Zoom account. A different account needs a new identity;
retain the old connection until its meetings and materials have been handled.

## Native call and consent

The entire call stage is Zoom Meeting SDK Client View, pinned to 6.10.0 and
isolated in its own same-origin iframe. Camera, microphone, participants, sharing,
recording and ending use native Zoom controls. Product questions and notes stay
beside the call. No duplicate product hang-up or recording toolbar is shown.
Notes or coaching failures do not prevent joining or interrupt Zoom transport.

New rooms are scheduled, non-PMI and passcode protected. They request
`waiting_room=false`, `join_before_host=false`, camera initially off and
`auto_recording=cloud`. The adapter verifies that Zoom returned these settings;
it does not silently accept an account policy that overrides them. After the
host starts, a participant enters without a separate host admission click.
Automatic recording is performed by Zoom, not a custom browser recorder.
[Create meeting settings](https://developers.zoom.us/docs/api/meetings/).

New rooms persist `recordingPolicy='automatic-cloud'`. Each participant must
explicitly submit boolean `consent: true` before the server issues their SDK
credentials. This applies to recruiters, employer hosts, authenticated candidates
and personal guest links. A host's consent does not stand in for a guest's, and
an earlier saved receipt does not bypass a later join check. Receipts retain the
participant identity and meeting. Existing manual-recording rooms are not patched
into automatic recording; their saved policy remains unchanged.

Only an authorized host receives role 1 and a ZAK. Guests receive role 0 without
ZAK or host account data. SDK signatures are meeting/role bound. The parent/frame
bridge checks both origin and source; secrets are sent in the authenticated
response and frame message, not query strings.

## Completion and recording identity

Room creation is durably claimed before the single Zoom POST. Ambiguous outcomes
remain unresolved instead of creating another room on retry. A numeric meeting
ID can be reused, so recording lookup uses the exact saved session UUID.
Authenticated joined receipts pin the observed instance. Recovery after a lost
receipt may pin only the exact current UUID proven ended by Zoom's past-meeting
endpoint. Another live UUID, a waiting state, a 404 or an empty recording list is
not proof of completion and cannot replace an existing pin.

Native End/Leave triggers bounded host-only read reconciliation through
`/api/hr/recruiter/{interview,meeting}/reconcile` or
`/api/hr/employer/call-reconcile`. These routes do not issue an end command. An
ordinary participant departure leaves the meeting live. Only exact provider
evidence of the ended session completes the product meeting and registers its
materials. The call-cleanup worker also checks a bounded set of due Zoom calls
when tabs have closed. Completed meetings without a material source are retried
idempotently, including a crash after business completion but before registration.

## Materials and 30-day cloud buffer

Zoom material sources always use `owned` storage even when the global Daily
policy remains `remote`. Existing Zoom remote sources are upgraded with a new
generation; the Daily default and sources are not changed. The shared worker
copies MP4 files into the configured private store and verifies size/checksum.
Only authenticated catalog access yields playback URLs. Zoom Bearer download
headers never reach the browser. Native Zoom transcripts may be imported when
available, but video readiness is independent; no separate ASR service is enabled
by this integration. See [MATERIALS.md](MATERIALS.md).

The optional `HR_ZOOM_RECORDING_RETENTION_ENABLED=1` worker keeps the cloud copy
for at least 30 days after both recording end and verification of our owned copy.
It moves eligible individual files to Zoom's trash; it never permanently deletes
files, deletes whole meetings or erases our objects. Before each operation it
checks the completed owner, exact pinned session/account/connection, unchanged
source generation and video inventory, current private-storage namespace,
verified file size and checksum. Uncopied M4A/chat/unknown files remain in Zoom.

Cleanup additionally requires account settings
`recording.allow_recovery_deleted_cloud_recordings=true` and
`recording.auto_delete_cmr=false`. Missing settings, missing scopes, provider
errors or absent verified copies block cleanup without blocking calls. An
uncertain trash response is recorded for review and is not automatically replayed.
The dedicated task is scheduled hourly and checks at most five due sources per
connection per tick. Successful/blocked checks have durable next-check state.

## Required granular scopes

| Operation | Scope |
| --- | --- |
| Create meeting | `meeting:write:meeting:admin` |
| Host ZAK | `user:read:token:admin` |
| Current meeting instance | `meeting:read:meeting:admin` |
| Exact ended instance | `meeting:read:past_meeting:admin` |
| Explicit host end | `meeting:update:status:admin` |
| Recording discovery/download | `cloud_recording:read:list_recording_files:admin` |
| Optional retention account guard | `account:read:settings:admin` |
| Optional individual file trash | `cloud_recording:delete:recording_file:admin` |

The final two scopes are only for retention. Fresh scope verification during
2026-10-25 deployment preparation found both absent. Retention is therefore not
reported active. At that preparation checkpoint production Zoom credentials had
not been installed. Code and offline tests are deployment preparation, not proof
of production activation. Adding scopes also requires verifying a newly issued
OAuth token; editing an app screen alone is insufficient.

## Validation and deployment acceptance

Use the project's Python 3.12 environment, with its locked materials dependencies:

```sh
cd services/recruiting-api
python3.12 -m unittest test_zoom_media test_call_providers \
  test_zoom_recording_consent test_native_zoom_end test_employer_native_zoom_end \
  test_materials_zoom test_zoom_recording_retention -v
```

The seven suites above passed 142 offline tests in the clean deployment worktree
on 2026-10-25 using the existing isolated Python 3.12 environment. They cover
explicit default-host selection, persistence, creation policy, participant consent,
role isolation, native completion, exact session identity, owned materials,
retention safeguards and unchanged Daily behavior. Rerun relevant suites after
further runtime changes; UI fixtures and mocked providers do not prove real browser
audio, permissions or cloud recording.

Before activation, confirm private configuration and actual host entitlement,
complete runtime/SDK assets, authenticated switch behavior for all meeting types,
and the shared private materials store/worker. Preserve active calls and take the
existing consistent backup through the deployment procedure. A real accepted
recording must be discovered under the correct session, copied and playable from
our catalog. Desktop Chrome/Safari and a real phone are acceptance targets;
responsive emulation alone does not prove phone media. Telegram may open the
system browser. Disabling `HR_ZOOM_ENABLED` stops new choices while preserving
existing Zoom joins/materials and the Daily path; it is not a database rollback.

Official contracts: [Meeting API](https://developers.zoom.us/docs/api/meetings/),
[account settings](https://developers.zoom.us/docs/api/accounts/),
[SDK authorization](https://developers.zoom.us/docs/meeting-sdk/auth/),
[Client View](https://developers.zoom.us/docs/meeting-sdk/web/client-view/meetings-webinars/),
[S2S OAuth](https://developers.zoom.us/docs/internal-apps/s2s-oauth/).
