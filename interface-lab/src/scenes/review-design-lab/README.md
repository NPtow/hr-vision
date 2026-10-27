# Candidate review design comparison

Local Storybook gallery: `hr-vision-review-design--gallery`.

Six independently authored refinements of the existing candidate review:

| Query `design` | UI label | Direction |
| --- | --- | --- |
| `system` | Инспектор | Shared alignment and quiet text navigation |
| `soft` | Поверхности | One lightly tinted inspector surface |
| `editorial` | Типографика | Selected direction: typography and reading rhythm with the original icon switcher |
| `compact` | Компактный | Rectangular desktop controls |
| `continuous` | Сегменты | Consistent segmented selection |
| `focus` | Контекст | Question composed with related skills |

Each stylesheet is scoped by `.review-design-lab[data-review-design]`. The gallery reuses `InterviewFeedbackLab` and the canonical candidate review, player, transcript, assistant and Orbit. Switching designs changes the wrapper attribute without remounting the player. Candidate switching uses the existing local preview data source.

The shared production markup only gains hidden tab labels and an answer-label span; visual overrides live inside this gallery. The comparison is not a production release. Backend assessment data and feedback contracts are unchanged.

Design references: local Apple Design guidance, [Impeccable layout/typeset/polish](https://github.com/pbakaus/impeccable), and [Design Spacing & Rhythm](https://github.com/buidangminh23/spacing-skill). Source instructions were read directly; no third-party skill runtime was installed.

Browser verification covered all six variants in transcript, assistant and assessment modes; 1422×800 and 1100×700 CSS viewports; four and six axes; selected long answer; skill/source navigation; single-skill question. `npm run typecheck` and the full Storybook build passed. Local screenshots and measurements are kept outside source in `output/review-design/`.
