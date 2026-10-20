# Recruiter interview workspace

The cabinet card contains name/time, direct candidate contacts, résumé, script
readiness and one action: **Открыть интервью**. It opens a full-screen reading
workspace with the candidate's approved questions. Reading, changing the selected
question and opening the résumé do not join a room, request devices, mark answers
or change interview status. Joining Daily is a separate action inside, using the
existing time window. A missing script does not prevent the call.

There are no script creation/import/approval controls, backup-link editors,
manual candidate-access controls, duplicated profile sections or separate
recruiter-note forms in the card. Historical profile notes remain stored.

## Preparing scripts in chat

1. A trusted operator exports the real candidate context with
   `scripts/recruiter-script.py --db ... context --candidate ... --interview ... --output ...`.
   Personal data is written to a new private file, never a public asset.
2. The agent prepares questions from the agreed system prompt and actual résumé,
   discusses them with the human in chat, and imports a draft using
   `... draft --file ...`. Expected revisions prevent stale overwrites.
3. Only after the human approves the exact revision in chat, run
   `... approve --candidate ... --interview ... --revision N --approved-by ...`.
   This is separate from draft import. It preserves approver identity and rejects
   a superseded draft, a completed interview or a pinned script.
4. The cabinet displays “Скрипт подготовлен” once an approved script exists, and
   otherwise “Скрипт не подготовлен”. It refreshes without page reload.

On recruiter join, the backend pins the approved revision. A later draft cannot
replace the script under an ongoing interview. All API operations enforce the
candidate/interview identity and assigned recruiter or existing team scope.

## During and after the interview

The existing Daily connection and consent-controlled recording are retained.
The collapsible right panel contains questions, interview notes and current
connection's final transcript events. Notes autosave to SQLite through one serial
writer with revision checks. Leaving/finishing flushes pending notes first;
failed writes preserve the text and offer retry/export.

Completed interviews open the same workspace for reading their pinned questions,
notes and existing recording/transcript materials. There is no new note form
outside the interview.

Email and Telegram links use stored candidate contact values. They open the
respective contact/composer and never send a message automatically. Existing
booking/access notification workflows remain separate from this UI change.

## Release boundary

No database migration or private candidate fixture is required for this UI
simplification. Only existing service stories are published. Local recruiter
prototypes and their private JSON must remain excluded from the service bundle.
