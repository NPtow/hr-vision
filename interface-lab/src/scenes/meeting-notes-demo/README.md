# Meeting notes demo

An isolated, real Daily meeting surface. It never reads or mutates candidate or vacancy stages.

## Entry

- Export: `MeetingNotesDemo({ token?, onExit?, displayName?, initialSessionId?, apiBase? })`.
- API default: same-origin `/api/hr/meeting-lab`, credentials included. Optional Bearer token is kept in component memory; Daily room tokens are never stored.
- Employer auth wrapper: `MeetingNotesDemoEntry`, owned by the parent integration task.
- Story ID confirmed from local Storybook `index.json`: `hr-vision-meeting-notes-demo--workspace`.
- Service entry: existing manager story with `?demo=meeting-notes`, configured by the parent integration task.
- Session permalink: `meetingSession` URL parameter. The history menu lists actual sessions from the API.

## Flow

1. Agree to recording/transcription, then join. Session creation uses a stable request ID for retries. One participant is enough.
2. Daily call object renders real participant tracks. The toolbar controls microphone, camera, screen sharing, invitation link, ending, pane visibility and browser fullscreen.
3. Only final Daily captions are accepted. All speakers including local are persisted in batches; failed batches stay queued and retry. No fake speech or recorded media is provided by the scene.
4. Notes are shared server data with CAS revision checks. A conflict preserves the local draft and blocks navigation/finish until the user explicitly chooses the common or local version.
5. Chat submits questions to the real server. Evidence is displayed using the returned source mappings and quotes. The optional post-meeting summary action uses the same endpoint and never overwrites notes.
6. End waits for capture acknowledgements with a bounded fallback, flushes final speech, finalizes on the server, flushes late turns and leaves Daily. The same screen then polls for actual saved materials.
7. Recording URLs stay stable while playing. Partial provider results (`complete:false`) continue polling even with `status:ready`. Explicit refresh after a media error can renew the access URL.

The server currently does not confirm a shared timebase between Daily live captions, VTT cues and recording start. Transcript-to-recording seek is therefore disabled unless a recording includes an explicit `transcriptOffsetMs`. The normal video scrubber is functional. No zero offset is guessed.

## UI / SDK references

- Existing local `interview-review/CandidateInterviewPlayer.tsx` and CSS: full viewport layout, icon pill tabs, fading transcript edges, current-line following, pane handle, overlay title, lower toolbar.
- Context7 `/daily-co/daily-js`, checked against installed `@daily-co/daily-js` **0.93.0** declarations: custom `createCallObject`, participant events and `tracks.*.persistentTrack`, recording/transcription methods/events.
- Official SDK source: https://github.com/daily-co/daily-js/tree/daily-js-releases

## Verification boundary

- TypeScript check passes.
- Fresh local story ID verified.
- Local layout only: 1440×900 and 390×844, no horizontal overflow; icon tabs, pane hide/restore, disabled controls before a session and disabled service capabilities checked in browser.
- The layout check temporarily intercepted auth/list responses in the test browser to render an empty, explicitly unavailable backend. The scene contains no mock API or fake meeting fixtures. Those screenshots do **not** prove a real call, speech, recording, persistence or AI response.
- Real end-to-end Daily/storage/model verification is owned by the parent task after its API publication; record its actual evidence separately.
