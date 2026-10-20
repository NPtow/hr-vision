# Recruiter connection uses the candidate call implementation

## Incident and change

The recruiter still loaded Daily Prebuilt while the candidate used a custom call object. The reported screenshot was our load failure screen. Earlier reports identified unreachable Daily `call-ui` framework/main assets. The installed SDK also allows three Prebuilt host attempts (25 seconds each), while our previous wrapper cancelled at 30 seconds. These observations do not establish the exact network failure on Maria's device.

RecruiterCall now uses the candidate device-access hook and captured-track handoff, Daily `createCallObject`, and a shared `CallMediaStage`. The approved recruiter shell, questions, original resume, notes, consent, start and completion endpoints remain. Neither primary call surface loads Daily Prebuilt `call-ui` assets. Daily call-machine, signaling and media connectivity remain necessary.

The browser request runs from an explicit preparation click. Preview track clones survive stopping the preview. Preview mute/camera toggles change existing tracks instead of reacquiring devices. Abort, retry, close and completed calls release owned tracks; a failed SDK cleanup requires reload. Loading allows the SDK fallback attempts; preparation itself has no join timeout. Screen sharing respects both participant permission and room configuration.

## Validation

- TypeScript, 20 connection/media helper tests and production Storybook build passed. Final CI validates the release again.
- Independent code review found preview toggles reacquiring devices; corrected and browser-verified.
- Real isolated Daily room, recording disabled, synthetic camera and microphone. Credential/coach API responses were fixtures; provider join, transport, tracks, controls, rendering and cleanup were real. No real interview was joined or modified.
- Both actual RecruiterCall and InterviewRoom joined as distinct participants with all `/call-ui/` assets blocked. No call-ui request was made. Each made exactly one getUserMedia request from preview through join.
- Remote video decoded at 320 x 240 on both sides, readyState 4; remote audio elements played unmuted, readyState 4. Recruiter mute/unmute and camera changes propagated to the candidate without another capture request.
- Folding and opening the coach panel preserved the local video track id.
- A late permission grant after choosing device-free entry was released; the call stayed audio/video off.
- Candidate leaving did not end the recruiter's call. Recruiter exit released capture tracks and destroyed the call object.
- Non-coach meeting entry works and hides screen sharing when disabled by the room.
- Blocking every call-machine load produced a visible recoverable error and destroyed the failed call object. Removing the block and retrying joined successfully.

Browser evidence: `output/playwright/recruiter-custom/` (local only; private room credentials are not tracked). Screenshots: two-party-recruiter.png and two-party-candidate.png. This is not proof of Safari behavior or availability on Maria's network. Live recording/transcription was not restarted in this connection-only QA; existing backend lifecycle ownership is unchanged.
