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
