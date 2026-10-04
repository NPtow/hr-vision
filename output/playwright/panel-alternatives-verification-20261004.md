# Panel alternatives verification · 2026-10-04

Local Storybook: `http://127.0.0.1:6007/iframe.html?id=hr-vision-panel-alternatives--gallery&viewMode=story&variant=1`.

- `npm --prefix interface-lab run typecheck`: passed.
- `npm --prefix interface-lab run build`: passed. Existing dependency module-directive warnings remain nonfatal.
- `scripts/panel-alternatives-check.js` via Playwright CLI: **91 checks passed**, `errors: []`.
- All ten layouts selected and checked at 1440 and 390 px; no document horizontal overflow.
- Profile selection, evidence and sheet dialogs, nested Escape/focus restoration (three repetitions), accordion close/reopen, next candidate, dossier selection, task lenses, mobile evidence reading/back, reduced-motion strip navigation, card page/back, task context and invalid URL recovery passed.
- Fixed the mobile table caption overflowing its containing block and the fast nested Escape closing the underlying profile.
- Saved all ten full-page views at both widths: `panel-alternative-01-1440.png` through `panel-alternative-10-390.png`.
- Visually inspected all ten desktop screenshots and mobile 01, 02, 10. Browser verification used Chromium, not a cross-browser matrix.
- Existing manager task-first flow independently passed 55 checks; see `task-first-verification-20261004.md`.

No meetings, recording, feedback generation, offer sending, database or authentication integration is asserted by these checks. The gallery is an explicitly requested interface comparison on fictional data. It was not deployed to the shared YC server.
