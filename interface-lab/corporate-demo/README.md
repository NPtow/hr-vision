# HR Vision · company website demo

Visual thesis: the white, generous HRDS interface becomes a continuous, colorful story about connecting a business need to the right person. Blue is the action color; lavender, mint and warm yellow distinguish stages without recoloring the product UI.

Content: business problem → brief → 2,000 profiles → graph of needs and experience → mutual interest → recorded interview with reasons → calendar → meeting → feedback changes the next shortlist → offer → payment after hire → first-candidate CTA.

Interaction thesis: scroll transforms the same brief/profile across stages; fine graph connections carry feedback back into matching; real demo controls let visitors play a sample interview, select a meeting slot and refine the next candidate.

## Sources

- Cora, inspected live on 11 October 2026: https://cora.computer/. Sticky scenes, inbox rows separating, draft growing from a row, messages converging into an envelope, the envelope closing and unfolding into categorized information, continuous landscape/parallax, floating navigation, repeat CTAs, plan switch and FAQ. We borrow the continuity and transformation, not its artwork or copy.
- Figma HR UI: https://www.figma.com/design/YrF3nopj0JRGxNjVfeAyH4/HR-UI?node-id=75-3. Read high-fidelity design context and screenshot for candidate profile `75:3` and meeting dialog `78:8375`. Local logo/play assets exported from these nodes. White surfaces, #121212 type, #F8F8F8 cards, 28px radii, pill controls, Roboto Flex and the radial blue primary button come from HRDS.
- GTM deck 2.9, slides 3–4 (mutual choice and process), 15 (brief → batch → feedback → new batch), 17–19 (feedback accumulates), 58 (shared candidate network). The Bitrix24 example and 2,000-candidate headline are supplied by Nikita, not independently verified metrics.

## Scope

The animated story is a marketing demonstration. All profile names, answers, candidate interest, meeting confirmations and offer states shown in the story are fictional scenario data. They are never copied into an employer's real workspace. Internal financial, GTM and market-research slides are not published.

The main site's header, brief action and final CTA now open the real employer product. “Войти” opens login; “Начать подбор” and “Найти кандидата” continue through registration (or the existing session) to vacancy creation. Only a visitor-edited brief is carried into the description. The default illustrative brief is not imported. The draft travels in a URL fragment, is removed from the address on arrival, and stays in the product tab's session storage during authentication. Cancel, successful creation and logout clear the pending entry. The auth logo and cabinet account menu link back to this site. The animation lab retains its isolated demo funnel.

## Build

From the repository root, using the existing interface-lab dependencies:

```sh
npm ci --prefix interface-lab
npm ci --ignore-scripts --prefix interface-lab/corporate-demo
node interface-lab/corporate-demo/build.mjs
```

This builds the website, animation lab, and all registered Storybook scenes and variants, then reads IDs from the generated index into `lab-manifest.json`. Source is separate from the employer product and existing Storybook galleries. Build output is `corporate-demo/dist/`; only this static output is eligible for deployment. Reduced-motion preferences remove scrub/panning and preserve all content and controls.

## Published demo

https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/

`python3 interface-lab/corporate-demo/deploy.py` publishes a separate immutable static release under `/srv/hr-vision-company-demo/releases` and switches its own `current` link. The Caddy route is scoped to `/demos/company-v1/` on the lab host. It does not replace either product or Storybook release. `deployment.json` records the current publication receipt. This folder is not wired into the existing product auto-deploy workflow.

## Verification · 11 October 2026

- TypeScript check and production build passed.
- Public index, JS, CSS and font bytes match the local build; HTTPS returns 200. Existing product and gallery routes returned 200 after the demo route was added.
- Checked live in WebKit at 1280×720 and 390×844. No horizontal overflow or browser console errors in the checked flow.
- Verified the headline transition, editable brief, graph, candidate rows, interview chapters and sample playback, meeting slot selection and confirmation, Bitrix24 feedback, alternative candidate propagation into the offer, four offer states and the final dialog.
- Fixed native scroll-timeline opacity mismatch, oversized nested SVG icons, overlap of graph labels and a missing mobile text space.
- Interview playback, confirmations, candidate selection and offer progression are local demonstrations. No live recording, real matching or external delivery is connected.
- Reduced motion is implemented through Motion and CSS; the operating-system preference was not changed for testing.

The Russian reference breakdown is in `RESEARCH.md`. Browser proof: `../../output/corporate-demo/feedback-desktop.jpg`.


## Animation lab

https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/lab.html#intro

The Proteus Motion reference supplies the layout: horizontal scene tabs, one canvas, and playback controls beneath it. This is HR Vision branding with shared company-site components, not another copy of the product.

- `src/site.tsx` is shared between the scroll website and isolated scenes.
- `src/lab/CompanyMotion.stories.tsx` declares the scenes and variants in a dedicated Storybook build. The generated manifest is the sole source of story IDs.
- `src/lab/Lab.tsx` supplies the navigation, timeline, speed, steps, device viewport and expanded view.
- `SceneHost` owns progress, `playing` and `runId`. Same-origin, source-checked messages connect the shell and the selected story. Pausing preserves progress; replay remounts local scene state. Only one story iframe is active.
- Each scene has a direct hash link. The full website remains at the original URL and links to the lab from its header.
- The lab runs locally within the page; it never schedules an actual meeting or sends an offer. Scene names, profiles, meetings and offers remain fictional demonstration data.

Copy pass: removed explanatory paragraphs beneath all major headings, decorative labels and signatures, repeated footer notes, and version/demo microcopy. Main headings now carry the business meaning. Labels and states that identify a field, person, time or action remain in the interactive examples.


### Lab verification · 11 October 2026

- TypeScript, Vite and the dedicated Storybook build passed. The generated manifest resolves all 11 real stories.
- Public HTML, lab manifest and Storybook index match the final build bytes. Release: `20261011T020006Z`.
- Opened all 11 scenes in the hosted browser. Verified pause holds progress, resume/steps, speed selection, replay clears edited brief and chosen time, chapter selection, invitation flight/confirmation, feedback changing the next person, offer progression and CTA → demo shortlist → interview.
- Verified full-screen open/close and both embedded viewport sizes. The full website and lab were checked at 390px and 1280px widths without horizontal overflow. No console errors were observed in the checked flow.
- Corrected the isolated interview reset so it starts at the first chapter. A transient iframe network error recovered through the explicit retry control.
- No operating-system accessibility setting was changed; reduced-motion handling is implemented but not separately emulated.
- Proof: `../../output/corporate-demo/animation-lab.jpg`.

Scene order updated: brief → candidate base → matching graph. The brief action and lab navigation follow the same sequence. Verified public release `20261011T021003Z`.

### Invitation animation comparisons

The lab's **Интерес** tab now compares **Письма**, **Импульс**, **Карточки** and the original scene. Direct links use `lab.html#interest/letters`, `#interest/signal`, `#interest/cards`, `#interest/original`. The graph tab links to these comparisons. Each experiment has its own Storybook story and shares the existing playback controls. Research and validation: [INTEREST-RESEARCH.md](./INTEREST-RESEARCH.md).


### Feedback cycles · review reshuffle and meeting graph

The lab now uses two distinct scenes. `lab.html#interview` (Focus and Closeup) ends with feedback and a card reshuffle inside the interface tour. The separate `lab.html#review-feedback` shows the same final segment directly. `lab.html#feedback/graph` retains the detailed graph after a meeting.

In the interview flow the employer reviews the recording, answers, assessment and recommendation, then chooses “Не хватает опыта в Битрикс24” or “Хочется больше инициативы”. Applying the answer turns it into a criterion, reshuffles the existing cards and brings forward Mikhail or Ilya respectively. “Смотреть интервью” opens the selected candidate's recording/answer/assessment presentation; “К подборке” returns to the settled selection. The demo data and quotes are authored examples, not a live matching engine or actual candidate evaluations.

The review segment shares the tour's progress clock, including seeking, pause and replay. Manual choices pause autoplay; Apply resumes from the response. It is 54 seconds for the complete lab tour and 20 seconds for the standalone review segment. The approved cycle is also integrated into the main website's `InterviewChapter`.

After a meeting, the new graph keeps previous task criteria visible, adds the response as a concrete requirement and only then draws the stronger candidate connection. Result proof comes from the authored interview excerpt. Prior experiments remain available at their existing URLs. Source for the shared card reshuffle is documented in `FEEDBACK-RESEARCH.md`; SVG implementation follows Motion's documentation at https://motion.dev/docs/svg-animation (installed Motion 13.4.6).

Verified release `20261011T141823Z`: TypeScript, Vite and Storybook build passed (20 story IDs); 11 relevant public files match local bytes. Hosted browser checks covered both feedback choices, reshuffle, opening the matching interview and returning, replay reset, and the embedded 390×844 layout. Checked the distinct after-meeting graph and its return to the tour; no console errors observed. Reduced-motion preference was not separately emulated. Proof and receipts: `../../output/corporate-demo/review-reshuffle-result.jpg`, `review-reshuffle-mobile.jpg`, `review-reshuffle-qa.json` and `review-reshuffle-deploy.json` in that directory.

### Main-site feedback integration

`#interview` now includes the approved card reshuffle as the last step of the same tour. `#feedback` uses `FeedbackGraph` after the meeting; its three answers update the candidate shown in the following offer. Both sections reuse the lab components and preserve the existing public links.

`useChapterControl` synchronizes the page scroll with a single scene clock. Apply explicitly plays the remaining explanation while the page follows the scene; scrolling, touch or navigation takes control back. Manual states remain stable across delayed browser scroll anchoring, including an opened replacement interview. Reduced motion uses direct state changes. Mobile graph progression brings the relevant graph/result area into view.

Run `npm test` in this folder for seven regression tests covering clock ownership, interruption, retry, final-state stability, background-tab delays and bounded seeking. Then run `node build.mjs` before `python3 deploy.py`. Main-site browser checks and public deployment verification are recorded in `../../output/corporate-demo/site-feedback-qa.json` and `site-feedback-deploy.json`.

Published and verified `20261011T144210Z`: 7 tests, TypeScript, Vite and 20 Storybook IDs passed. Sixteen public files match the local build. Main-site browser checks passed for the scroll-to-feedback transition, manual reshuffle, persistent replacement interview, meeting graph and matching offer. Local 390×844 checks used the same built site in an isolated iframe; the OS reduced-motion preference was not emulated. Desktop public proof: `../../output/corporate-demo/site-feedback-live.jpg`.


### Main-site corner countdown CTA

The approved Контур button now appears in the main site's fixed header. `CountdownButton` and `cta-offer` are shared with the lab: 50% for the first 10 seconds without a visible timer, then a fixed 30% for 30 seconds with large translucent digits, a retreating outline and blue-to-red color. At 40 seconds the timer, discount and outline disappear; the button continues opening the existing local demo brief.

`SiteCountdownButton` waits until the calendar chapter («Встретьтесь. Без “а когда вам удобно?”») reaches the middle of the viewport. Until then, 50% stays visible with no countdown. Crossing that depth starts the unchanged 10-second 50% phase followed by the 30-second 30% phase. Fast scrolling past the chapter or opening the page at/beyond it also starts the clock. Progress uses monotonic elapsed time from that single trigger; scrolling back, internal navigation and dialog changes do not reset it. Delayed frames cannot extend the offer, and the animation loop stops at expiry. Reduced-motion users receive changing values without rolling digits. The lab retains its own controlled playback and replay clock. SVG outline geometry follows the actual button dimensions, including 390px and 320px page widths.
