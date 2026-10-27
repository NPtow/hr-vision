# Candidate website — local design review, 14 October 2026

## Intent

Use the employer website's typography, blue accent, calm continuous backgrounds and scroll-driven product scenes to explain the candidate journey. This is a separate marketing entry at `candidates.html`, not a change to the working candidate portal.

Journey: repeated applications → one interview → a profile assembled from answers → relevant employers see the interview → interested teams send invitations → a direct conversation with a manager.

## Apple-design review and fixes

| Finding in first draft | Implemented correction |
| --- | --- |
| Decorative cartoon faces felt unrelated to the employer product. | Shared visual language: initials, quiet video silhouettes, recognizable HR Vision window. |
| An entire profile field was replaced after every answer. The mechanism was unclear. | Facts accumulate. A highlighted phrase maps to the active fact; earlier facts remain visible. |
| Automatic fades briefly left an empty interface; manual changes waited on outgoing content. | Removed exit-before-enter transitions. Selection responds immediately; motion emphasizes the selected area. |
| Tiny text inside a compressed illustration made the useful information hard to read. | Larger answer, role, invitation and matching-reason typography; intrinsic layouts on mobile instead of scaling a desktop picture. |
| The employer graph was a decorative three-way fan. | Persistent candidate profile, each company's concrete need, outward interview delivery, returned interest and a final invitation count. |
| Scroll was the only reliable way to inspect the process. | Steps can be selected manually, and ordinary scrolling resumes the scene. Mobile controls stay available above the product window. |
| A final network packet was still in transit at the last manual stop. | Last stop resolves to 100%, with all returns finished. |
| Incoming result overlapped the network controls in a short desktop viewport. | Reserved vertical room for the result and controls. |
| Hidden completion text was exposed to assistive technology. | Completion and returned-interest labels are only exposed in their actual completed state. |
| Motion-only presentation left reduced-motion users without a coherent overview. | Static, readable reduced-motion layout plus manual scene choices. Reduced transparency and higher contrast are supported. |
| Portaled booking dialog exceeded a 320px viewport because its own padding was outside the declared width. | Applied border-box sizing to the dialog itself; the verified width is 288px with internal vertical scrolling. |
| Opening a chapter URL directly could land at the top before React mounted its anchor. | Resolve known chapter anchors after mount. |

These are web adaptations of the supplied Apple-design skill, not claims about Apple's private geometry or animation values. Brand styling remains HR Vision.

## Interaction boundaries

- Invitation examples are authored fictional vacancies; names, compensation and schedules are demo data.
- Interview CTA opens a two-question local demonstration. Entered text stays in component state and is rendered as text.
- Picking a time demonstrates the confirmation state. It sends no invitation and does not reserve a real calendar slot.
- No AI API, authentication, microphone, recording or candidate database is connected in this change.
- No unsupported “twice as fast” statistic is published. The page explains which repeated stages are removed.
- The initial review was local. The candidate demonstration was subsequently published at the user's explicit request on 14 October; see the release verification below.

## Review entries

- Main page: `http://127.0.0.1:4173/candidates.html`
- Development: `http://127.0.0.1:5173/candidates.html`
- Storybook: `http://127.0.0.1:4173/storybook/?path=/story/hr-vision-candidate-site--candidate-landing`
- Seven candidate stories cover the whole page, five individual scenes and reduced motion. Build verifies their generated IDs.
- Browser evidence and build output: `../../output/candidate-site/`.

## Verification completed locally

- TypeScript, Vite and Storybook build passed; all seven generated candidate story IDs are present.
- Reviewed desktop scenes, mobile 390px and 320px layouts. Document width matches the narrow viewport; the 320px booking dialog remains inside the frame.
- Interview answers and priorities survive Back; the result uses the entered answer. Closing returns focus to the trigger.
- Company selection changes the invitation, manager and available slots. A new company starts with no selected slot. Booking confirmation retains the chosen slot. Escape and Done close the dialog.
- Interview profile facts accumulate; manual stage buttons respond immediately.
- Built reduced-motion story has normal-flow sections and readable completed content; manual steps still work. No console warnings or errors in the checked page and reduced-motion story.
- Direct chapter navigation and the final CTA screen were checked after the anchor fix.
- The booking and interview interactions are still local demonstrations, not production integrations.

## Public release · 14 October 2026

- Candidate page: https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/candidates.html
- Release `20261014T125030Z-candidate` on outreach. `deploy-candidate.py` clones the current release, adds only the candidate entry and isolated assets, verifies the 62 pre-existing files and atomically switches the release link. Existing employer and laboratory files are preserved.
- All 21 existing project tests and the TypeScript/Vite/Storybook build passed. These are project regression tests; candidate interactions were checked in the browser.
- Sixteen public files match their expected SHA-256, including the candidate bundle/font/logo and the unchanged employer/lab entries.
- Hosted browser: accumulated profile facts, company switching, slot selection/confirmation, interview answer/priority/result and image loading passed. No browser console warnings or errors were observed in this flow.
- Deployment and verification receipts: `../../output/candidate-site/candidate-deployment.json` and `candidate-public-verification.json`. Public screenshot: `candidate-live.png` in the same directory.
- Last employer-only baseline retained at `releases/20261013T215607Z`; no service restart or Caddy change was needed.

## Screenshot corrections · 14 October 2026

- Removed the six requested secondary elements: the employer footer link, “Вы тоже выбираете”, the call footer, its introduction/status strip, the graph's central HR Vision label, and “Давайте по-другому”.
- The removed repeat HR screening stage is fully visible in red with a complete strike-through. The subsequent stages retain their scroll progression.
- Replaced the audience text links with a segmented “Ищу работу / Ищу сотрудника” navigation. It uses real links, an active-page state, visible keyboard focus and 44px targets; the narrow header has two rows.
- The candidate page still uses the existing HR Vision brand. The separate МашаДаша logo comparison preserves the original Figma portraits, palette and typefaces; only framing is being compared.
- Typecheck, production/Storybook build, and all 21 project tests passed. Browser checks covered the hosted desktop page, mobile widths 320/390px, the interview dialog, and the employer navigation link.
- Latest candidate release: `20261014T140817Z-candidate`. Twelve candidate files match their expected SHA-256; all 62 employer/lab files were preserved.
- Fixed archive timestamps in `deploy-candidate.py`: zero timestamps could produce the same Caddy HTML ETag after a same-length content change. The latest HTML has a new timestamp-derived ETag and normal reload retrieves the update.
- Current proof: `../../output/candidate-site/candidate-ui-cleanup-verification.json`, `candidate-cleanup-live-desktop.png`, and `candidate-cleanup-live-mobile.png`.


## Identity refresh · 14 October 2026

The approved МашаДаша identity supersedes the previous blue-only direction above. Candidate actions now use the dark coral accent; the employer side uses blue. The combined illustrated logo replaces the former logo and audience pills, with separate semantic links and a soft selected-state halo. Warm continuous backgrounds, ink typography, italic headline accents, quiet card contours and accessible button contrast are shared with the live brandbook. The deployment preserves the current behaviour bundles; receipts are in `../../output/brand-refresh/`.
