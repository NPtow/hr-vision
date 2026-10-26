# Private HTTP API v1

All routes except GET /healthz require exactly one
`Authorization: Bearer SERVICE_TOKEN` header. The authenticated HR backend owns
user/tenant/resource authorization and must not pass its token to the browser.
Unauthorized 401, absent ID 404, invalid input 422, too large 413, exhausted queue
or unavailable storage 503. JSON responses are no-store; errors contain safe
machine codes only. Payloads and provider rejection bodies never enter logs.

POST bodies are application/json with one Content-Length, no Transfer-Encoding,
max 4 MiB and timeout 15s. Duplicate keys, nonfinite numbers, invalid UTF-8 and
unexpected envelope fields are rejected. Oversized input is not truncated.

## Submit and read

`POST /v1/vacancy-profiles` →202:

```json
{"vacancy":{"vacancy_id":"id","revision":"owner-snapshot-revision","data":{"title":"...","description":"...","tasks":[],"clarifications":[]}},"rubric":{"rubric_id":"id","version":"version","parameters":[]}}
```

`parameters` must be a nonempty complete rubric: each
`{parameter_id,title,definition,levels:[{level_id,label,description}]}`, at most 100
parameters with 2–10 distinct levels each. Supplied level order is authoritative.
Use the shared `sales-v1.json` catalog, version 2026-10-26.behavioral-anchors.1.
The request state contains only `{vacancy}`; every Choice sees its complete data.
Inputs are frozen before admission; equivalent canonical input reuses run_id.

`GET /v1/vacancy-profiles/{run_id}` →200 and POST return:

```json
{"run_id":"sha256","status":"queued","profile":null,"error":null,"vacancy_revision":"owner-snapshot-revision","rubric_version":"version","model":"jev-1.14.0"}
```

Statuses: queued,sent,response_saved,completed,failed,outcome_unknown.
Provider call is not retried automatically after failure or uncertain outcome.
Completed profile fields:
`{contract_version:"vacancy-level-importance-v1",profile_id,run_id,vacancy_id,
source_revision,vacancy_sha256,rubric_id,rubric_version,rubric_sha256,
importance_version:"importance-0-3-v1",model,parameters,usage}`.
`vacancy_sha256` hashes data; source_revision is preserved from the owner.

Parameter fields: `{parameter_id,title,levels,required_level_id,importance,
status,raw_answers:{level,importance}}`. Each raw answer is the complete Choice
`{type,choice,confidence,probabilities}`. Level choice includes all level IDs and
insufficient_data; importance choice options I0,I1,I2,I3,insufficient_data map to
numeric importance 0–3 or the literal insufficient_data. Unknown required_level_id
is null. status is irrelevant for known weight 0, needs_review for unknown
importance or unknown positively weighted level, otherwise ready. usage is the
unmodified provider token usage. GET never calls the provider.

## Immutable manual revisions

`POST /v1/vacancy-profiles/{run_id}/revisions` →201 `{profile}`:

```json
{"expected_parent_id":"sha256","author":"hr:user-id","changes":[{"parameter_id":"parameter-id","required_level_id":"catalog-level-id","importance":3}]}
```

Author is the trusted adapter's authenticated principal, nonempty max 200 chars.
Changes must be nonempty, each parameter unique and present in the profile;
required_level_id may be null and importance may be insufficient_data. Invalid
levels, boolean/fractional weights and missing/extra change fields are rejected.
No metadata, catalog or raw answer can be overwritten by a correction.

Each correction creates a new content-derived profile_id with
`revision:{author,parent_profile_id}` and retains original run_id, source hashes,
model usage and raw_answers unchanged. The expected parent must be the current
head; a competing stale update gives 409 revision_conflict. Repeating the exact
same parent+author+changes returns the same stored revision (even after a later
head exists), never creates another mutation. There is no overwrite/delete route.

`GET /v1/vacancy-profiles/{run_id}/current` →200 `{profile}` reads the latest
revision (base before manual changes).
`GET /v1/vacancy-profiles/profiles/{profile_id}` →200 `{profile}` reads that exact
immutable base/manual revision. The original GET by run_id always returns the
original model result; consumers selecting a manual profile must use current
and retain its profile_id for recommendation/staleness checks.

## Health and diagnostics

`GET /healthz` →200 `{status:"ok",version,model}`: process liveness only.
Authenticated `GET /readyz` →200 ready when local storage responds and worker is
alive;503 when unavailable/stopping. Does not probe/pay the external provider.
`GET /v1/diagnostics` gives job counts by status and queue_limit 100.
`GET /v1/vacancy-profiles/{run_id}/diagnostics` gives run_id,status,created_at,
updated_at,elapsed_seconds,response_sha256,error; timestamps are UTC epoch
seconds. No input, prompt, free-form exception, key or rejection body is returned.
