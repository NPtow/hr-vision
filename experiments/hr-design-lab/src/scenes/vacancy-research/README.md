# Vacancy research lab — 26 October 2026

Research artifact, not a production vacancy-creation release.

## Iteration 02 — profile forms, after user feedback

The current `--prototype` link now opens three hiring-target profile forms. The original experiment remains at `--early-prototype`; the original reference list and its favorites key are untouched.

- New visual references: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--profiles&viewMode=story
- Three current forms: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--prototype&viewMode=story

Accepted direction: chat, one question at a time, a progress bar, and Ashby-inspired suggestions. A proposed skill becomes part of the profile only after Add. Matrix, Canvas, Artifacts and notetakers are excluded from the new direction. This is a desired future employee profile, not an assessed existing candidate.

`ProfileResearchLab.tsx`, `profile-research.css`, `portrait-references.ts` implement an isolated follow-up:

- Eight primary-source visual references: HubSpot, UXPressia, Attio, Xtensio, Smaply, MuchSkills, Ashby profile, and the previously selected Ashby suggestions mechanic. Actual screenshots, author illustrations and small previews are labeled. Attio's published screen is a company record, explicitly treated as a composition analogy. Two author videos are available on demand.
- Three substantially different forms: compact role dossier; a portrait made from cards; a narrative of behavior in work situations. Accepted data persists when switching forms.
- Pending and accepted skills are distinct states. The three-suggestion progress bar only counts accepted items. Change level and importance, skip, reconsider, remove, show a filled example, expand a skill, switch question/chat view, autoplay, pause and reset all work locally.
- Employer quote remains unchanged during edits. Example candidate speech follows the chosen level and is labeled invented. Mandatory conditions remain separate from skill importance.
- `runId` remounts the scene, `playing` controls timed actions, manual input stops autoplay, reset clears state and scroll. Motion honors reduced motion. No external model or production API is called.

Research receipts: `output/portrait-lab-20261026/`; image provenance: `public/media/vacancy-portraits/attribution.json`. Context7 supplied current shadcn controlled-collapsible/card composition docs; Motion was already available. No new production dependency was added.

Verification: typecheck and static Storybook build pass; 18 jsdom state checks cover provenance, proposal acceptance, editing, layout switching, skip/reconsider, removal, source preservation, question/chat, reset and autoplay. New Storybook routes and all 12 downloaded source images respond over local HTTP. Browser control remains disabled: responsive geometry, visual polish, animation feel, focus, and external video playback are not visually accepted. Source images were inspected directly as files; the MuchSkills AVIF was decoded solely for inspection.

The sections below document iteration 01 and remain for reference.

## Open

- References: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--references&viewMode=story
- Interactive directions: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--prototype&viewMode=story
- Motion studies: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--motion&viewMode=story
- Agreed task and path: http://127.0.0.1:6006/iframe.html?id=hr-vision-vacancy-research--route&viewMode=story

Story IDs verified against the running Storybook index. Start from the project root with `npm run storybook`.

## User intent

Sales roles first. Hiring manager and HR share a simple path. The employer describes work, outcomes, situations and conditions; the system proposes a structured profile, which visibly grows after each answer. Clicking a requirement expands it and reduces the chat. Required level has a concrete behavioral explanation and an illustrative candidate utterance. Changing the level changes that utterance. Source employer quotes are distinct from illustrative candidate speech. End state: employer-confirmed profile for HR to use in the Workbench.

## Included

22 references classified by task; 3 official YouTube videos; 7 displayed local source images (plus one downloaded alternative); source links, observations, applications and limitations. Favorites persist only in this browser. Schematics are explicitly labeled as our interpretations, not source screenshots.

Three interactive layouts: split conversation/profile, one question in focus, matrix/radar comparison. Six independent motion studies. Five prepared conversation topics produce three skill requirements and separate formalities. A complete local snapshot of all 16 core parameters and five levels per parameter is available in the catalog dialog.

## Data and borrowing

`sales-rubric.json` is semantically identical to `/Users/NIKITA/.codex/worktrees/assessment-services/HR Vision/packages/assessment-contracts/rubrics/sales-v1.json`, version `2026-10-26.behavioral-anchors.1`. It is a snapshot, not a live API integration.

Motion 13.4.6, already installed in the lab, implements layout/opacity transitions. Context7 searches and documentation informed `LayoutGroup`, `layout`, `AnimatePresence`, reduced motion, CopilotKit state sharing, assistant-ui tool rendering and shadcn Resizable examples. Documentation receipts are in `output/vacancy-lab-20261026/context7-*.md`. MIT licenses were checked at the upstream repositories for Motion, assistant-ui, CopilotKit, Radix and shadcn; the latter four are references only, not newly installed runtime dependencies.

Source image provenance: `public/media/vacancy-research/attribution.json`. Images remain owned by their source publishers; their display here is for the user's reference comparison, not an assertion that product assets are open source. AI Elements' current documentation announces migration to shadcn/ui; this is flagged in its reference cards.

## Boundaries

No model calls, candidate evaluation, live database writes, real vacancy edits, HR handoff or messages. Prepared answers, selected levels, importance and candidate utterances are simulation data. The final confirmation is explicitly local to the demonstration. Uncovered parameters are unknown, not zero. The radar plots required level only; missing levels leave a gap instead of a zero-valued point. Importance and mandatory conditions remain separate.

## Verification

- TypeScript typecheck and full Storybook production build pass.
- 11 DOM/state checks pass using a local jsdom harness: catalog integrity, preserved evidence, reference filter and source modal, answer-to-parameter flow, expanded editor, changed example, undo, complete profile, reset, source recovery in focus mode, unknown radar values and six motion studies.
- Live Storybook index and all eight local image assets respond correctly.
- The DOM harness stubs layout/browser facilities and is NOT visual acceptance. Real-browser responsive geometry, animation quality, native focus behavior and YouTube playback remain unverified because browser control was disabled in this session.

Receipts and test runner: `output/vacancy-lab-20261026/`. The test-only jsdom dependency is isolated there; main package and lockfile are unchanged. No production deployment performed.

## Next decision

Select useful patterns in the lab, combine them into one flow on a real sales brief, then test comprehension with a person. Only after that connect the chosen interaction to source quotes, profile versions, manual-edit preservation and the core catalog API, and verify handoff into the Workbench.
