# Vacancy profile service

Independent localhost process with private SQLite and one durable worker.
Owner: Ядро. Authorized HR backend supplies a complete vacancy snapshot and
versioned rubric. The service sends exactly two Choice questions per parameter
(level and importance) with the same full vacancy state, saves the provider
response before interpretation, and owns immutable profiles/manual revisions.
It receives no CV, interviews or candidate profile. It does not rank candidates,
check structured mandatory facts, send invitations or access the HR database.

The runtime uses Python 3.10+ standard library and the frozen `jev_client`
package. The shared library is packaged into each service release; it is not a
runtime dependency on the candidate service. Local checkout command:

```sh
PYTHONPATH=services/vacancy-profile:packages/jev-client python3 -m vacancy_profile.api
```

Required private environment: `VACANCY_PROFILE_SERVICE_TOKEN` (64 hex digits),
`VACANCY_PROFILE_JEV_API_KEY`. Optional model is pinned `jev-1.14.0`, port 8108,
DB defaults to `~/.local/share/hr-vision-vacancy-profile/runs.sqlite3`, and
`VACANCY_PROFILE_JEV_PROXY` accepts only an explicit existing loopback socks5h
proxy. Provider deadline 120s, single POST with no retries. Service binds 127.0.0.1.

Inputs larger than 4 MiB are rejected entirely; no field/text truncation.
A 100-job durable pending queue and 16 HTTP handlers bound admission. HTTP body
read timeout 15s. Identical complete input (including revision, rubric, prompts
and model) has the same run_id and never silently re-bills. Revision is an opaque
owner-supplied snapshot revision; vacancy_sha256 independently hashes complete
`vacancy.data`. JSON duplicate keys and nonfinite values are rejected.

Importance version `importance-0-3-v1` defines auxiliary 1, substantial 2,
critical 3 and explicit irrelevant 0. Model option IDs are I0,I1,I2,I3 and
insufficient_data. Unknown importance stays insufficient_data; positive weight
with unknown requirement gives needs_review. Explicit0 gives irrelevant.
No text mentioning a skill does not establish weight 0. Profile levels retain
the exact catalog order, IDs, labels and descriptions; raw Choice distributions
are never rewritten. Manual corrections preserve both original Choice answers.

Contract: [API.md](API.md). Operations and artifact build:
[deploy/README.md](deploy/README.md). Shared passport and acceptance matrix:
`packages/assessment-contracts/{README,ACCEPTANCE}.md`.

Offline verification (no paid/provider calls):

```sh
PYTHONPATH=services/vacancy-profile:packages/jev-client python3 -m unittest discover -s services/vacancy-profile/tests -v
```

Tests cover the complete 16-parameter/80-anchor catalog, HTTP result and manual
revision flow, negative inputs/access, queue backpressure, timeout/rejection,
immutable identity/replay, interrupted/saved/queued recovery, separate-process
restart and SQLite backup/restore. Model responses in these tests are fixtures.
Production, real JEV quality/latency, HR end-to-end and target rollback remain
UNVERIFIED until their specific evidence is recorded in the shared matrix.
