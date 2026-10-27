# Interviews already conducted and assessed

2026-10-11. Lab proposals for the company's interview chapter.

## Message and composition

Recommended headline: **Интервью проведены. Кандидаты оценены.**
The previous “Сразу интервью. И причины выбора.” did not say that this work was already complete.

Two 40-second alternatives sit next to the original 24-second scene:

- `lab.html#interview/focus`: a moving spotlight keeps the entire profile in place.
- `lab.html#interview/closeup`: the same profile is panned and enlarged around each selected block.
- `lab.html#interview/original`: previous animation retained for comparison.

The four stops are recording → a concrete answer → recruiter assessment, including a gap → the reason this experience serves the employer's task. Each focus holds for roughly 7–8 seconds; the beginning and end show the whole composition. Manual step anchors land after text emphasis has finished. Headline and underlying content stay stable, without narration captions or tooltip paragraphs.

This is an animation prototype. Anna and the illustrated recording are existing fictional demo material, not a newly conducted interview or working video recording. Assessment wording derives from the demo's existing answers (returning customers, three years in B2B, amoCRM but no Bitrix24). No invented numeric score. No external hiring action occurs.

## Research via Context7

Queried `/websites/driverjs` and `/nilbuild/driver.js` for spotlight, stage padding, rounded focus areas, highlight transitions and tours. Queried `/websites/gsap_v3` for pinned, scrubbed timelines and labels.

Primary references:

- [Driver.js simple highlight](https://driverjs.com/docs/simple-highlight): retain interface context and expose one target.
- [Driver.js animated tour](https://driverjs.com/docs/animated-tour): transfer attention between target elements.
- [Driver.js styling hints](https://driverjs.com/docs/styling-hints): overlay, target padding and visual hierarchy.
- [GSAP ScrollTrigger](https://gsap.com/docs/v3/Plugins/ScrollTrigger/): one bounded timeline, named stops and stable holds. Useful for a future scroll-driven landing-page version.
- [Motion layout animations](https://motion.dev/docs/react-layout-animations): preserve the identity of a UI block during spatial transitions.

The implementation adapts the **interaction pattern**, not Driver.js source code or its popover UI. No new dependency was installed. The existing Motion 13 runtime drives the spotlight, camera and inline text emphasis from the lab's single clock. This avoids an independent tour timer continuing after pause or seeking out of sync. No claim that Driver.js or GSAP is running here.

## Implementation boundaries

- New `InterviewTour.tsx`, scoped styles and scene metadata; two real CSF Storybook exports.
- Actual published Storybook IDs are resolved from the build index into `lab-manifest.json`.
- Target bounds measured with ResizeObserver and offset geometry, not layout reads on each frame.
- One scalar clock determines camera transform, spotlight bounds/opacity, and highlighted text. Scrub/replay/step/pause all control the same animation.
- Narrow screens use a single column and pan to the selected block without zooming the text down.
- Reduced motion uses instant focus changes and disables automatic playback through the existing scene host.
- Website scenes and the earlier interest variants retain their original behavior; these proposals are confined to the lab.

## Verification

TypeScript, Vite and Storybook builds pass. All 16 published stories are included in the manifest. Hosted browser QA covers both variants, spotlight bounds, readable target blocks on desktop/mobile, pause/replay/step controls and retention of the original comparison. Deployment receipt: `deployment.json`. Browser and HTTP evidence: `../../output/corporate-demo/interview-deployment-check.json` and screenshots in that directory.
