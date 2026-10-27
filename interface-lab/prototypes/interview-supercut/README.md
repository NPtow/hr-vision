# Interview viewer · Supercut reference

Local, isolated Storybook prototype for HR Vision. It is outside the production `src/` story glob and is not wired to the employer backend.

Visual thesis: the recording is the main surface; a frameless transcript and quiet pill tabs sit alongside it. Black player controls use the reference's pink progress accent. Fullscreen keeps the transcript on a white side panel and moves reactions into the control bar.

States: ordinary review, expanded workspace, timestamped comment. The same media element persists across layout changes. Local interactions: playback, ±5 seconds, seek, speed, mute/volume, captions, theatre, fullscreen, chapter and transcript navigation, transcript search, local comments/replies/deletion, reactions and moment links. Comments and reactions are only localStorage records with a prototype-specific key.

Reference: https://supercut.ai/share/fluently/rtpYwcxVQYLaPIMPdSaCXI (inspected 19 October 2026). Confirmed normal and expanded layouts, Lucide micro-icons, transcript seeking/following, chapter segments, reactions and the login gate for writing comments. Authenticated comment editor was not visible; that editor is a local adaptation, not a claimed exact copy of the private screen.

Real example: existing DSA interview 378713, Dina Chaplygina. The source transcript is grouped into adjacent same-speaker paragraphs without rewriting. No source decisions, interviews, invitations or comments are changed. The fixture and poster are stored in `output/prototypes/interview-supercut-20261019/private/` and served only by this dedicated local preview. Do not deploy this build as a public gallery.

Limits: there is one original 1920×1140 recording, not an adaptive encoding ladder. Auto/original in the quality menu use that same original source. The input below the video is local exact-phrase search, not an LLM assistant. Microphone affordance explains that text search is available; audio is not captured. The remote source video needs network access.

Run from `interface-lab/`:

```sh
npx storybook dev -c prototypes/interview-supercut/.storybook -p 6018 --host 127.0.0.1 --no-open --disable-telemetry
npx tsc --noEmit -p prototypes/interview-supercut/tsconfig.json
npx storybook build -c prototypes/interview-supercut/.storybook -o ../output/prototypes/interview-supercut-20261019/site --disable-telemetry
```

Get story IDs from the running or built `index.json` before sharing links.
