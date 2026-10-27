# Private DSA assessment preview

This pipeline reads the private local vacancy, candidate transcripts and the
versioned assessment-core anchors. It does not update production candidates,
send messages or make hiring decisions. Personal data and every Jev request and
response remain in a user-private directory outside Git and static builds.

## Inputs

The private data directory must contain `candidates.json`, `transcripts/`,
`vacancy-anchor-audit.json`, `vacancy-assessment-text.txt`, and the successful
vacancy-selection response at `jev/vacancy-response.json`. Pass `--data-dir`
to change the location and `--core-dir` for a different checked-out method tree.
The default paths match the local research setup and are not deployed paths.

## Run

From `interface-lab`:

```sh
python3 scripts/dsa_assessment_preview.py --prepare-only
python3 scripts/dsa_assessment_preview.py
python3 scripts/dsa_assessment_preview.py --render-cached
python3 -m unittest discover -s scripts -p 'test_dsa_assessment_preview.py'
```

`--prepare-only` prepares request data without network calls. The normal run
uses only the installed Jev skill client, with at most three simultaneous API
calls. Existing successful matching responses are reused. Failed or unknown
POST outcomes are never retried automatically. Do not run several writers
against the same data directory: the normal process already runs three workers.
`--render-cached` rebuilds the view from saved responses without API access.

If the private directory contains `reviewed-source-overrides.json`, every cached
render reapplies the manual source review. Its `exclusions` remove a source from
explicit skill bindings (or `skills: "*"`); its `contexts` replace reading text
with an inclusive range of original candidate turn indices. A missing binding,
missing turn, another speaker, or another candidate fails the operation before
the reviewed view is published. Removal of evidence from a scored criterion
also fails: a reading review cannot silently alter a score.

Adjusted citations carry `reviewContext`, `evaluationSourceId` and
`evaluationTurnIds`. These distinguish the expanded or trimmed reading context
from the original quotation that Jev evaluated. Model units and probabilities
remain untouched. `reviewed-source-audit.json` records reasons, original source
IDs and old quotation text privately. It must never be committed or served as a
public asset. Rebuilding twice preserves these overrides without API calls.

The local preview server serves `assessment.json`, resumes and protected video
routes; it must listen on loopback only. Real data must not be copied into
Storybook source fixtures, committed, or included in production/static builds.

Start the local services in separate terminals if they are not already running:

```sh
python3 scripts/serve-assessment-preview.py
HR_BUILD_TARGET=mockups npx storybook dev -p 6032 --host 127.0.0.1 --no-open --disable-telemetry
```

Open `/iframe.html?id=hr-vision-assessment-interpretation--dsa&viewMode=story`
on the Storybook server.

## Original indicator method (preserved traces)

All original interview turns are covered by approximately one-minute retrieval
episodes. Local heuristics mask identifying contacts/names and recognized personal
sentences before the request; they are not a guarantee of complete redaction.
Scoring instructions exclude personal traits and health. ASR metadata is kept separately. A source is selected from
existing candidate-only quotations before independent indicator questions are
asked. Adjacent ASR turns may be concatenated verbatim. The code resolves IDs,
text and playback time from source records; the model never generates links.

The full transcript is considered for retrieval, but only up to four selected
episodes per skill receive detailed indicator checks. This is not exhaustive
proof over every possible event. Retrieval probabilities are ranking signals.

The preview has two explicit, uncalibrated thresholds:

- `0.5` relevance makes a quotation available for reading; it does not set a level.
- `0.8` support is required for a behavioral indicator and admissible evidence
  class. General claims, hypothetical answers and interviewer-supplied solutions
  are quote-only. Class probability controls the admissibility cap; a weak argmax
  cannot produce a stronger evidence class.

Concrete narrative is capped at 3, incomplete narrative at 2, independent
observed sales roleplay at 4. No level 5 is generated. Missing evidence is never
level 1. An explicit concern can set 1 within an admissible episode. Contradictory
episode levels separated by two or more points produce `conflict` and no combined
level. Repeated/overlapping stories do not establish independence or raise a cap.
`NEW-01` has no presale anchors and remains quote-only.

This is a documented preview policy, not execution of the assessment core's
complete rating/admissibility engine. It uses draft method blocks. A result of
no supported levels says only that the preview policy did not establish levels
from these particular interviews; it is not a negative candidate assessment or
proof that the production core failed. Human feedback and hiring decisions are
separate from these immutable model traces.

Every output preserves actual model versions, raw probabilities, matched actions,
raw levels and display levels separately. ASR confidence and model confidence
are not a competence grade or a measured accuracy percentage. The UI should
explain the method in its inspection view rather than invent a single confidence.

## Direct JEV score exploration, 2026-10-25

The DSA story now displays `modelEstimate`, produced by `dsa_jev_direct_score.py`,
in five alternative graphs (`graph=orbit|lanes|petals|matrix|timeline`). The original
`level`, evidence class and indicator-policy traces remain unchanged. They are not
silently converted to direct scores.

Six requests / 66 questions returned 33 direct scores and 33 material checks from
`jev-1.14.0`. Existing reviewed professional quotations and preceding context are
sent per skill together with the professional vacancy. This pass does not score
every transcript token: retrieval across the full interview already selected its
source set. A score is provisional interpretation of the described approach.

`score` is an expected zero-based index over five descriptions. Display adds one;
it never substitutes confidence for skill level. Rounded probability buckets may
differ from the rounded score by at most 0.055. Confidence and the five-bucket
distribution remain inspectable. No former 0.8 display gate is applied here.
`NEW-01` uses an explicit prototype handoff scale; other dimensions use core
presale descriptions. This is not the production core scoring engine.

`reviewed-explanations.json` is a private, source-bound editorial layer with 62
bindings (candidate, criterion, evidence). It supplies a short explanation and a
single-action question. The loader rejects missing or foreign bindings and adds
real end times from source turns. It does not claim these explanations were generated
by JEV. Regenerate the merge without network calls:

```bash
python3 scripts/dsa_jev_direct_score.py --render-only
```

Feedback appears for the current source block during playback or after a seek.
There is no timer or separate rate button. Overlapping skills can be selected;
comments lock the visible question while playback continues. Browser-only opinions
use candidate + revision + criterion + evidence keys, independently of model output.
The local prototype never sends candidate messages or changes hiring status.
