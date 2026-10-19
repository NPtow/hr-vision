# Complete the approved six meeting notification types

Base: 8746feccbd7ab8e137448175ee432d4eeccd3352. Same isolated worktree and codex/hh-outreach-release branch. The preceding Mini App/reminder release is independently being verified by the controller.

## User specification

The user explicitly answered “Все 6 пунктов” to:
1. Primary interview booked: candidate and assigned recruiter.
2. Employer proposes a meeting time: candidate, with a link to confirm.
3. Candidate confirms the meeting: employer and candidate.
4. 24 hours before a confirmed meeting: both participants, Telegram and email.
5. 15 minutes before: both participants, Telegram and email, meeting button.
6. After the employer meeting is completed: candidate and employer, request feedback.
No new rescheduling/cancellation features. Shortlist Mini App is already implemented. Reminders #4/#5 are already implemented; verify compatibility and do not duplicate them.

## Global constraints

- Existing single bot, SMTP transport, role outboxes and worker. No additional polling, provider/client service or cron.
- Candidate, booking-only, recruiter and personal employer identities remain separate. No new account/session/password issuance or silent email verification. Recipients come from canonical meeting ownership; no client-selected account IDs.
- Use both eligible channels independently, consistent with existing operational delivery. Missing/unverified channels remain explicit terminal states, never promised as delivered.
- Existing notifications for #1/#2/#3 must be reused/completed without duplicate sends or broad unrelated rewrites. No retrospective mass-send after deployment or later linking. New events are generated transactionally on the canonical transition, deduplicated and revalidated at claim.
- Revalidate current meeting status/time/revision, candidate/application/workspace ownership, active account, destination and binding. Suppress stale invitation/confirmation after completion or replacement, and feedback after submission/revocation. Preserve durable bounded retries and uncertainty quarantine.
- Messages use actual Moscow date/time and concise human labels. Telegram uses a button, never a raw long link. Employer opens the existing signed Mini App at the owned meeting/feedback context; email opens ordinary authenticated website. Candidate links open the relevant existing meeting/feedback action, not an unrelated home screen. Booking-only primary confirmation keeps its exact personal link and limited capability.
- Feedback request follows actual canonical completed employer meeting, not merely the clock passing the scheduled end. Reuse current candidate post-meeting response and employer feedback UX, without inventing a parallel questionnaire or claiming completion from time alone.
- No new reschedule/cancel notifications or controls, no live test messages/provider calls/getUpdates, no production changes/push/deployment by implementer. Tests use isolated state and controlled providers.
- Preserve latest selected-candidate UI and unrelated untracked files. If adding backend modules, update package-release and receiver complete-layout compatibility; controller installs reviewed receiver before release.
- No subagents from implementer; controller owns reviews and deployment. Commit task-owned files only. Use PYTHONDONTWRITEBYTECODE=1 and bundled Python runtime.

## Task 1: Complete lifecycle notifications and regression coverage

Audit the existing canonical hooks against all six types and implement the missing recipient/event/action paths. Current starting points: CandidatePortal.event/confirm_meeting/complete; existing candidate notification EVENTS and booking-confirmation path; recruiter_notifications.enqueue_booking; employer_notifications binding/outbox/Mini App; meeting_reminders for #4/#5.

- Build a concise implementation matrix mapping each type to hook, recipients/channels and action target; this is part of the report and product documentation.
- Preserve existing primary candidate and recruiter booking notices and employer proposal candidate notices, adding useful actual meeting context/buttons where missing without duplicate events.
- Add employer-side confirmation and both sides' completed-meeting feedback request through durable transactional hooks. Wire existing candidate delivery/action paths so #2/#3/#6 open the right current meeting/application state. Read current completion and feedback APIs before choosing links.
- New lifecycle events must not be backfilled/replayed for historical meetings at deploy. Queue atomically, independent email/TG eligibility, revalidate stale records/bindings and feedback already submitted. Ensure repeated transition callbacks/HTTP retries do not duplicate messages.
- Cover all six in meaningful isolated tests: canonical create/propose/confirm/complete, intended two participants, each eligible channel, same-transaction rollback, repeat transition exactly once, wrong recipient/workspace, stale/changed/ended/superseded records, disconnected/rebound channels, candidate verification and booking-only isolation, feedback already supplied, and existing reminders unchanged. Test the actual shared worker/transport boundary with controlled providers rather than merely renderer snapshots.
- Update relevant docs/packaging; typecheck/build if frontend navigation is touched. Focus checks on changed behavior; leave full CI and actual native/provider receipt to controller.

Report to task-1-report.md in this plan's scratch folder with status, exact commands/results, API/event/module changes, notification matrix, migration/receiver prerequisites and verification boundaries. No real production or provider sends.

## Task 2: Controller review and release

Task-scoped spec/quality review, resolve findings, final whole-change review against this newly approved scope, focused browser navigation verification if UI touched, appropriate required checks and CI release. Finish verifying the preceding immutable release first; do not push a newer main SHA while its receiver is still publishing. Install updated receiver if module set changed, preserving backup. Verify public/runtime revision, schema/config and no historical replay; distinguish deployed code from actual recipient receipt. Report all six types and final status to user.
