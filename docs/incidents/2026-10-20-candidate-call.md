# Candidate could not enter the 20 October interview

All times below are Moscow time (UTC+3). This report contains no invitation
secrets, participant tokens, CV data, or contact details.

## Finding

The candidate did not establish a Daily connection. Room provisioning and
access issuance succeeded. Her later screenshot shows our “Загружаем
комнату…” state: the iframe had been mounted but Daily's `loaded` event had
not arrived.

The original invitation was checked against its stored hash and opened in
a browser at 16:42. It successfully joined the actual private room with
camera and microphone off, then left. This proves that the invitation and
room were usable from the test environment at that time. It does not prove
reachability from the candidate's mobile carrier, VPN, or embedded browser.

The exact cause on her device remains unconfirmed. The application did not
collect client failure events before joining, and Daily had no candidate
session from the failed attempts. A VPN or carrier block is a hypothesis,
not an established diagnosis.

## Evidence

Production at investigation start:

- Web: `91c39864a4ea6a8233c9c8002f1bd86fa57033d2`.
- API: `bacaeee7752974614749b106af2e692887cd495d`.
- Interview: `interview_74aa93a0c862451fbd164f6f`.
- Invitation: `invitation_48c5c46de10e299fb0973b0f`.
- Original Daily session: `85f4e830-711d-4cd1-b4a0-67c9e1f112b8`.

| Time | Observation |
| --- | --- |
| 16:00:57–16:16:43 | Daily session with only the recruiter; maximum one participant. |
| 16:04:18–16:04:20 | Room lookup/update and access token issuance: HTTP 200. |
| 16:05:02–16:05:04 | Second successful sequence. |
| 16:06:30–16:06:32 | Third successful sequence. |
| 16:08:08 | Guest invitation created; valid until 18:00, not revoked. |
| 16:12–16:16 | Recruiter client reported late socket health check and signalling reconnects. These occurred after the candidate's first failed attempts and cannot establish their cause. |
| 16:33 | Candidate screenshot: our room-loading indicator, no loaded Daily interface. |
| 16:42:13–16:42:23 | Authorized technical test of the original link. Session `2405b01d-2f47-4eb2-a1e9-6d50e0c26b2e`, one participant, devices off. This session is an agent test, **not** a recovered candidate interview. |
| After test | Actual room presence confirmed empty. |

The allowed interview-entry window was 15:45–18:00. Passing 16:00 or 16:30
did not invalidate this invitation.

API service logs did not show an application error in the original failure
window. The web service did not retain detailed access logs. One aborted
asset response in the edge proxy could not be attributed to this candidate.
Private provider logs are retained under the server's restricted operations
directory, not in Git.

## Review findings and changes

1. **Unbounded device preparation.** An unanswered browser permission
   request could keep the join button disabled indefinitely. Preview now
   has a 10-second deadline and offers entry with both devices off.
   Late permission grants release their tracks after timeout or unmount.
   Video playback no longer blocks preview completion.
2. **No recovery when embedded video fails.** Credentials are retained for
   a direct link to the same private room, with the same participant scope.
   The link appears after failure. It works even when the SDK fails before
   the access response arrives. It does not remove room authentication or
   enable recording.
3. **Ambiguous loading failures.** Access issuance, SDK loading, iframe
   loading, and network connection now have separate bounded stages and
   corresponding messages. A terminal provider error triggers cleanup.
   Frame cleanup remains bounded; an unresponsive frame cannot trap the UI.
4. **An unrelated demo eagerly imported the video SDK.** Because it shared
   the product entry bundle, failure to load that chunk could prevent even
   the invitation from rendering. The demo now imports its SDK on join.

These changes harden entry and recovery. They cannot make an unreachable
Daily domain reachable; direct entry still needs connectivity to Daily.
No interview schedule, consent, candidate profile, or completion status was
manually changed. No recording or transcription was started by these tests.

## Verification

- TypeScript check and production Storybook build.
- 12 focused connection/cleanup/device/token tests.
- Isolated real Daily room: successful join, leave, and direct fallback
  with camera and microphone off.
- Deliberately unanswered device permission: UI unlocks with no-device
  entry.
- Deliberately unavailable SDK, with a slower credentials response:
  invitation still renders; readable failure and direct-room recovery
  appear.
- Direct fallback opens and joins the same isolated Daily room.
- Deliberately blocked iframe: loading and cleanup time out, the overlay
  closes, and the direct-room link remains available alongside reload.
- Original production invitation independently joined and left, as recorded
  in the timeline.

The fault-injection harness and screenshots are local incident evidence.
Deployment verification is recorded separately in Veritas task
`task_20261020_HzYwPz`.

## Remaining diagnostic gap

Before-join failures currently lack server-side client diagnostics. A future
diagnostic event should record only attempt ID, connection phase, elapsed
time, and an allowlisted error code. Invitation/token URLs, raw provider
errors, CV data, and media must not be logged. Until that is available,
successful server responses must not be treated as proof that a candidate
entered the call.
