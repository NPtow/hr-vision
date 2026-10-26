# Recommendations 1.0.0

Independent deterministic process with its own SQLite. Owner: assessment core
team (thread «Ядро»). The authorized HR backend supplies immutable candidate and
vacancy profiles plus explicit confirmed structured facts. This service neither
loads HR databases nor calls a model. The HR backend retains tenant/source
authorization, current-version checks, candidate consent and employer delivery.

The API implements `packages/assessment-contracts/README.md`. The bundled
`rubrics/sales-v1.json` is frozen into the release. Profile hash, rubric ID/version,
all parameter IDs and ordered level definitions must match it. Runtime does not
read another service's files or database. Public identifiers alone grant no access.

## Run

Python 3.9+ standard library (verified on 3.9.6); no installation or paid dependency. From repository
root, load a private 64-hex token through the existing secret mechanism, then:

```sh
PYTHONPATH=services/recommendations python3 -m recommendations
```

Required: `RECOMMENDATIONS_SERVICE_TOKEN`. Optional: `RECOMMENDATIONS_DB`
(default `~/.local/share/hr-vision-recommendations/results.sqlite3`),
`RECOMMENDATIONS_PORT` (default `8099`). `RECOMMENDATIONS_HOST` may only be
`127.0.0.1`; public binding is rejected before storage opens. The DB and backups
are mode 0600, newly created parent directories 0700. A runtime file lock permits
one process per DB, including across different ports. Stop gracefully with SIGINT;
the service waits for active HTTP requests on close. SIGKILL restart is tested.

## HTTP

All routes except `GET /healthz` require one `Authorization: Bearer <token>` header.
The browser uses the authorized backend, never this token. Responses disable
caching and include a generated `X-Request-ID`. No bodies, headers, source text,
freeform provider errors or raw request paths enter service logs.

| Operation | Result |
|---|---|
| `POST /v1/recommendations` | 200, complete saved `recommendations-v1` result |
| `GET /v1/recommendations/{64hex}` | 200, exact immutable saved result; no calculation/write |
| `GET /healthz` | 200, process/service version only |
| `GET /readyz` | 200 when SQLite read/write transaction can be acquired; 503 on outage |
| `GET /v1/operator` | protected counts, version, rubric hash, limits, operation ID and timing counters |

POST body is exactly
`{vacancy_profile,candidate_profiles,candidate_fields,vacancy_fields,policy}`.
Profiles are direct completed results, not status envelopes. Candidate contract:
`full-corpus-level-choice-v1`; vacancy: `vacancy-level-importance-v1`;
importance: `importance-0-3-v1`. Vacancy raw importance Choices use
`I0,I1,I2,I3,insufficient_data`. A manual vacancy profile may include
`revision:{author,parent_profile_id}`, changes `profile_id` and retains raw answers.

Every numeric probability must be a genuine finite number in [0,1], with total
within `1e-5` of one (same rounding tolerance as producing JEV transport). Chosen
options must have maximal raw probability. Booleans never stand in for numbers.
Unknown fields, source text, duplicate JSON keys, duplicate candidate profiles or
fact rows, incomplete fact/profile sets and incompatible catalogues reject the
whole request before any durable mutation. An empty candidate fact list is a
confirmed empty set; null means unknown. A missing industry key is unknown,
not zero months. Canonical IDs are compared exactly without case folding or aliases.

Initial policy is exactly
`{version:"tanh-v1",k:1,probability_mode:"conditional_levels"}`.
Positions follow the catalogue 1..N. For an included skill,
`q=p/sum(numeric p)`, `u=tanh(k*(position-required_position))`,
`m=sum(q*u)`, `score=sum(importance*m)/sum(included importance)`.
Confidence is displayed separately. Raw probabilities are never rewritten.
Weight zero or chosen insufficient data excludes the skill from numerator and
denominator. All excluded means `score:null,status:unscored,rank:null`.
Unknown importance or positive weight with unknown requirement rejects with
`422 {error:"clarification_required"}` rather than returning a partial match.

Confirmed mandatory failure produces `eligibility:fail,status:filtered,rank:null`;
it remains in the saved audit and the consumer hides it from the ordinary list.
Unknown mandatory checks leave a visible row, `eligibility:unknown` and
`requires_human_approval:true`. Passing all mandatory checks produces `pass`.
Each check contains reason and both fact revisions. Scored visible rows sort by
score descending then candidate ID ascending; unscored rows follow; filtered
audit rows are last. Only visible scored rows receive consecutive ranks.

Every parameter reports raw p, derived q, curve u, each contribution, m, weight,
weighted contribution, exclusion reason, original unknown probability and
confidence. Coverage includes total/included/excluded counts and weight sums.
Pool membership and every source/profile/fact revision remain tied to the result.
Scores are indices in [-1,1], not probabilities of a successful hire.

ID = SHA-256 of canonical JSON `{service_version,input}`. JSON object keys sort;
input arrays preserve supplied order. Identical inputs reuse one saved row.
The full input and result are committed in one transaction before responding.
GET verifies input/result hashes and never recomputes or repairs a result. Invalid
saved data gives 503. Losing a response can be recovered by the same POST or ID.

Limits: 1..1000 candidates, at most 16 MiB input, eight concurrent connections,
15s request read timeout, SQLite lock timeout 1s. Each limit rejects explicitly;
none truncates the pool, profiles or descriptions. The backend must enumerate its
requested catalogue fully rather than treating a database page limit as total.

Errors are `{error:<stable_code>}`: 401 unauthorized; 404 not_found;
408 request_timeout; 413 request_too_large; 422 invalid_input,
invalid_content_length, invalid_content_type, incomplete_body,
incompatible_profiles, unsupported_policy, invalid_pool_size,
duplicate_candidate or clarification_required; 503 service_busy,
store_unavailable or service_unavailable. Correct the input/configuration or retry
an identical deterministic POST after availability recovers. There is no model
retry, queue, network dependency or automatic publication side effect.

## Backup, restore, restart and rollback

Use SQLite online backup while the process is running. Commands emit only counts
and status. Destinations must not already exist. From repository root:

```sh
PYTHONPATH=services/recommendations python3 -m recommendations backup /private/path/backup.sqlite3
PYTHONPATH=services/recommendations python3 -m recommendations restore /private/path/backup.sqlite3 /private/path/restored.sqlite3
```

Restore verifies SQLite integrity and every saved input/result identity. Stop the
service, change `RECOMMENDATIONS_DB` to the restored path, start the same release,
check readiness and GET a known recommendation ID. Restore never overwrites an
active DB. If startup reports `service_configuration_error`, inspect token shape,
loopback port, writable private storage and another owner's lock; secrets never
need to be printed. Search structured logs by returned request/operation ID.

The initial schema is append-only: no update/delete/migration endpoint exists.
Releasing a new scorer changes the service version and therefore new result IDs;
previous saved results remain readable using their stored version. Preserve the
previous artifact and DB backup before switching process/routing. A production
artifact rollback is the release owner's responsibility and remains unverified
here; local restart and restore do not prove a previous production release works.

## Verification

```sh
PYTHONPATH=services/recommendations python3 -m unittest discover -s services/recommendations/tests -v
```

See `ACCEPTANCE.md` for evidence and remaining product/release boundaries.
