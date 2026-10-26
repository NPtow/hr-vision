# Recommendations acceptance

Version 1.0.0, branch `codex/assessment-services`; owner «Ядро».
Scope: isolated local service (actual loopback HTTP + SQLite and subprocesses).
No live model/provider, production deployment, employer delivery or SSH performed.

Initial conditions and thresholds inherited from
`packages/assessment-contracts/ACCEPTANCE.md`: 100 complete compatible profiles,
POST <=2s; saved GET <=500ms; invalid input rejects atomically; deterministic
formula and complete pool; restart/backup recover the exact committed result.

| ID | Required scenario / expected terminal result | Status | Evidence |
|---|---|---|---|
| R1 | tanh per level, conditional q, confidence separate, exclusions change denominator, all excluded null | PASS | `tests/test_engine.py`, explicit .07615941559557649 reference and unequal tanh(mean) |
| R2 | Exact canonical IDs/months, fail filtered, unknown visible with human approval | PASS (service boundary) | field/revision/ranking tests in `test_engine.py`; product UI remains separate |
| R3 | Complete pool, duplicate facts/profiles, incompatible versions/hash/IDs/order, strict probabilities | PASS | engine tests incl 1000 complete and 1001 rejected; HTTP atomic rejection keeps DB empty |
| F1 | Protected reads/writes/metadata; framing, timeout, limits, UTF8/JSON reject without payload/secret logs | PASS | `test_api.py` + subprocess log inspection |
| F2 | SQLite unavailable returns readiness503, health200, no partial result; recovery works | PASS | real DB path replaced with directory and restored in isolated HTTP test |
| P1 | Commit-before-response, same request one row, crash/restart immutable GET without recalculation | PASS | `test_store.py` concurrent 16 submissions and `test_process.py` SIGKILL/restart |
| P2 | 100 actual catalogue profiles, real subprocess POST and saved GET meet thresholds | PASS | test output includes measured times and 3,915,015 input bytes; metrics/logs checked |
| D2 | Online SQLite backup, fresh-path restore, exact ID/results, restore process reads result | PASS | store and CLI/subprocess tests; unsafe overwrite rejected |
| D3 | Independent launch, loopback-only, private permissions, exclusive DB owner, bounded concurrency | PASS | startup/lock/mode/8 slow connection tests |
| I1 | Real HR adapter and product consume saved current recommendation | UNVERIFIED here | owned by product integration workstream |
| I2/I3 | Stale checks, consent, manual approval and authorized employer publication | UNVERIFIED here | consumer owns these guards; pure calculation never publishes |
| D1 | Production release/rollback artifact and post-release smoke | UNVERIFIED | parent release workstream; same-version local restart is not production rollback |
| P2 prod | Long-lived production observation/alert delivery | UNVERIFIED | only bounded local test window observed |
| F2 model | Provider retry/unknown outcome | N/A | no model or external calls in this service |
| P1 queue | Recover in-flight background tasks | N/A | synchronous bounded pure calculation; only committed terminal results accepted |

Evidence command from repository root:
`PYTHONPATH=services/recommendations python3 -m unittest discover -s services/recommendations/tests -v`.
The tests use disposable paths and test tokens only. Synthetic model-shaped
profiles test scoring/contracts; they do not prove model assessment quality.

Verdict: service implemented and verified in isolated local processes. Overall
product/release **NOT READY** until the shared mandatory integration and release
rows have evidence. This file does not claim production acceptance.
