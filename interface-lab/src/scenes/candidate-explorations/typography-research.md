# Typographic pause states

Two local candidate-cabinet explorations, October 13, 2026. Scope is the pause-state component, not production search status or a new onboarding path.

## Direction

1. **A new chapter, on paper** (`variant=0`). A restrained personal receipt uses line-by-line text reveal, a small settled ink mark, and a perforated status footer. Main reveal finishes at 1.38 seconds. The status is explicitly “Поиск на паузе”; the user can resume or open “История поиска”. Previous processes are described as preserved in history. No salary amounts, dates, employer names, or fabricated offer data.
2. **An underlined statement** (`variant=1`). An editorial statement fills the surface. “хорошо” and “классной” receive one short SVG underline after the text enters. Last underline finishes at 1.56 seconds, then the whole composition stays still. No looping, random particles, sound, or forced waiting before input.

Both use the requested sentence “Я хорошо зарабатываю в классной компании”. The phrase is the person's chosen pause-state sentiment, not a claim inferred from platform data. Parent must display the component only in that intentionally chosen state.

## Research performed

Read `apple-design/SKILL.md` and `references/geometry-and-layout.md`: optical text hierarchy, immediate button feedback, reduced motion, and purposeful animation. Spacing, blue, radii, and durations here are HR Vision web design decisions, not Apple's proprietary parameters. Inter is already installed in this lab.

Context7 CLI was actually used, in order:

```text
c7 search "Motion React useReducedMotion stagger pathLength SVG animation"
c7 docs /websites/motion_dev_react "useReducedMotion initial animate SVG pathLength stagger children whileTap duration" 3200
c7 docs /websites/motion_dev_react "SVG line drawing motion.path pathLength initial animate transition duration" 2000
```

Search resolved the official Motion React documentation (`trust=9.7`, 314 snippets). The second query was broad and returned partial matches; the exact SVG behavior was verified in the official docs rather than invented from the query.

Primary references:

- [Motion useReducedMotion](https://motion.dev/docs/react-use-reduced-motion): user's motion preference replaces positional entry with a short fade, and line drawing is static.
- [Motion SVG animation](https://motion.dev/docs/react-svg-animation): `motion.path` / `pathLength` draw a restrained underline.
- [Motion gestures](https://motion.dev/docs/react-gestures): `whileTap` gives immediate press feedback without blocking callback input.

No external source code copied. Original component composed with the project's installed `motion@13.4.6` API; no Motion+ paid components used.

## Component contract

`CelebrationTypography({ variant, runId, onResume, onProcesses }: CelebrationProps)` imports the shared `./types`. `variant` 0 and 1 select the compositions. Changing `runId` remounts only the animated stage for replay. Both callbacks always remain available; no network, no backend mutation, no global listeners, no animation timers, no local account state.

CSS is scoped to `.ce-type*`. Container queries adapt inside a 390px phone pane or wide desktop canvas, including side-by-side browser previews. Font resizing is fluid. The component title has a single accessible label, decorative SVG and repeated text are hidden from screen readers, and keyboard focus is visible.

## Validation boundary

Root owns Storybook registration, browser visual checks and final gallery. This component does not add its own scene controls inside the product UI. Replay belongs in the lab shell. Local callback and responsive checks do not establish production pause behavior.
