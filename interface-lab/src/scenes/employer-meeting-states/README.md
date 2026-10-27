# Meeting states in the employer task card

Local Interface Lab proposal. Reuses the existing `CandidateStageCard` and `CandidateInterviewPlayer`; no production workflow, auth or persistence changes.

- Same timeline, distinct meeting instances. Selecting a historical stage does not change its state.
- States: candidate confirmation pending, confirmed, live, completed, cancelled. Repeat meeting appends an instance and preserves the previous meeting's result and comments.
- Everyone in the company has the same actions and sees the same shared feedback. Account selection lives in lab controls, not a permission model.
- Date, participants, rescheduling and cancellation are under one menu; participant details and agenda expand on demand.
- Leaving a call is distinct from ending it for everyone. Recording readiness is independent from meeting completion.
- No test assignments, offer redesign, messages, database changes or actual camera/microphone calls.

The local fixture `/public/__local_prototypes__/meeting-interview.json` is excluded via Git's local exclude file. It contains an existing private DSA reference and must not be published. The scene fetches it only on loopback hosts. The original screening video is explicitly labelled in the lab chrome as a visual sample for the fictional meeting; it is not asserted to be a recording of that meeting. Without the file the scene still builds, with an unavailable player state.

Stories are discovered by the existing glob under `HR_BUILD_TARGET=all` or `mockups`. The service build excludes this scene and the local public directory.

Manual path: pending → confirm in lab controls → join → leave/rejoin → end for everyone → shared feedback → simulate materials ready in lab controls → next meeting → revisit prior stage → reschedule → cancel. Reset clears every local scenario and its comments.
