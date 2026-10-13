# Employer backend integration — 12 October 2026

User scope: finish the existing employer cabinet backend, preserve the interface,
work in parallel and track the implementation in Veritas. No dependency on
Alexey's matching core may be simulated or presented as delivered.

## Sources of truth

- `cp_candidates`: one candidate identity, uploaded CV and candidate preferences.
- `cp_vacancies`: vacancy; `em_vacancies`: its single employer workspace owner.
- `cp_applications`: candidate participation and approval for one vacancy.
- `em_reviews`: ordered review queue, selection and employer rejection.
- `cp_meetings`: exact appointment IDs, retained history and active constraints.
- `cp_employer_workflows`, offer versions, feedback and messages: persisted actions.
- Existing Storybook screen markup stays; an employer DTO supplies real data.
  Explicit `legacy=1` retains the separate previous demonstration scenario.

## Implementation owners

| Area | Owner | Veritas |
| --- | --- | --- |
| Employer sessions and HTTP orchestration | employer_access_backend | task_20261011_MlxwKv |
| Vacancies, batches, queue, intake drafts | employer_vacancy_backend | task_20261011_KcSjlV |
| Feedback, decisions, offer versions, chat | employer_outcomes_backend | task_20261011_HMK-EB |
| Candidate meeting/history bridge | employer_candidate_bridge | task_20261011_HMK-EB |
| Existing screen data and action bindings | employer_interface_wiring | task_20261011_uW3VQ7 |
| Independent HTTP acceptance and isolation | employer_acceptance_review | task_20261011_Q-3hNy |
| Projection, HTTP adapter, notifications and release | root | task_20261011_mVOXWm |

## Checks required before release

1. Empty employer accounts contain no demonstration people or vacancies.
2. Foreign workspace/application/meeting IDs fail before provider I/O.
3. Approved, consenting candidate identity persists through proposal, confirmation,
   meeting, feedback, offer reply and correct-vacancy closure.
4. A successful meeting proposal removes the candidate from the review queue and
   opens that same candidate in the vacancy pool. Failed/cancelled scheduling does
   not select a candidate.
5. Repeat commands are idempotent; simultaneous bookings and offers cannot double
   allocate a calendar or close another vacancy.
6. Rescheduling retains exact historical IDs/materials; an old link cannot confirm
   the new time. Past meetings leave the global queue and remain in the profile.
7. Rejection ends active proposals/offers without deleting another employer's data.
8. Provider failure leaves a retryable state. Revoked authority during I/O cannot
   return a new room token or material link.
9. Existing candidate/recruiter tests, types, production build, deploy receiver and
   public revision/health verification pass.

## Explicit boundaries

- The matching core is absent. The batch contract accepts producer-supplied IDs,
  order and explanations; it does not generate rankings or substitute demo data.
- CV intake creates employer-owned drafts; it does not fabricate candidate consent,
  a completed primary interview or an accepted participation.
- No new employer registration, vacancy creation or candidate offer screens are
  designed in this backend task. Corresponding authenticated APIs are available;
  existing controls alone are connected.
- Notification events are persisted. Real email/Telegram delivery is not enabled
  or claimed by local tests; employer notices currently persist without a provider.
- Real Daily lifecycle was verified in the previous release. This release's new
  ownership and lifecycle tests use an isolated database and a fake provider.
- Read-only production baseline at start: 1 candidate, 0 vacancies/applications/
  meetings/interviews and 0 live calls. No synthetic test profiles will be inserted
  into the production database.

13 October local acceptance: 384 server tests, 19 receiver tests, 10 transcript
checks, TypeScript and production build pass. A real browser against the local
SQLite-backed HTTP adapter verifies cookie entry, selection/navigation, reload,
queue removal, cancellation and the existing mobile layout. See the acceptance
report for boundaries and artifacts. Public revision is still checked separately.
