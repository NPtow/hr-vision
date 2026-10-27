# Existing employer CV intake

`employer_intake.py` preserves the current `CandidateIntake` / `IntakeDetail`
contract. It stores employer-owned drafts, not candidate accounts. Uploading a CV
does not grant consent, complete an interview, or create a review-queue entry.
The draft ID is not an application ID. No `cp_*` person/application is synthesized.

The schema is `em_intake_batches`, `em_intake_candidates`, `em_intake_files`.
Both workspace and canonical vacancy own each draft. Existing legacy global
`intake_*` rows are not adopted into any workspace.

## HTTP adapter

Initialize after `employer_store.init`.

1. Authenticate employer and derive `workspaceId` server-side. Validate the
   requested vacancy via `employer_store.vacancy` before processing a file.
2. For POST, call `existing(db, workspace_id, vacancy_id, body)` for a cheap retry.
   A cached response avoids parsing the same CV again.
3. `prepare(body)` validates sizes/signatures and parses supported CVs. Call it
   **outside the global HTTP/SQLite lock**; PDF extraction starts the existing
   bounded local subprocess and may take time.
4. Revalidate the session, then `add(db, workspace_id, vacancy_id, body,
   prepared=prepared)` inside the caller's transaction. Ownership, closure,
   idempotency and quotas are rechecked after the write lock is acquired.

Methods:

- `list_candidates(db, workspace_id, vacancy_id)` → `{candidates: IntakePerson[]}`.
- `add(...)` → the same wrapper with only the submitted batch, in input order.
- `archive(db, workspace_id, vacancy_id, {id, archived, vacancyId?})` → `{candidate}`.
- `file(db, workspace_id, file_id)` → `(bytes, headers)` for the binary HTTP path.
  The headers include attachment disposition, `nosniff`, byte length, and
  `Cache-Control: no-store`. Filename alone or knowledge of a file ID grants no access.

POST body is the existing `{batch, scope, entries, vacancyId?}`. `scope` remains
the old presentation value (`sfera`/`dsa`); it does not establish authorization.
The HTTP adapter supplies and checks the real vacancy separately.

Each entry accepts `name`, `role`, `contact`, `link`, `note`, and optional
`file: {name, data}` where `data` is strict base64. DTO is unchanged:
`{id,name,role,contact,link,note,scope,files:[{id,name,size}],createdAt,archived}`.
Parsed fields/text remain private in the file record. Only a missing role may be
filled from a labelled desired-role field; the user's name/edit is preserved.

## Limits and honest extraction status

- 1–10 entries; 5 MiB per attachment, 8 MiB per batch.
- Maximum 500 drafts per workspace; 128 MiB total storage in this module.
- PDF/DOCX use `candidate_resume.parse_upload` with its file complexity, worker
  time/memory, and readable-text limits. Invalid or unreadable files return its
  explicit error and no partial batch is retained.
- DOC/RTF remain accepted attachments as in the existing screen; extraction is
  marked `unsupported`, text/fields stay empty. There is no pretend conversion.
- HTTPS resume links are validated and stored, never downloaded or scraped.
- Repeated same-key requests reuse draft/file IDs. Same-key changed payloads
  return 409. Batches with any failure roll back fully, even if the caller catches
  the exception. Archiving is reversible and does not delete files.

Recruiter conversion/onboarding of an employer draft is a separate explicit step;
this module does not connect to HH, issue invitations, or add a nonconsenting
person to the employer's review queue.

`python3 -m unittest test_employer_intake -v` covers the unchanged DTO, real DOCX
extraction, private download, spoofed IDs, malformed/oversized files, idempotent
concurrent upload, outer rollback, caught mid-write failure and persistence.
