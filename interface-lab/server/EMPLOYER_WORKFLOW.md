# Employer workflow integration contract

`employer_workflow.py` adds outcomes to canonical `cp_applications`, `cp_candidates`,
`cp_vacancies`, and `cp_meetings`. It does not create replacement people, CVs,
meetings, recordings, matching rules, UI, or outbound network requests.

## Entry points and authorization

- `init(db)`: schema initialization after CandidatePortal tables/migrations.
- `identity(db, application_id, candidate_id=None, vacancy_id=None)`: validates
  canonical application identity. Candidate mismatch returns 404.
- `load(db, application_id, actor='manager', candidate_id=None, vacancy_id=None)`:
  UI-compatible overlay. Candidate actor requires the authenticated candidate ID
  and excludes questionnaires, feedback, drafts, and unsent offers.
- `manager_action(db, application_id, action, body, manager_id=..., vacancy_id=None)`:
  **caller must first authenticate a real employer and check workspace ownership**.
  The manager ID must come from the session, never from submitted JSON.
- `candidate_action(db, authenticated_candidate_id, application_id, action, body)`:
  uses actual candidate-cookie identity; checks ownership independently and rejects
  manager-only actions.
- `reject(db, application_id, manager_id=..., vacancy_id=None, request_id=None)`:
  explicit initial-review rejection without a feedback prerequisite. The adapter
  first cancels pending/confirmed meetings, then calls this helper and
  `employer_store.reject` in one transaction. A live meeting or accepted offer
  blocks rejection. Any existing sent/draft offer becomes `withdrawn`, retaining
  immutable terms; it is not mislabelled as a candidate refusal.
- `prepare_questions(db, application_id, meeting_id, interviewer_turns,
  vacancy_id=None)`: call after fetching the actual employer's transcript outside
  the transaction. Uses existing conservative `feedback.questionnaire`; no generic
  or model-generated substitute questions. First nonempty questionnaire is frozen.
- `meeting_feedback(db, application_id, meeting_id, vacancy_id=None)`: private
  manager-only exact meeting history overlay. Never include it in candidate/guest
  responses.

Workspace checks, canonical meeting authorization, vacancy generation/revision
checks, and queue visibility are integration responsibilities. When `decision`
becomes `declined`, root invokes `employer_store.reject` in the same transaction.
The workflow does not delete a person or alter their other applications.
If `em_reviews` already says `rejected`, the candidate projection shows a declined
decision and all further candidate actions are blocked even without a workflow row.

## Actions

All mutation bodies optionally accept `requestId` (1–120 chars) and
`expectedRevision` (current workflow revision, integer). A reused requestId with a
different payload returns 409; same actor/application/key/payload returns current
state with no repeated event or message. Use requestId for chat transport retries.

| Actor | Action | Fields |
| --- | --- | --- |
| Manager | `feedback.questions` | `meetingId`, `questions` (actual questions, one per line) |
| Manager | `feedback.draft` | `meetingId`, `questionnaireId`, `answers` |
| Manager | `feedback` / `feedback.submit` | Same, with every question answered |
| Manager | `decision` | `value`: `pool` or `declined` |
| Manager | `reject` | Explicit early refusal; see helper above |
| Manager | `offer.save` / `offer.draft` | `role`, `compensation`, `format`, `expectations`, `startDate`; `version` required when editing |
| Manager | `offer.send` | Current `version` required |
| Candidate | `after` / `after.interest` | Exact latest completed `meetingId`, `value`: `yes` or `no` |
| Candidate | `offer.reply` | Current `version`, `value`: `accepted` or `declined` |
| Both | `chat` | `text`; `requestId` recommended |

Each answer is `{rating: 'clear'|'unclear'|'unanswered'|'not_asked', comment?: string}`.
Submitted feedback is immutable and attached to the exact meeting + questionnaire.
Identical submission/send/reply/draft/decision retries are semantic no-ops.

Offer terms are immutable versions. Editing a sent offer supersedes the old
version and keeps the new one sent; stale replies/edits return 409. SQLite partial
unique index allows only one sent/accepted offer per vacancy, across processes.
After decline another candidate's draft can be sent. Acceptance closes exactly
that vacancy via `cp_vacancy_outcomes`, merging only `status`, `closedBy`, and
`closedByApplication` into its canonical JSON; all other vacancy fields survive.

## Responses, transaction, and events

Actions return `{state, events, replayed}`. State includes canonical IDs,
`revision`, `decision`, `afterInterest`, `closedBy`, current meeting overlay,
feedback (manager only), current offer, and up to 500 latest messages. Full message
and offer histories remain stored. Current meeting is latest inserted canonical
meeting; explicit feedback actions may address an older completed meeting, without
silently switching to the latest one. Feedback from an older meeting does not
satisfy the gate for a newer meeting. After-interest for an older meeting displays
as pending when a newer meeting exists.

Every mutation begins `BEGIN IMMEDIATE` if needed and always uses a savepoint.
It never commits the connection. The caller enqueues events and commits all
canonical/queue/workflow changes atomically; failures roll back the command's
savepoint. No provider/network calls belong inside this transaction.

Events: `{candidateId, applicationId, vacancyId, type, entityId, eventKey, audience}`.
`eventKey` is stable for an actual event, so an outbox can deduplicate it.

| Type | Audience |
| --- | --- |
| `offer.sent`, `offer.updated`, `offer.withdrawn`, `employer.declined` | `candidate` |
| `offer.accepted`, `offer.declined`, `candidate.after` | `employer` |
| `chat.message` | Other party |

The caller must route audiences separately. Do not send candidate-response notices
back to the same candidate through a candidate-only notification service. All
channel configuration and external delivery remain outside this module.

## Verification boundary

`python -m unittest -v test_employer_workflow` covers local SQLite isolation,
feedback privacy, exact meeting/versions, unchanged identities, terms validation,
ownership, atomic rollback, same/different-vacancy behavior, optimistic revision,
and independent-connection races for send/edit/reply/chat. These unit checks are
not proof of deployed HTTP authorization, browser wiring, or live notification
delivery; those are root integration acceptance checks.
