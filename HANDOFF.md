# Selection UI handoff — 2026-10-26

Owner: Верстак. Integrator/release owner: Ядро. Branch `codex/selection-vacancy-ui`, base `a2ca12db`.

## Behavior

- Only Candidates and Vacancies remain. Legacy `tab=assessments` opens Candidates. The global directory no longer silently selects a vacancy.
- Default filter requires `hrInterview.state=ready`, `allPartsReady=true`, and positive recording and primary-transcript counts. A closed room, assessment, employer meeting, incomplete recording parts, or absent metadata cannot qualify. Reset restores this filter.
- The existing candidate assessment and its raw probability/insufficient-data view open from Candidates. Candidate facts can be edited explicitly; no facts are inferred from residence or general experience.
- Opening one vacancy loads its saved requirements, saved recommendation result, and vacancy-scoped candidate/nomination directory. The backend owns scores, order and eligibility. Hard failures are hidden; unscored/pending rows have no invented zero or rank.
- Requirements show required levels and importance separately. Manual corrections submit a new profile revision. Unknown required facts remain null, not false/zero.
- Invitations require an explicit selection and current matching/profile revisions. Unknown mandatory conditions need a separate per-candidate approval. Submission uses a stable request ID, including an explicit retry after a lost response. Reads never start models or send invitations.
- Invitation acceptance, verification, delivery-provider acceptance, candidate consent, and employer publication remain distinct states. Verification failure offers an explicit retry; no automatic POST retries.

## Backend contract

The UI follows the frozen contracts in `packages/assessment-contracts/README.md` and `ACCEPTANCE.md` in the assessment-services worktree. Production requests use the existing authenticated `selectionRequest` bridge; fixtures are not imported by runtime UI.

| Endpoint under `/api/hr/selection/` | Usage |
| --- | --- |
| `candidates`, `candidates?vacancyId=…` | Global and vacancy-scoped directories, `hrInterview`, nomination and delivery states |
| `candidates/{id}/readiness`, `/assessment` | Existing assessment read/explicit calculation |
| `candidates/{id}/required-fields` | Read/save canonical work-location, employment and industry facts |
| `vacancies/{id}/profile` | Read/explicit model extraction, `{revision}` |
| `vacancies/{id}/profile/revisions` | `{revision, profileId, changes}` with required level and importance |
| `vacancies/{id}/required-fields` | Read/save mandatory vacancy conditions, `{revision, fields}` |
| `vacancies/{id}/recommendations` | Read/explicit comparison, `{revision, candidateIds}` |
| `publish` | `{vacancyId, candidateIds, requestId, recommendationId, revision, unknownApprovals}` |
| `nominations/{id}/retry` | Explicit `{revision, recommendationId}` |

Source-of-truth details:

- Recommendation parameter keys are `excluded`, `exclusion_reason`, `m`, `levels[].p_raw/q/u/contribution`; coverage uses `included_parameters`, `excluded_parameters`, and weights. No browser-side substitute formula.
- Option IDs/labels come from required-fields responses (`locations`, `employmentTypes`, `industries`); UI has no hardcoded production taxonomy.
- Profile/recommendation identity, contract version, source revision, vacancy profile ID and rubric hash must agree before scores are current or invitation controls are enabled.
- `pendingVerification` is acknowledged separately from `proposed`. The vacancy directory supplies nomination `id/state/error/retryAt/attempts`.

## Owned files

Runtime: `interface-lab/src/scenes/selection-workbench/` only (SelectionWorkbench, CandidateDetail, VacancyDetail, VacancyRequirements, RequiredFieldsEditor, MatchExplanation, matching-contract, api, review-state, scoped CSS).

QA: `interface-lab/scripts/selection-statuses.component.test.mjs`, `selection-vacancy.fixtures.mjs`, `selection-vacancy-preview.mjs`, and `fixtures/selection-matching.json`.

No Python, production configuration, authentication, deployment, CI, call-provider, or employer review code changed. No real candidate notifications sent.

## Verification

Passed on 2026-10-26:

1. `npm run typecheck`.
2. `HR_BUILD_TARGET=service npm run build` (Storybook and observability build complete).
3. `node --test scripts/selection-statuses.component.test.mjs`: **14/14 passed** using installed Chrome. Covers HR evidence baseline; candidate assessment/raw probabilities; combined filters/reset; engine ranking/fail/unscored/pending; unknown approval; ambiguous response idempotency; stale identity rejection; versioned requirements; facts/unknown handling; unavailable service; legacy route/read-only behavior; 390px containment and keyboard dialog escape; pending verification; explicit retry.
4. Manual Storybook inspection: candidate assessment drawer and vacancy desktop/mobile layout. `git diff --check` passed.

The matching fixture was generated by the actual pure `recommendations.engine.recommend` function from synthetic inputs. The tests stub HTTP and block non-local requests; they **do not** prove JEV output, live backend compatibility, delivery, or production acceptance. Ядро performs combined integration/release checks.

Local evidence (not part of the commit): `output/playwright/selection-vacancy-ui/` contains test/typecheck/build logs and `candidate-assessment.png`, `vacancy-desktop.png`, `vacancy-mobile.png`.

The Storybook dev page logs two pre-existing missing-dev-resource 404s (`vite-inject-mocker-entry.js`, `observability.js`). They did not prevent these UI paths. The production build does generate `observability.js`. No claim that all browser console output is clean.

## Preview / reproduction

Current isolated preview:

`http://127.0.0.1:6028/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=selection`

From `interface-lab`, run in separate terminals:

```sh
node scripts/selection-vacancy-preview.mjs
HR_BUILD_TARGET=service HR_DEV_API=http://127.0.0.1:8498 npm run storybook -- --port 6028
```

API binds loopback only, uses synthetic in-memory data and never forwards to production. Fixture mutations disappear on restart. The local `interface-lab/node_modules` symlink is intentionally untracked and must not be cherry-picked.

For the component test in this environment:

```sh
PLAYWRIGHT_MODULE='/Users/NIKITA/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs' \
CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
node --test scripts/selection-statuses.component.test.mjs
```

Cherry-pick this branch's UI commit into the combined branch. Re-run shared typecheck/build and actual backend acceptance there. Do not deploy this isolated branch by itself: its new endpoints are supplied by the core branch.

## Follow-up: complete directory pagination

The initial UI assumed a complete directory from one GET. The current backend returns `count/offset/limit/nextOffset` and defaults to 200 rows. The follow-up commit adds `directory.ts`, used by both the global directory and the vacancy-scoped directory. It requests 500 rows per page and resolves only after `nextOffset=null` and a consistent complete traversal. It rejects missing metadata, count drift, gaps/short pages, non-progressing or invalid continuations, and missing/repeated candidate IDs. `vacancyId` is preserved on every page. Page errors render a whole-catalog error instead of a partial list or a false empty result; 401/403 still expire the session.

The matching command still uses the unchanged HR recording AND primary-text/all-parts predicate. More than 1000 ready candidates produces an explicit error and disables calculation; it never slices the pool. Saved recommendation scores/order and requirement levels are unchanged. This fix addresses catalog truncation at scale; Ядро confirmed that the current production count is 78, so pagination does not explain the reported three missing candidates.

Follow-up evidence: `node --test scripts/selection-directory.test.mjs` **35/35 passed** (12 main scenarios plus validation subcases); `npm run typecheck` and `HR_BUILD_TARGET=service npm run build` passed. Logs: `output/playwright/selection-vacancy-ui/pagination-{unit-tests,typecheck,build}.log`. The fixture server and existing component-test request stub now retain query parameters and return pagination metadata.

**No browser was started or tested for this follow-up** because Work browser access is disabled. The earlier 14 browser passes and screenshots describe the preceding UI commit, not this pagination change. Backend/API/deployment are untouched. Restart the synthetic preview API to load its updated fixture code before reusing the local preview.
