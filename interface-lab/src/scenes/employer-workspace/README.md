# Employer workspace

The working service uses the existing shared API, not the isolated candidate-panel fixtures.

- Candidate panel: existing screening player, full/short recording, chapter seeking, source excerpts, experience and terms. DSA materials retain their original source and read-only behavior.
- Intake: manual profiles, HTTPS resume links, or up to 10 PDF/DOC/DOCX/RTF files (5 MB each, 8 MB batch). Candidate records and attachments are private SQLite data on outreach, not committed assets. Links are saved, not scraped. No candidate consent, interview, availability, or account is fabricated. Archive can be reversed in the Added/Archive list.
- Scheduling: server-provided slots, Moscow calendar, 30-minute meetings. Existing pending/confirmed invitations can be rescheduled; candidate confirmation is required again. Concurrent stale forms cannot overwrite another tab's new invitation. Other candidates' meetings remain unchanged.
- Existing calls, post-call feedback, chat and offer flow remain connected.

Entry: `/iframe.html?id=hr-vision-product--manager&viewMode=story&screen=panel`. Existing browser access is required. The sidebar returns to hiring tasks and the DSA panel.

## Deployment

Push to main uses the existing service workflow. `server/app.py` creates intake tables without changing the shared hiring scenario. SQLite backups taken by the receiver include resume blobs.

The dedicated outreach Caddy configuration allows 12 MB only at `/api/hr/intake` for base64 transport; other API paths stay at 32 KB. Install this config administratively on outreach when provisioning a new host; CI intentionally does not change infrastructure configuration.

## Checks

TypeScript, Storybook build, transcript checks; API tests execute with an isolated temporary SQLite DB (never reset the shared scenario for these tests). Intake tests cover manager authorization, file-byte preservation, rollback of invalid batches, separate hiring tasks, idempotency and reversible archive. Calendar tests cover pending state, manual confirmation, collisions, multiple meetings and stale reschedule requests.
