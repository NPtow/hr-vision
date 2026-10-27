# Candidate path

The service candidate entry (`hr-vision-product--candidate`) now opens personal registration and a persistent candidate cabinet. `legacy=1` keeps the earlier team scenario accessible. The manager story keeps its employer interface; `workspace=recruiter` opens the private recruiter workspace with the existing team grant. There are still exactly three service stories.

Candidate sequence: introduction → email/password account → mandatory CV upload → short review of extracted data and missing preferences → actual recruiter slot → 30-minute human interview → cabinet → vacancy decision → separate meeting-time confirmation. Registration saves the account immediately; returning to the same account resumes the unfinished step. No slots, profiles or vacancies are seeded in the personal path. The recruiter must add available times first. Recording uses the existing configured Daily service; candidate device access starts only on an explicit click.

The CV review lets the candidate correct a factual draft of “About me”, city, employment type and residence area. A precise home address is not required. Salary range and latest start date are requested when missing; historical compensation and an earliest available date are not silently treated as these preferences. The CV parser uses local, conservative extraction, not a matching model or an external AI service. Candidate edits do not overwrite the recruiter-only internal profile.

## CV storage and extraction

`POST /api/hr/candidate/resume` accepts `{filename, contentBase64}` from the owner’s candidate session. Supported files are PDF with a text layer, DOCX and TXT, up to 5 MiB decoded. The HTTP handler authenticates before accepting the separately limited 7 MiB JSON request. The regular JSON limit remains 32 KiB. Raw CV bytes and extracted text stay in private SQLite tables, outside the public release. State responses contain file metadata and the extracted profile fields, not the CV contents. Downloading the original requires the owning candidate session.

PDF extraction requires `pdftotext` (`poppler-utils` on Ubuntu) and runs in a bounded subprocess. DOCX ZIP/XML size limits reject oversized, encrypted and unsafe documents. Scanned or unreadable files produce an actionable upload error; this version does not claim OCR support. No CV is sent to an external parser. A new recruiter interview booking requires an uploaded CV and completed onboarding. Already booked, live and completed interviews remain accessible for existing accounts.

Vacancy auto-approval is OFF by default. The candidate may opt in. The first release only auto-approves when role, geography and supported work mode match exactly; uncertain salary or other unsupported criteria leave a manual decision. No vacancy approval confirms meeting time. Candidate-editable information and recruiter-only evaluation are separate records.

## Runtime

`cp_*` tables share the existing SQLite database outside releases. Personal sessions use a distinct Secure/HttpOnly `__Host-hr_candidate` cookie. Recruiter endpoints require the existing manager bearer and do not accept candidate sessions. API writes require JSON and same-origin browser requests. Deployment guards include both recruiter interviews and hiring-manager meetings.

The deploy receiver must allow `candidate_portal.py`, `candidate_notifications.py` and `candidate_resume.py` before publishing this release. Install `poppler-utils` without restarting unrelated services before deploying CV support. Never use a different database or reset the team scenario during deployment. The source receiver includes the runtime allowlist and live-call guard.

`HR_TRUST_CLIENT_IP=1` may only be enabled with the configured ingress: the DSA Caddy overwrites `X-HR-Client-IP` with `{remote_host}`, outreach accepts only its trusted ingress address or localhost, and the API listens on loopback. The API validates the single address and ignores this header unless explicitly configured and received from loopback. See [Caddy header behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers).

## Notifications

The API starts a bounded background worker only with `HR_NOTIFICATIONS_ENABLED=1`. Configure the existing private environment file; never put credentials into the repository:

- SMTP: `HR_SMTP_HOST`, `HR_SMTP_PORT=587`, `HR_SMTP_SECURITY=starttls`, `HR_SMTP_FROM`, `HR_SMTP_USER`, `HR_SMTP_PASSWORD`. TLS on port465 is also supported with `ssl`.
- Telegram: `HR_TELEGRAM_BOT_TOKEN`, `HR_TELEGRAM_BOT_USERNAME`, `HR_TELEGRAM_POLLING=1`. Use a dedicated bot without a competing poller or webhook. A webhook alternative requires its configured secret.
- Optional `HR_CANDIDATE_PUBLIC_URL`; default is the service candidate story, including its query parameters.

Email verification and Telegram linking bind to the authenticated candidate. Channels are independent and show unavailable until configured. Previously disabled events do not silently send after configuration. The first release has no self-service password recovery or appointment cancellation; those exceptions are handled by the team.

## Isolated local verification

Use Python with `hashlib.scrypt`, an isolated `HR_DATA_DIR`, a local-only `HR_TEAM_KEY`, and `HR_ORIGIN=http://localhost:6027`. Start API on8491 and Storybook with `HR_BUILD_TARGET=service HR_DEV_API=http://127.0.0.1:8491` on6027. Keep notifications disabled. Do not run shared-scenario reset scripts against the deployed service.

Run `python3 -m unittest discover` in `server`, the deployment receiver tests, `npm run typecheck` and the service build. For browser verification use two separate candidate/recruiter contexts. Fake browser media can verify real provider recording without accessing the operator's physical camera or microphone.

The matching kernel and the connection between the existing employer interface and personal candidate records remain separate integration work. The employer scope is limited to backend support for its existing interface; an ATS connection is not part of it. The recruiter workspace is not evidence that the employer connection is complete.

Baseline release `e6b5b5a`, verified on 11 October 2026 before the CV change: 102 API/notification tests, 16 deploy-receiver tests, typecheck, 10 transcript parser checks and the three-story service build passed. Browser checks covered registration at 390px, preference persistence with auto-approval disabled, real slot booking, two independent participants in a Daily call using synthetic browser media, and playback of the resulting 161-second recording. The candidate returned to the cabinet after completion, manually accepted a vacancy, and separately confirmed the proposed meeting; both parties saw the persisted confirmation after reload. Synthetic audio does not establish Russian transcription quality. Mail and Telegram delivery remain unverified until the dedicated channel configuration is supplied. CV-specific verification must be recorded separately for the new release.
