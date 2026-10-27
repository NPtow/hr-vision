# Candidate pause: two original motion directions

Scope: `CelebrationPath.tsx`, `celebration-path.css`. These are local, illustrative pause states. The agreed model moves all processes to search history on pause; the integrating parent owns that state change. This component itself does not mutate a real candidate or application.

## Context7 evidence — 2026-10-13

Executed the required sequence:

1. `c7 search "motion react"` → `/websites/motion_dev_react`, trust 9.7, 314 snippets.
2. `c7 docs /websites/motion_dev_react "SVG pathLength animation useReducedMotion spring interruptible transition" 3200`.

Relevant source results:

- [Motion SVG animation](https://motion.dev/docs/react-svg-animation): SVG motion elements, path drawing with `pathLength`, explicit transform origins. Also checked the official page directly.
- [Motion accessibility](https://motion.dev/docs/react-accessibility): preserve outcome and controls, substitute fades/static targets for spatial movement with reduced motion.
- [useReducedMotion](https://motion.dev/docs/react-use-reduced-motion): preference changes trigger a render; the scene follows the current preference, not a startup snapshot.
- [Motion gestures](https://motion.dev/docs/react-gestures): immediate press feedback with `whileTap`.

Read `apple-design/SKILL.md` and `references/geometry-and-layout.md`: spatial consistency, short interruptible response, optical alignment, and explicit distinction between Apple guidance and our chosen web geometry. The Apple HIG motion URL returned a JavaScript-only page to the web reader; no additional claim is sourced to that unavailable page.

## Variant 0 — a route becomes an arrival

A pale route receives a blue-to-teal trace. Three small stops resolve along it; the end becomes a geometric work bag and a lime flag. The trace supplies direction, the bag supplies meaning. The final illustration is quiet and static. This is not a fixed hiring funnel: the decorative dots do not assert a count or order of real interview stages.

The longest sequence finishes at 1.68 seconds. No infinite motion, particle loop, large backdrop animation, sound, or generated assets. Artwork is original SVG.

## Variant 1 — the steps settle into a result

Three abstract profile/calendar/document cards unfold from one stack. A strong blue bag sits in front as the single result; a small lime confirmation mark is the final beat. This is a distinct spatial composition and animation, not a color swap of variant 0.

The longest sequence finishes at 1.62 seconds. Transform origins keep each paper attached to its source before it settles. Springs have no bounce; hover/press response remains available during entrance.

## Interaction and accessibility

- Exact heading: «Я хорошо зарабатываю в классной компании».
- «Поиск на паузе» communicates state. The caption states that all processes are saved in history. «История поиска» invokes `onProcesses`; «Снова искать работу» invokes `onResume`. Both remain available during the animation.
- Replay remounts only the illustration and its short heading entrance; explicit replay is a reset, not a queued competing animation.
- Parent `runId` resets the same finite entrance. No external timers to leak on unmount.
- Reduced motion: render the final illustration and copy directly; no traced route, translation, scale, or rotation.
- No real requests, sends, camera access, permissions, or backend mutation.
- Container queries adapt to narrow embedded previews independently of browser width.

Validation: targeted TypeScript check passed with `tsc --ignoreConfig --noEmit --strict --skipLibCheck --target ES2022 --lib ES2022,DOM,DOM.Iterable --module ESNext --moduleResolution Bundler --jsx react-jsx --esModuleInterop --types vite/client src/scenes/candidate-explorations/CelebrationPath.tsx`. Full-project check was blocked only by a separate `ProfileVariants.tsx:39:241` value typing error at the time of this check. Visual and interaction validation is still needed in the integrating Storybook scene. Library-specific imports and typing use the repository's installed `motion` 13.4.6 and React 19.3.0.
