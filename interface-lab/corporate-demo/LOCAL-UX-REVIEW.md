# Local UX review — 11 October 2026

Scope: the HR Vision company site and its embedded product demonstrations. This pass is local only. The existing typeface, palette, composition, graph metaphor and scroll narrative remain the design baseline.

Method: applied the `apple-design` skill and its geometry/layout methodology. The principles used here are immediate response, clear grouping, user control, legibility and adaptive presentation. CSS target sizes and color values are implementation choices for this web page; they are not claimed to be Apple's platform tokens. This is a source-level review with browser verification delegated to the integrated site pass.

## Fixes in `src/ux-polish.css`

| Finding | Change | Why it matters |
| --- | --- | --- |
| Light focus outlines are difficult to distinguish on pale surfaces; focus can be clipped inside product windows. | A consistent darker focus outline; inset outline for clipped interview panels and profile actions. | Keyboard users can see the current action without adding permanent interface labels. |
| Several mobile feedback actions are 35–40 CSS px high, while retry/navigation links are smaller. | Minimum 44 CSS px control height, clearer 12–13 px labels and comfortable line height. | Makes actual interactive targets easier to acquire. |
| Global `button:disabled { cursor: wait }` implies loading for controls that are simply unavailable. | Default cursor for disabled controls; progress cursor only when `aria-busy="true"`. | Visual feedback matches the state. |
| Some controls change only on click/release; hover elevation can remain after touch. | Immediate pressed brightness feedback without overriding scene transforms; hover-only lift removed on non-hover devices. | Actions react during the press and do not remain visually lifted on phones. |
| Graph content is taller than its mobile frame, but the frame uses `overflow: hidden`. | The mobile graph viewport can also scroll manually. Automatic progression still works. | Users can inspect the question, graph or result at their own pace and focus can reveal off-screen controls. |
| Secondary calendar status text and graph captions are very faint. | Stronger neutral text while retaining the blue/green palette. | Status can be read without relying on color or motion alone. |
| Long candidate names, evidence and quotations can escape result cards. | Explicit wrapping, aligned proof icons and slightly larger mobile evidence text. | Profile content remains readable with realistic copy. |
| Transparency and increased contrast preferences have no dedicated adaptations. | Solid surfaces for reduced transparency; stronger foregrounds/borders for increased contrast; visible native colors in forced-colors mode. | The same information remains understandable when atmospheric materials are unsuitable. |

The CSS is deliberately scoped under `.company-site` and must be imported after the scene styles. The root site element needs the permanent `company-site` class. The new demo funnel has its own `.demo-funnel` portal and owns its focus, safe areas and form styling; this sheet does not override it.

## Native mobile framing

The existing `useSceneFrame` fitted a 390×744 composition into the space left under the header. On a 320×568 screen its scale was about 0.61, so a 44px control rendered at roughly 27px. This was a functional readability and interaction defect rather than a visual preference.

`src/useSceneFrame.ts` now uses the actual available width and height at the mobile breakpoint, with scale fixed at 1. Interview and feedback content can scroll within that frame. The interview viewport uses flex sizing so its heading takes its natural height rather than relying on a fixed 94px deduction. Measurement happens before paint and responds to both container resizing and breakpoint changes. Desktop aspect-ratio fitting and the separate lab preview frame are unchanged.

`tests/scene-frame.test.ts` checks readable/tappable size invariants for the 320×568 and 390×844 layouts, behavior in a short viewport and the existing desktop aspect-ratio fitting. Three tests pass.

After the integrated mobile review found the calendar heading under the fixed header, `ux-polish.css` also gives the needs and calendar sections natural page flow at widths up to 600px. Their cards are no longer confined to a vertically centered, clipped sticky canvas; the heading starts below a 108px top clearance and the full form remains reachable even on a 320×568 screen. The graph frame uses the same 108px mobile header clearance. The site owner is switching compact needs to the full editable example rather than depending on the scroll typewriter in this shorter natural section.

## Findings routed to the owning implementation agents

1. **Interview focus used to cause navigation.** `InterviewTour` called `select()` from `onFocus`, coupling Tab navigation to `seek()` and page scrolling. The interview agent confirmed removal of this coupling and is keeping explicit activation separate from focus.
2. **Interview and feedback switched entire layers.** The interview agent is integrating the feedback step into a continuous product surface to retain candidate context and avoid abrupt opaque-layer changes.
3. **Calendar's ARIA radio group lacked arrow-key behavior.** Reported to the site owner. Native radio inputs or the appropriate Arrow/Home/End selection behavior should accompany the visual selection state.
4. **The funnel's actual business destination is not settled.** The funnel agent is implementing a clear local preparation path with retained task, criteria, evidence and a transparent local completion state. It must not imply a real search, invitation or submitted customer request.
5. **Intro gaps, carousel pace, offer timeline and final CTA spacing** are being addressed by the site owner as part of the designer's revision set. This CSS does not duplicate those structural changes.

## Preserved behavior after source inspection

- Chapter playback yields to wheel, touch, pointer, navigation keys and hash changes. `ChapterClock` ignores delayed scroll observations during explicit playback and uses a bounded frame delta after backgrounding. The existing clock tests cover these causal transitions.
- The corner countdown starts when the calendar section reaches the configured viewport depth, then uses a monotonic deadline. Returning up the page does not restart it. The accepted fixed offer phases are unchanged.
- The graph keeps the existing task, requirement and candidate relationships. User feedback changes a requirement and highlights evidence in the resulting candidate.
- Reduced-motion users already receive static or stepped equivalents through the chapter controls. No new looping animation was introduced in this review.
- No extra badges, service icons, explanatory footnotes, new font or glass rebrand were introduced.

## Integrated browser checks still required

These are verification tasks, not claims that they have already passed.

- **Desktop, 390×844 and 320×568:** verify the new native-size interview/feedback frames in the integrated page. On the shortest viewport, reach every form action and result through the scrollable content; nothing should depend on a transform that shrinks the whole product. The static interest scene uses separate framing and still needs a visual readability check.
- **Keyboard:** Tab across all active tour controls, select a criterion, apply feedback, inspect the next candidate, retry, enter the funnel, move between steps, Escape to close and verify focus returns to the trigger. Hidden scene layers must not receive focus.
- **Manual interruption:** start feedback playback and immediately scroll backward or choose another answer. No stale frame should restore the abandoned selection or recapture the scroll.
- **Mobile graph:** confirm the manually scrollable viewport does not create a dead end; page scrolling must continue at its boundaries, and the selected answer/result remains reachable.
- **Reduced motion and transparency / increased contrast:** verify both content and available actions, not just the absence of animation. In particular check that interview reasons and the full graph result can still be read.
- **Long content / zoom:** long business task, candidate surname and quote; no clipped buttons, misplaced focus outlines or horizontal page overflow. Test 200% browser zoom.
- **Corner CTA:** waiting before the calendar does not spend the offer; crossing the calendar depth starts it exactly once. Opening and closing the demo does not reset the clock.

## Verification performed in this isolated pass

- Read the current source for site controls, countdown, chapter clocks, framing, graph, interview review and their styles.
- Parsed `ux-polish.css` with the project's installed CSS build parser; no syntax warnings.
- Ran the three mobile/desktop frame invariants in `tests/scene-frame.test.ts`; all passed.
- Did not run the global build while other agents were editing, did not operate the browser in parallel and did not deploy.


## Integrated browser verification completed

The site owner verified the built local preview at port 4173 on desktop and in same-origin 390×844 and 320×568 browser frames. Both interview feedback branches reach the correct candidate and evidence; opening the answer and returning to the shortlist work. Different business tasks produce different shortlist profiles. The form validates missing contact details and finishes in an explicitly local prepared state; Escape returns focus to the trigger. Calendar selection, arrow-key selection, offer steps and calendar-depth countdown behavior were checked.

The native-size short-screen layout remains reachable. Calendar and needs headings now clear the fixed header. A final 2 px calendar overflow at 320 px was traced to grid intrinsic sizing, corrected with min-width: 0 and constrained day columns, and rechecked: document scrollWidth and clientWidth are both 320. The React update-during-render warning was removed by coalescing derived-motion React state updates in one animation frame; no fresh warning/error appeared in the final built-preview scenarios.

All 21 automated tests passed, and the full TypeScript, site, lab and 24-scene Storybook build passed. The preview remains local; no deployment was performed. Screenshots are in output/local-review-20261011/. This does not claim a physical-device, screen-reader, 200% zoom or special system-contrast audit.
