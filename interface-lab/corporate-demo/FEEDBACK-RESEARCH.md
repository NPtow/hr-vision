# Feedback animation comparison — 2026-10-11

The user rejected the existing comment → generic graph → candidate animation. These two experiments show the effect on actual candidate UI. They are lab variants; the full company page keeps its current scene until a variant is chosen.

## Ready examples inspected

Context7 `c7 search` and `c7 docs` queries covered Aceternity UI, Motion Primitives and Animate UI. Aceternity's public manual component sources were then inspected in the browser.

- [Animated Testimonials](https://ui.aceternity.com/components/animated-testimonials): overlapping cards, subdued inactive cards, rotation/depth and active-card lift/settle. Adapted to candidate cards in **Пересборка**. Random rotations and autoplay intervals are replaced by seeded poses and the existing lab clock.
- [Compare](https://ui.aceternity.com/components/compare): two layers, clipped after-state, moving boundary with a light gradient. Adapted to live profile markup in **До / после**. No particle engine or additional runtime dependency.
- [Aceternity licence](https://ui.aceternity.com/licence): the public components can be incorporated into end products. These experiments adapt interaction patterns; they do not republish the source as a component library. No paid source was used.

## Narrative and controls

Feedback about Bitrix24 becomes a requirement. In Пересборка (16 s), the candidate deck opens and Mikhail moves to the front. In До / после (14 s), Anna's profile is progressively replaced by Mikhail's, showing the CRM experience difference. All profile data is demo data.

Both variants use the existing play, pause, replay, speed, chapter and scrub controls. Clicking the feedback advances to the result. Reduced motion renders the final state. Original feedback remains available for direct comparison.

## Validation

- TypeScript, Vite and Storybook build passed; the manifest contains all 18 actual Storybook exports.
- Published release `20261011T032201Z`. Six public files, including the scene bundle, matched the local build byte for byte.
- Browser review covered both variants at 1280×720 and 390×844, forward/backward chapter seeking, playback/pause and clicking feedback to reach the result. Both mobile scenes fit the viewport without horizontal overflow. The original scene remains accessible; no browser console errors were captured.
- Compare fades its text blocks around the moving boundary, avoiding mixed fragments of the old and new profile text.
- Reduced motion is implemented but was not separately browser-emulated.
- Evidence: `output/corporate-demo/feedback-deployment-check.json`, `feedback-browser-check.json`, `feedback-reshuffle.jpg` and `feedback-compare.jpg` in the HR Vision workspace.
