# Object celebrations · HR Vision candidate

Two original SVG/CSS illustrations for the pause-search screen. No imported artwork, remote assets, sound, loops, confetti, mascot or production side effects. The scene consumes `CelebrationProps`; the parent decides what `onResume` and `onProcesses` do.

## References actually read

2026-10-13: read `interface-lab`, its current-lab reference (which points to a different checkout; this task uses HR Vision's existing Storybook), `apple-design`, its geometry methodology, and `context7`.

Context7 calls:

```text
c7 search "motion react"
# selected the returned /websites/motion_dev_react, trust 9.7
c7 docs /websites/motion_dev_react "SVG pathLength drawing animation rotateY 3d transform useReducedMotion spring motion button" 3800
```

Relevant primary references returned by Context7:

- [Motion accessibility](https://motion.dev/docs/react-accessibility): swap spatial motion for static/opacity feedback when reduced motion is requested.
- [Motion useReducedMotion](https://motion.dev/docs/react-use-reduced-motion): the hook reacts to the current device preference.
- [Motion gestures](https://motion.dev/docs/react-gestures): immediate `whileTap` response without locking controls during an animation.
- [Motion transitions](https://motion.dev/docs/react-transitions): authored transform/opacity sequences; durations and easings here are our design choices, not Apple's published tokens.

Existing dependency inspected: `motion: 13.4.6`, React 19.3.0. No new packages. Apple principles used: a familiar physical metaphor, coherent nested radii, restrained typography, immediate response and a reduced-motion alternative. The SVG geometry is our web adaptation, not a reconstruction of Apple assets.

## 0 · Моё место

A calm workstation assembles: laptop settles onto the desk, a screen check draws, the plant gives one small wave, and a cup's steam appears. The total intro ends at 1.45 s. Desktop uses illustration left / copy right; phone stacks them. The warm plant pot is the only warm accent. The smile is carried by the pot's small curve, not an anthropomorphic mascot.

## 1 · Новый пропуск

A small physical pass begins on its “Ищу своё место” side and flips to “Хорошая работа. Моё место.” A perforation and restrained barcode give it a tangible shape. One small sparkle arrives after the result. The intro ends at 1.45 s. No indefinite floating or shimmer. At reduced motion the completed front face is visible immediately.

## Interaction and boundaries

- Click/tap the illustration or focus it and press Enter/Space to replay; resetting `runId` or changing `variant` restarts only the art subtree.
- `Снова искать работу` → `onResume`; `История поиска` → `onProcesses`.
- The heading reflects the user-requested positive pause wording; it does not infer actual employment. This is an illustrative state controlled by the lab.
- Search pausing moves former processes into history; no history or meeting is discarded. The parent owns those records.
- No remote calls, browser permissions, notifications or data writes.
- Styles are scoped under `cex-object-*`; the story shell remains the responsibility of the parent scene.
