# Candidate access from an HH interview

The personal HH booking link resolves to one prepared candidate and a preserved
full CV. The candidate chooses a recruiter slot, supplies their email and
explicitly agrees to preparation of their profile from that CV. The booking
transaction records `cp_booking_profile_consents` for the exact candidate and
interview; it does not create a login, send access mail, imply recording/reuse
consent, or enable automatic vacancy approval.

The assigned, active recruiter can press **Отправить доступ на почту** in the
existing interview details or call interface. `CandidateAccess` activates the
same `cp_candidates.id` using its standard scrypt password format and enqueues
one access email. The email contains the ordinary candidate login URL, email
login and generated password. CV, profile, preferences, booking and interview
remain attached to the same candidate. Standard candidate login opens that
existing appointment without a second CV upload or registration.

An account already active before this feature receives a sign-in reminder using
its existing email and password. This feature never resets its password or
merges candidates that share an email. Legacy manual interviews without the new
booking consent retain their existing profile-claim flow. Existing HH bookings
with a personal booking link can complete the email/consent step on that same
page without choosing a second slot or sending another booking notification.

## Integration

Construct `CandidateAccess(portal)` after `CandidatePortal` and `CandidateBooking`.
The API's existing origin, JSON, recruiter session and rate checks apply. Then:

- `POST /api/hr/recruiter/candidate-access/state`, JSON `{candidateId}` calls
  `state(db, candidate_id, principal=principal)`.
- `POST /api/hr/recruiter/candidate-access/send`, JSON `{candidateId, requestId}`
  calls `request(db, principal, candidate_id, body)` inside `BEGIN IMMEDIATE`.
- A server-side adapter can instead call
  `enqueue(db, candidate_id, interview_id, request_id, principal=principal)`.
- Call `start_worker()` only in the API server main entrypoint. It creates no
  requests; it only drains this feature's explicit outbox. `dispatch_once()` is
  available for isolated tests or an operator-owned worker.

Both read and write access require the **named recruiter owning this interview**;
a legacy team principal or another recruiter cannot read delivery status or send
credentials. The target email is fixed by booking consent, or by the canonical
existing account. The API does not accept an alternative email or a recruiter
checkbox as substitute consent.

The safe state contains `status`, `mode`, `recipientEmail`, `emailConfigured`,
`canSend`, `blockedReason`, `consentRequired:false`, `deliveryId`, `requestId`,
`interviewId`, `lastError`, `sentAt` and `deliveryMeaning:smtp_acceptance`.
There is no password, access token or email body in API responses.

## Delivery and idempotency

States: `not_requested → queued → sending → sent|failed|unknown`.

`sent` means that SMTP accepted the message, not inbox delivery or human reading.
The worker uses `candidate_notifications.Settings` and its TLS-only `Transport`.
`HR_NOTIFICATIONS_ENABLED=1`, `HR_SMTP_HOST`, `HR_SMTP_FROM` and the existing SMTP
security/authentication settings must be valid. With mail unconfigured, sending
returns 503 and does not create credentials or enqueue a deferred surprise email.
Sending access does not mark the candidate's email verified for other notices.

An explicit request ID is bound to recruiter, candidate and interview. Duplicate
requests and concurrent clicks converge on one outbox item. A fresh request ID
while queued, sending, sent or unknown does not send again. The credentials and
Message-ID remain stable across explicit retries of a known failure.

The worker commits `sending` before SMTP I/O. A timeout, unexpected transport
error or stale sending lease becomes `unknown`; neither the worker nor another
button click retries it. Even the shared transport's generic `smtp_rejected` is
treated as unknown because it does not identify whether SMTP DATA was already
accepted. Authentication failure, recipient refusal and TLS-configuration
rejection are known failures that a new explicit click can retry after repair.

The database stores only the normal password hash, an opaque issuance ID and a
private random derivation key. The access password is reproduced transiently in
memory for hashing or email delivery; no plaintext password/body is stored in
SQLite, logs, API responses or URLs. Protect the database and backups as secret
material. Existing claim links are revoked when credentials are first issued;
call links and interview media state remain unchanged.

## Local acceptance

`python3.12 -m unittest test_candidate_access test_candidate_access_flow` uses
temporary SQLite, synthetic candidates, fake SMTP and mocked TLS transport. It
does not send any real email. Coverage includes canonical booking-to-login
continuity, ownership, consent/CV prerequisites, address collision, unconfigured
SMTP, duplicate clicks, restarts, concurrent workers, known-failure retry and
quarantined uncertain delivery. Real SMTP configuration and a controlled inbox
receipt remain separate production acceptance checks.
