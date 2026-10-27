# Invitation / mutual-interest experiments · 2026-10-11

The screenshot shows the `interest` chapter, not the `connections` graph. The lab preserves the graph and adds a direct link from it to the invitation comparisons.

## Sources inspected with Context7

- Motion for React: `/websites/motion_dev_react`; installed Motion 13.4.6. [Motion values](https://motion.dev/docs/react-motion-value), [useTransform](https://motion.dev/docs/react-use-transform), [SVG animation](https://motion.dev/docs/react-animation). Use one progress value for every position, fold, flip, opacity and SVG gradient. This makes reverse scrubbing and pause exact.
- Magic UI: `/magicuidesign/magicui`; [Animated Beam](https://magicui.design/docs/components/animated-beam). Inspected the public bidirectional SVG-gradient implementation. The signal experiment adapts the technique into a finite invitation-and-reply sequence; it does not run perpetual beams.
- Motion Primitives: `/websites/motion-primitives`; [Morphing Popover](https://motion-primitives.com/docs/morphing-popover). Useful continuity principle: change the same object's state instead of layering unrelated badges over it. The cards use explicit progress-driven transforms rather than a spring layout animation, so the lab can freeze mid-flip.

No new package, remote image, paid Motion+ source, timer or network request was added to the animation core. Demo identities come from the existing fictional candidate fixtures.

## Directions

1. **Письма / letters** — a stack of addressed envelopes fans out; flaps open; letters lift and turn into personal replies. Soft peach, mint and lilac paper with a restrained shadow.
2. **Импульс / signal** — invitations travel along continuous curves. Arrival rings and replies follow in sequence; a return signal reaches the vacancy. Names stay in fixed, unobstructed cards.
3. **Карточки / cards** — profiles spread from one stack; the invitation becomes the card surface; each card turns into the person's reply. The same card persists throughout.
4. **Исходный / original** — the previous version remains for comparison.

Each new direction is a real Storybook story, 14 seconds long, with shared play/pause/replay/speed/scrubbing, desktop/mobile layouts, and a static completed state for reduced-motion preference. Switching a variant creates a new iframe and resets its clock. The full website retains its current animation pending selection; experiments are scoped to the lab.

## Public entry points

- `/demos/company-v1/lab.html#interest/letters`
- `/demos/company-v1/lab.html#interest/signal`
- `/demos/company-v1/lab.html#interest/cards`
- `/demos/company-v1/lab.html#interest/original`

Build validates the actual generated Storybook IDs from `index.json` before emitting `lab-manifest.json`.

## Verification

Published release: `20261011T022427Z`.

- TypeScript, Vite and Storybook build completed; 14 generated story IDs verified.
- Public site, lab entry, manifest and Storybook index match local build bytes (`output/corporate-demo/interest-deployment-check.json`).
- Live browser: all three new variants and original load; graph shortcut opens the comparison; switching the interest tab to the same default variant does not leave a loading overlay.
- Pause: all three letter-sheet style values remained identical over an independent check interval.
- Replay: progress reset from 78% to approximately 2% before pausing; seeking back to 78% produced exactly the same sheet styles.
- Desktop and 390×844 inner mobile canvases visually inspected. Letters and cards finish as a vertical reply list on mobile; signal finishes with the vacancy and all three replies visible.
- Corrected paper flap stacking, overlapping transition statuses, and invitation overlays so names remain visible.
- Browser console errors: none in the checked flow.
- Reduced motion is implemented as the static completed concept state; OS preference emulation was not performed.

Screenshot: `output/corporate-demo/interest-letters.jpg`.
