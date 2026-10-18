# Telegram employer Mini App and meeting reminders

Base: 7b20dccc (latest main at start). Worktree: /Users/NIKITA/.codex/worktrees/hh-outreach-release/HR Vision, branch codex/hh-outreach-release.

## User intent and approval boundary

The user explicitly requests employer shortlist notifications with a button opening the existing website inside Telegram, and reminders in BOTH email and Telegram (not fallback-only). They reject adding rescheduling and cancellation. Proposed reminder timings from the preceding conversation are 24 hours and 15 minutes.
A six-item meeting-notification catalogue was sent for confirmation. Items beyond already approved reminders and current existing booking behavior are pending that reply; do not invent approval. Employer shortlist/Mini App work is independently authorized.

## Global Constraints

- Use the existing single bot and existing SMTP transport. Never expose credentials, signed initData, chat IDs, login/session tokens, CV contents or personal invitation links in reports/logs/commits.
- Preserve separate candidate, booking-only, recruiter and employer identities. A public role button cannot grant access or silently associate identities by name/email.
- The Telegram booking scope does not become full candidate-account access. Never merge or reset accounts to implement this.
- No new reschedule/cancel features. Do not remove existing unrelated functionality.
- Keep existing candidate-review design, latest main changes, and MashaDasha soft-depth UI. Reuse the existing employer website, do not build a parallel fake UI.
- All authorization, visibility, vacancy/workspace ownership and notification eligibility checks remain server-side.
- Durable transactional outbox, bounded retry, explicit uncertainty handling, no silent replay of historical unbound/disabled events or real test spam.
- No real provider sends, getUpdates, production mutation or deployment by implementers. Tests use isolated temporary SQLite and controlled providers.
- Existing untracked preview/output files belong to other work; do not stage/remove them.
- New backend modules must be included in package-release.mjs and deploy/ci/receiver.py while preserving older runtime layouts/rollback. Controller installs receiver before CI release.
- Use Python /Users/NIKITA/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 and PYTHONDONTWRITEBYTECODE=1. Commit only task-owned files; do not push.
- Do not spawn subagents. Controller handles independent review and release.

## Task 1: Employer shortlist notifications and Telegram Mini App

Implement a complete, small employer path: authenticated employer connects Telegram once; bot acknowledges and provides a Mini App button; future newly available candidate selections produce a notification with a Mini App button leading to that vacancy's real shortlist; opening inside Telegram securely restores the linked employer's own session.

Architecture:
- Introduce focused employer_notifications.py for employer binding, durable delivery and shortlist events; if necessary put Telegram initData verification in a separate telegram_miniapp.py module. Reuse candidate_notifications.Transport instead of a second transport implementation.
- Add authenticated employer endpoints for channel status, creating one-use employer-scoped deep-link binding, and disconnecting this employer only. Non-team, active employer accounts only; internal shared team grant is not a public employer identity.
- Add a public same-origin bounded POST endpoint for Telegram Mini App session exchange. Validate raw initData HMAC per official Telegram docs, constant-time signature, auth_date not older than 5 minutes and not materially in future, unique fields, bounded input, integer private user ID, existing active employer binding. No trust in initDataUnsafe, query account IDs or email. Preserve the application's existing authentication/session-cookie protections and use its normal employer session issuance.
- Handle the employer deep-link namespace in the shared dispatcher and send explicit success/expired/wrong-link responses without consuming unrelated role handlers. A simple /start or employer role card may expose public Mini App login; it must not grant private account access.
- Support web_app inline buttons through the existing validated transport. Restrict app URL to the configured HTTPS product origin; reject arbitrary app URLs and malformed mixed-button shapes. Preserve ordinary URL and callback buttons.
- Add a compact Telegram action/status in the real employer account menu. Use existing MashaDasha UI primitives; no large new settings panel.
- Bootstrap the official Telegram WebApp SDK only for this entry path; handle ready/expand/safe viewport, loading/error/expired/unlinked login states. Ordinary browser employer login still works. In Telegram, validated linked sessions open the existing ConnectedJourney employer UI on the requested owned vacancy/shortlist; unlinked users can sign in and connect explicitly. Never persist credentials/initData in URL or logs.
- Mini App links are public navigation only, not bearer links. Server verifies vacancy/workspace ownership regardless of URL. Use existing employer-entry/query navigation instead of building a parallel shortlist page. If a requested vacancy is unavailable, show a clear recoverable state.
- Build shortlist notices from canonical employer-visible candidates only. Unapproved/unconsented profiles must not be disclosed or counted as ready. Aggregate newly available candidates per vacancy into one meaningful notice per worker cycle/publication, deduplicate per availability/application, and revalidate before dispatch. Binding existing accounts should show current shortlist via welcome button, not automatically replay historical events.
- Cover both newly published batches and candidates becoming visible after subsequent approval. A replayed import/worker cycle must not duplicate notifications; no hardcoded example candidates.
- Text stays concise, e.g. “Новые кандидаты по вакансии «…»” + count and “Посмотреть подборку”. Do not put CV/contacts in the Telegram body.
- Write focused regression tests for authenticated binding, one-use expiry/rebind/disconnect, cross-workspace denial, forged/stale/future/duplicate initData rejection, valid miniapp login, exact safe web_app payload through real Transport mocked only at network, shortlist visibility/dedup/stale queue and ordinary browser fallback.
- Add frontend behavior tests where material, typecheck and build; leave full production/native client verification to controller. Update relevant docs and deployment allowlists.

Official reference: https://core.telegram.org/bots/webapps (Inline Button Mini Apps, Validating data received via the Mini App), https://core.telegram.org/bots/api#inlinekeyboardbutton. No third-party new service/library is required.

Task report must identify new API shapes/module names, tests/commands/results, packaging prerequisites and any unverified native Telegram behavior.

## Task 2: Reminders in both channels for actual participants

Implement after Task 1 review is clean. Approved scope: reminders for confirmed primary recruiter interviews and confirmed employer meetings, at 24 hours and 15 minutes, sent independently to EMAIL and TELEGRAM when each channel is eligible. No fallback-exclusive delivery.

- Add a focused scheduler/module integrated into the existing single notification worker, not a second Telegram poller. Reuse role-specific outboxes/transports.
- Determine participants from canonical current records: primary interview candidate + assigned recruiter, employer meeting candidate + employer workspace/account. Never notify an arbitrary account supplied by the client.
- Booking-only candidates use their saved interview consent/email and separate booking Telegram binding; full candidate-account verification/access rules remain unchanged. Do not silently mark email verified or grant account access. If a channel is not connected/eligible, expose that state rather than silently promising delivery.
- Idempotency includes meeting/interview identity, current time/revision, offset, participant/role and channel. Revalidate current future confirmed status, ownership, binding and start/end before sending.
- Only create due reminders in a bounded catch-up window (10 minutes) and before the meeting starts. Do not send a stale 24h reminder together with a 15m reminder after downtime. Meetings booked after a reminder threshold should not receive a stale reminder for that threshold. Do not replay old unrelated notifications.
- Email and Telegram both carry date/time in Moscow and a role-appropriate safe action link/button. Booking-only candidate gets exact own invite URL; recruiter gets their cabinet/meeting context; employer gets the Mini App meeting context from Task 1. No credential issue/reset and no CV disclosure.
- Candidate /meetings should include primary interview for authenticated candidates and limited “Моя запись” for booking-only chats as needed to make reminder links recoverable; keep access scopes strict. Recruiter receipt and safe meeting link acknowledgement may be improved as required for reminder entry. Do not implement an unrelated all-role redesign.
- Add focused due/not-due, both-channels, exactly-once across retries/restarts, late creation, catch-up, ended/cancelled/changed record, wrong recipient/rebind, unbound and booking-only isolation tests. Never use wall-clock sleeps or real provider calls.
- Additional event types from the pending catalogue must NOT be enabled unless controller explicitly supplies the user's confirmation. Report what remains pending.
- Update packaging/receiver if a module is added, preserving prior complete layouts.

## Task 3: Controller integration, review and release

- Whole-branch review of Tasks 1/2; resolve important findings.
- Verify a local Mini App entry with a controlled signed initData fixture using isolated state and ordinary browser fallback, desktop/mobile, true shortlist visibility and no horizontal overflow. Never publish synthetic candidates into production.
- Run required repo checks and release through existing GitHub CI, preserving concurrent main changes.
- Install a reviewed receiver allowlist update if necessary, with backup and its tests; do not manually rsync the application.
- Verify public revision, actual runtime module hashes/config and migration shape without sending historical messages. Native Telegram rendering is only claimed if actually observed.
- Deliver the concrete notification catalogue and explain approved/deployed versus still-awaiting-confirmation items. Keep evidence under output/telegram-employer-miniapp-20261019.

