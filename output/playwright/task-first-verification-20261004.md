# Task-first manager UI verification · 2026-10-04

Local source, Storybook at `http://127.0.0.1:6007`.

- Updated existing `scripts/agency-product-check.js` for the approved task-first flow; observed run returned `passed: 55`, `errors: []`.
- Entry starts at hiring tasks and has one action inside the workspace. Permanent sidebar and manager coordinator-question controls are absent.
- Invitation alone does not reveal the meeting page; independent candidate confirmation makes its contextual entry available.
- Returning to candidates, reload persistence, decline/undo, evidence dialogs and consent boundaries passed.
- Existing candidate interface was not changed, its connected regression checks still passed.
- Layout checked at 390, 768, 1024 and 1440 px; no document horizontal overflow. Desktop task/shortlist and mobile task/detail screenshots inspected.
- Screenshots: `task-first-manager-1440.png`, `task-first-manager-390.png`, `task-first-shortlist-1440.png`, `task-first-shortlist-390.png`, `task-first-detail-390.png`.

These checks cover the existing local UI preview. They do not verify production meetings, storage, feedback assessment or offer delivery. Those decisions are awaiting review. No public deployment was attempted for this revision.
