# Production Jev assessment

Private service on `127.0.0.1:8096`, separate process, SQLite state and credentials.
It has no HR database access. The HR adapter owns authorization and source binding.
The browser never calls TypeSafe or this service directly.

## Product contract

- Issuing an active public profile queues the completed interview's base assessment.
- Attaching a candidate to a vacancy (application, selection cohort or imported intake)
  queues that same base assessment and a separate vacancy comparison.
- The public profile shows base skills. Employer review can also show the comparison.
- Resume/contact edits, opening a profile, repeated events, and another vacancy do not
  rescore the same interview. Changed recording/transcript, prompt pack or model gets a new identity.
- This is evidence for human review. There is no hire/reject action, ranking, or automatic
  change to an application's stage. Missing evidence is unknown, never a zero score.

`assessment_jobs.py` inserts intents in the product transaction, without external I/O.
It resolves one completed interview, one recording and its transcript on the server,
rechecks access/source before storing, and polls the private durable queue.
Intake feedback projections carry their own vacancy fingerprint and reference the
unchanged base revision. Stale projections are not returned. A changed source is
rechecked every 10 minutes after completion; source metadata is checked before each read.
The initial backfill covers active public profiles only, not every historical candidate.

## Pipeline and contract

Pinned model: `jev-1.14.0`; active immutable prompt pack: `2026-10-25.2.json`.
Pack .1 records the initial synthetic experiment. The criteria and 5-level behavior
anchors come from the project's criteria-2026-10-v1 / CD-24/v2 rubric.

1. Strict input schema and injection scan before storage.
2. Deterministic retrieval of up to 10 matching fragments plus immediate neighbors per skill.
3. Four typed evidence calls select source IDs (or NONE). No generated quotations.
4. Typed evidence basis: observed, narrated, hypothetical, unknown.
5. Typed five-level score; expected value is computed from validated probabilities.
6. Independent groundedness call; publish only supported evidence.
7. Code assembles labels, exact quotes and source timestamps.

Evidence/basis threshold 0.55; groundedness 0.75; comparison threshold 0.60.
These are operational gates, **not calibrated probabilities of job performance**.
One selected episode cannot establish the rubric's level 5. Display is capped at 4,
or 3 for hypothetical answers. Self-reports are explicitly marked. Raw measurement
and the reason for the cap are retained. Retrieval coverage is recorded; this does
not claim exhaustive recall on long interviews or validate real-world performance.

Comparison receives only explicit vacancy requirements and base evidence, then returns
`evidenced`, `clarify`, or `not_required`. It cannot change any base score. No protected
traits or non-work personal factors are evaluation criteria.

## Private HTTP

All routes except health require constant-time Bearer verification.

| Route | Body/result |
|---|---|
| `GET /healthz` | version, model, process health |
| `POST /v1/jobs` | `{subject,sourceHash,chunks:[{id,text,speaker,startMs,endMs}]}` |
| `POST /v1/matches` | `{baseJobId,vacancyId,vacancyHash,requirements:[{id,text}]}` |
| `GET /v1/jobs/{id}` | id, status, assessment, error code |
| `DELETE /v1/jobs/{id}` | cancel and erase payload/result |

States: queued, running, completed, failed, cancelled, expired. Job identity includes
sanitized inputs, exact pack hash and model. One consumer; completed stages replay
from cache. A persisted `sent` stage with no confirmed result fails
`provider_outcome_unknown` after restart. It is never automatically charged again.
An invalid schema gets exactly one bounded retry; network/HTTP failures do not.
Manual regeneration after a terminal provider error requires an explicitly reviewed
new revision; clearing the calls ledger to retry is prohibited.

Limits: 100 queued/running jobs, 5 new jobs per subject per day, 600 chunks, 220 KB
source payload, 130 KB per model call, 48 questions/call, 55 seconds/provider call,
600 seconds from first call to starting another stage (last stage can finish up to
58 seconds later), 900,000 conservative token units/job and 6,000,000/day.
Budget reserves input UTF-8 bytes plus output allowance before each call. Actual
provider usage is retained separately; this deliberately overestimates input tokens.

Payload retention 7 days; result/evidence/call retention 90 days. IDs/tombstones remain
to avoid silent re-billing. HR-owned published projections follow the HR retention
policy; revoking a link immediately closes public access and does not erase other
authorized employer access. Logs contain job IDs, version/model and error codes, not
transcripts, prompts, tokens, contacts or provider response bodies.

## Deployment and recovery

1. Run service, adapter, public-profile, selection, assessment and receiver tests,
   frontend typecheck/build, then the opt-in live synthetic golden set.
2. Copy committed `services/assessment` to
   `/opt/hr-vision-assessment/releases/<40-character commit SHA>` and verify hashes.
3. Run `sudo python3 RELEASE/deploy/install.py RELEASE`. It refuses to replace a worker
   with active jobs, uses the existing approved JEV key/proxy, writes root-only env files,
   starts the private service, and stages the HR bridge EnvironmentFile. It does not
   restart HR API. Re-runs preserve the service token and provider configuration.
4. Install the tested CI receiver allowlist containing `assessment_jobs.py`. Deploy
   the HR app through the normal main CI release with the existing live-meeting guard.
5. Check both releases, service health, unauthorized rejection, job completion, and
   anonymous public-profile UI/citation seeking. Only then call the deployment verified.

Secrets: `/etc/hr-vision/assessment.env`, `/etc/hr-vision/assessment-bridge.env` (0600).
State: `/var/lib/hr-vision-assessment/jobs.sqlite3` (0700 directory, 0600 database).
Worker has DynamicUser, read-only system, no home access and memory/CPU limits.
No public port or Caddy route is added.

For recovery, stop new intake with `HR_ASSESSMENT_ENABLED=0` during an authorized,
guarded HR deploy; preserve queue/calls. Roll back only to an assessment-capable HR
runtime and compatible worker pack. The receiver refuses removing the adapter while
queue data exists. Never clear evidence or bypass a live-meeting guard for rollback.

## Verification

`python3 -m unittest discover -s services/assessment/tests -v` is offline.
`tests/live_goldens.py` is opt-in and makes paid requests with an operator-supplied
`ASSESSMENT_JEV_KEY`; it contains synthetic data only. Recorded .2 acceptance covers
positive evidence, no evidence, prompt injection, recruiter-only speech and vacancy
comparison with invariant base scores. This is a regression gate, not psychometric calibration.
