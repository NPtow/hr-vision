# Candidate call: custom UI and one device permission origin

## Change

The candidate meeting uses a Daily call object and our recruiter call surfaces.
There is no embedded Prebuilt UI and no notes, transcript, CV or interview-script
panel. Candidate credentials, consent, materials and room ownership remain
server-controlled. Candidate exit only leaves their connection.

The explicit prejoin button requests devices on our page. Live preview tracks
are cloned and passed into the call object before retiring the preview; joining
does not acquire devices again. All owned tracks are stopped on cancellation,
failed join, exit and unmount. Device-free entry can enable devices later.
Enabling a device exits browser fullscreen so browser permission UI is accessible.

This addresses the separate-origin permission problem discussed in the
“Скрипт для HRa” thread: granting our preview access did not grant the embedded
Prebuilt origin access. The custom call has a single capture origin.

## Review

- Authorization: the existing guest/interview/meeting join endpoints and consent
  checks remain; controls respect the participant token's publication permissions.
  No candidate action starts/stops recording or finishes a meeting.
- Resource handling: provider-owned remote tracks are never stopped by rendering.
  Separate remote audio avoids local feedback. Blocked autoplay exposes an
  explicit “Включить звук” action.
- Recovery: retain bounded credential/SDK/connection waits, cancel stale attempts,
  preserve unexpected-disconnect errors and require reload after failed disposal.
- Accessibility: labeled controls, keyboard focus containment/return, inert
  background, reduced motion and mobile controls at least 44 px.

## Verification

Local component in Chromium connected two test participants to a disposable,
private real Daily room with recording disabled. Only synthetic camera/audio
and a generated canvas screen were used for the controlled media tests.
Credential delivery in the local harness was stubbed; Daily signaling/media
were real. No real candidate meeting was changed.

- Both remote video and audio played; self video was muted.
- One `getUserMedia` acquisition across preview, join and mute/unmute.
- Mic/camera off reached the second participant and restored successfully.
- Shared screen reached the second participant at 1280 × 720 and stopped.
- Leaving one participant kept the other connected; rejoin succeeded.
- Device-free join succeeded. Denied devices displayed recovery actions;
  granting microphone only produced a live audio track with no video track.
- At 390 × 844 no horizontal overflow; device controls 44 × 44 px.
- Blocking `c.dailywebrtc.net/call-ui/**` did not prevent custom call entry.
- Blocking the primary call-machine CDN activated the SDK's secondary CDN
  (`c.dailywebrtc.com`) and still connected.
- Blocking all `**/call-machine/**` routes produced a visible connection error
  after 6.35 seconds and removed the call overlay/spinner.
- Typecheck, candidate call/access tests and production Storybook build passed.

The reported Prebuilt framework URL returned HTTP 200 from this test network.
This does not establish its availability on the candidate's earlier network.
The SDK still requires Daily signaling, media and call-machine endpoints; it
cannot compensate for every network/provider outage.

Real Android/Telegram and Safari permission-prompt behavior, camera hardware,
and recovery on the affected person's network were not reproduced in these tests.
Do not present these controlled checks as proof of that user's exact incident.

## Concurrent changes

The preceding main-branch update retains native Prebuilt preparation for other
roles and the direct-room fallback. Its provider token settings and forced
iframe disposal remain. Candidate UI intentionally uses the custom call object
instead; no Prebuilt preparation is mounted in the candidate's main call path.
