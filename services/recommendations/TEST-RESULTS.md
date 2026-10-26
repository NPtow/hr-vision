# Isolated verification evidence

2026-10-26 04:18 MSK, macOS local checkout, branch `codex/assessment-services`,
base commit `a2ca12dbfb0d04e86270b4f6462b063943fea38a`. Uncommitted implementation
is version 1.0.0. Release owner records final committed artifact separately.

Command:

```sh
PYTHONPATH=services/recommendations python3 -m unittest discover -s services/recommendations/tests -v
```

Result: **34 tests PASS**, 1.309s, Python 3.9.6. Actual child service process, HTTP loopback and
SQLite used for restart/backup/load scenarios. No model/provider mocks substitute
for service execution: model-shaped profile fixtures are already completed inputs.
The service itself has no provider dependency.

Measured 100-profile request with all 16 parameters from bundled trusted rubric:

```json
{"local_hundred_profile_post_seconds":0.234865541,"request_bytes":3915015,"saved_get_seconds":0.07556787500000017}
```

Thresholds: local POST <=2s, immutable saved GET <=0.5s. This is a short local
sample, not a production SLO or extended observation window.

Trusted rubric canonical JSON SHA-256:
`0a60df14697d677eaad8c90137cc4e5bfeb81462391f15d2b38983fe4d2e1e01`.
Bundled and common catalogue equality is a test assertion.

Runtime outcomes verified:

- Reference tanh score .07615941559557649; conditional numeric mass; confidence
  independence; weighted/excluded denominator; unknown all excluded => null.
- Max 1000 candidates retained completely; 1001 rejected. Mixed incompatible
  profiles and duplicate/missing fact rows leave no result row.
- Confirmed mandatory fail remains filtered audit; unknown remains visible and
  requires human approval; exact IDs and industry months with both revisions.
- POST commits input/result before returning; sixteen concurrent identical
  submissions yield one row. GET reads/hash-checks without running scoring.
- SIGKILL after a successful POST, fresh process restart, GET and repeated POST
  return the same ID/content. A second DB owner exits with configuration error.
- Online SQLite backup from running process; fresh-path restore; restart with
  restored DB yields exact saved result. Existing destinations refuse overwrite.
- Real isolated database outage: health200, readiness503, POST503; restoring the
  DB makes readiness200 and subsequent POST succeed without partial rows.
- Eight held connections exhaust bounded capacity with503; closing them restores
  service. Auth/framing/timeout/JSON/UTF8/nonfinite/body limits reject safely.
- Service logs include operation ID and elapsed time without source fields,
  rubric descriptions, payload or test bearer token.

Product UI integration, production smoke, historical artifact rollback and
production alert delivery remain outside these observations; see ACCEPTANCE.md.

## Precision-2 compatibility follow-up

2026-10-26 04:59 MSK, base commit
`d5134ed2` plus uncommitted `services/recommendations/**` changes. The only scoring
runtime change delegates distribution validity to shared
`jev_client.is_choice_probability_distribution`; formula, stored raw probabilities,
exact argmax checks, policy and deterministic IDs are unchanged. Release freezes
the helper beside this service; monorepo launch keeps its previous PYTHONPATH.

Same verification command: **38 tests PASS**, 1.495s. Added candidate and vacancy
Choice vectors totalling .99 and 1.01; raw inputs and output p are unchanged;
derived numeric q sums to one. Off-grid/gross/zero totals and non-maximal selected
options reject. Actual HTTP POST/GET verifies .99 is saved unchanged. A copied
standalone artifact imports the frozen shared helper without the monorepo.

```json
{"local_hundred_profile_post_seconds":0.202083,"request_bytes":3915015,"saved_get_seconds":0.06952908300000016}
```

No model/provider requests, production changes or commit performed by this
workstream during the compatibility follow-up.

## Version 1.1.0: automatic-policy arithmetic and points

2026-10-26 19:32 MSK, branch `codex/automatic-vacancy-comparison`, baseline
`a567c7bac4bbc631050fd73cd3fee81c32b67554`, uncommitted recommendations changes.

Command unchanged: **52 tests PASS**, 1.807s. Existing `test_engine.py` assertions
were not weakened. New open tests in `test_higher_policy.py` verify known-r
arithmetic/ranking agreement with `tanh-v1`, unknown-r signed linear fallback over
2/3/7/10 levels, immutable raw input, conditional probabilities, correct weight
denominator, −1/0/+1 → 0/50/100, null preservation, additive point contributions,
finite ranges, rejection of client-injected result fields and unknown importance.

Real HTTP verifies strict-v1 rejection and v2 calculation from the same unknown-r
profile, server-generated points, immutable GET and no partial stored row on
invalid input. Actual independent process is killed/restarted after a v2 POST;
GET and repeated POST return the exact same saved result. A historical
1.0.1-shaped record without point fields remains unchanged when read by 1.1.0;
the GET path is checked with calculation disabled. SQLite schema is unchanged.

```json
{"local_hundred_profile_post_seconds":0.26016149999999993,"request_bytes":3915015,"saved_get_seconds":0.08359404100000001}
```

These are ordinary implementation/open tests and isolated local runtime checks.
They do not represent isolated coding or protected build/seal/verify completion;
the unavailable protected runner remains the separately accepted exception in
`AUTOMATION.md`. Independent runtime QA and production acceptance belong to their
respective workstreams. No production reads/recalculation, provider requests,
deployments or commits were performed here.

## Version 1.2.0: unknown importance contributes zero effective weight

2026-10-26 20:35 MSK, branch `codex/matching-unknown-importance-zero`, baseline
`b8c76adefa9db9c99d6668f48ace77be09325644`, uncommitted recommendations-only changes.

Before implementation, the new v3 open tests failed with `unsupported_policy`.
After implementation, the unchanged full verification command reports
**64 tests PASS**, 1.897s. `test_auto_policy.py` verifies mixed known/unknown weights,
distinct unknown vs explicit zero exclusions, all unknown => null/unscored,
all five direct no-target point values, retained known-target tanh, raw snapshot
immutability, candidate abstention and unchanged v1/v2 rejection rules.

Real HTTP verifies v1/v2 rejection and v3 success from the same unknown-importance
snapshot, exact saved input and idempotent POST/GET. An independent process saves
both all-unknown and partly weighted v3 results, is killed/restarted, and reads the
same immutable results. A historical 1.1.0 v2 result remains unchanged on GET.
Only tests' expected current service version moved from 1.1.0 to 1.2.0; legacy
policy behavior assertions remain in place.

```json
{"local_hundred_profile_post_seconds":0.245628167,"request_bytes":3915015,"saved_get_seconds":0.08243879200000004}
```

The v3-only additive `source_importance` records the immutable original importance;
`weight` is the effective numeric weight. Source unknown maps to effective zero
with `exclusion_reason:"unknown_importance"`; source explicit zero keeps
`"zero_importance"`. Null totals remain null. SQLite schema is unchanged.

These open implementation tests and local runtime checks do not claim protected
isolated coding/build completion. Independent QA is separate. No deployment,
real-candidate data access, provider/model calls or commits performed here.
