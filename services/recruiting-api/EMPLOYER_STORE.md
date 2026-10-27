# Employer vacancy and review storage

`employer_store.py` is a persistence adapter over the existing `cp_candidates`,
`cp_vacancies`, and `cp_applications`. It does not perform matching, copy candidate
profiles, or replace the candidate's vacancy approval.

Initialize after `CandidatePortal.init(db)` and `employer_access.init(db)`. All
methods take a trusted `workspace_id` derived from employer authentication. The
HTTP adapter must never accept it as authority from request JSON. Mutations use
savepoints, acquire SQLite's write lock when starting a transaction, and leave
commit/rollback to the caller. Do not run schema migration inside an action.

## Adapter contract

- `create_vacancy(db, workspace_id, body, request_id=None)` returns the canonical
  vacancy and stable `id`, `generation`, `revision`. Accepted input fields:
  `title`/`role`, `company`, `city`, `remote`, `description`, `problem`, `salary`,
  `format`, `expectations`. Conflicting title/role values are rejected.
- `update_vacancy(db, workspace_id, vacancy_id, body, expected_revision=None,
  request_id=None)` merges only those editable fields. Workflow-owned `status`,
  `closedBy`, and `closedByApplication` survive edits.
- `vacancy`, `list_vacancies`, and `archive_vacancy` enforce ownership. Archival
  preserves data and requires no pending/confirmed/live meetings.
- `ensure_open` rejects closed/archived vacancies before new meetings or offers.
- `bump_revision` increments the vacancy's UI revision after workflow mutations.
- `application(db, workspace_id, application_id)` returns IDs, canonical profile
  fields, approval status, review status/order, and recommendation provenance.
  It omits account credentials, contact email, and unknown profile properties.
- `applications(db, workspace_id, view='all', vacancy_id=None)` and
  `review_candidates(db, workspace_id, vacancy_id, view='proposed')` show only
  accepted applications with current `consentReuse`, never rejected ones.
- `skip(..., request_id=None, expected_revision=None)` moves a proposed candidate
  to the end. Supply one UUID per user action and reuse it for transport retries.
- `reject(...)` persists rejection for that participation. The candidate may
  independently remain active in another vacancy. Root handles cancellation of
  future meetings in the same transaction; this module never stops live media.
- `mark_selected(db, workspace_id, application_id, meeting_id)` verifies the exact
  persisted meeting and moves the review into the selected pool. Call inside the
  same transaction as successful meeting creation. Opening or cancelling an
  unfinished scheduler must not call it.

`application` has `include_rejected` and `include_unapproved` flags only for trusted
internal reconciliation. Never expose those switches to employer HTTP input.

`adopt_existing(db, 'hr-vision-team')` explicitly adopts only unowned old recruiter
vacancies and their applications. Existing ownership in another employer's
workspace is preserved. Imported provenance is `recruiter`, version
`existing-recruiter-v1`, with no invented explanation. An already existing meeting
means the candidate is already selected. Other workspaces cannot use adoption.
For those internal-team vacancies only, old meetings owned by `recruiter:team`
are assigned to `workspace:hr-vision-team`; IDs, status, revisions and media stay
unchanged. Meetings belonging to any other workspace are never reassigned.

## Matching boundary

`import_batch(db, workspace_id, vacancy_id, body, portal=CandidatePortal)` accepts:

```json
{
  "source": "core",
  "version": "producer-version",
  "externalBatchId": "unique-producer-batch",
  "candidates": [
    {"candidateId": "existing-cp-id", "rank": 0, "reason": "Producer explanation"}
  ]
}
```

This must be a separately authorized internal integration route; ordinary
employers cannot submit arbitrary person IDs. Sources are `core` or `recruiter`.
An explicitly empty result is valid. Candidate IDs and ranks must be unique,
rank is a nonnegative integer, and nonempty reasons are retained verbatim.
At most 100 entries are accepted, not a promise of a 100-person matching batch.

Before any writes, the entire batch requires completed recruiter interviews and
current reuse consent. The existing candidate portal creates vacancy proposals
and its transactional notification event. Manual candidate approval remains
required unless the candidate's existing explicit auto-approval preferences
qualify. No meeting is automatically confirmed.

Producer source + external batch ID is idempotent within a workspace and vacancy.
Same-key changes return 409. New batches may refer to existing applications but
cannot resurface rejected candidates, move selected ones back to the queue, or
overwrite their original explanation. Batch receipts and original provenance
remain inspectable in `em_batches` / `em_batch_items`.

Verification: `python3 -m unittest test_employer_store -v` uses temporary SQLite,
synthetic profiles, and a fake outbox; no external matching or live data. It covers
restart, outer rollback, caught mid-batch failure, concurrent retry, consent
withdrawal, queue cycling, rejected/selected preservation and workspace isolation.
HTTP integration and production deployment are separate root-agent checks.
