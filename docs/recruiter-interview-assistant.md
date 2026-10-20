# Recruiter interview assistant

The existing Daily recruiter/candidate call now includes a folding assistant
panel: the approved questions, per-question notes and general notes, and live
final transcript events. Resume and candidate contact actions stay available
even if the video provider cannot connect.

## Prepare before the call

1. Open the candidate's interview in the recruiter cabinet. “Материалы для
   агента” exports the real resume/profile, the exact candidate/interview IDs,
   the current draft revision and a `draftTemplate`.
2. In a separate agent conversation, use the owner's agreed system prompt to
   produce `questions` and `source` in that template. This application does not
   generate questions in the background or choose a replacement system prompt.
   Record the prompt version and source of evidence in `source`. Do not invent
   candidate answers or scores.
3. Save the returned JSON through “Добавить черновик”, or let a trusted operator
   import it with the bridge below. A new revision becomes a draft; the
   previous approval remains available until another draft is approved.
4. Read the questions, optionally edit them and choose “Согласовать для
   интервью”. Approval is a separate authenticated recruiter action.
5. Joining pins the approved version for that interview. Later imports cannot
   replace the script being used. An unprepared script does not block the call
   or general note-taking.

The local Kretova/Rybkina companions remain separate reference screens. Their
private files are not bundled into the published service or silently imported
as approved scripts.

## Human-operated agent bridge

`interface-lab/scripts/recruiter-script.py` works against an existing database
with trusted operator filesystem access. It never creates recruiter sessions,
changes candidate statuses, approves a script, or sends a message.

```
python3 interface-lab/scripts/recruiter-script.py --db /path/to/journey.sqlite3 \
  context --candidate person_ID --interview interview_ID --output /private/context.json
python3 interface-lab/scripts/recruiter-script.py --db /path/to/journey.sqlite3 \
  draft --file /private/draft.json
```

Context exports use mode `0600` and refuse to overwrite existing files. Keep
exports/drafts outside version control. The import must contain exactly
`candidateId`, `interviewId`, `expectedRevision`, `questions`, and optional
`source`. Each question contains a unique stable `id`, `question`, optional
`topic`, `why`, and `followups`. Importing a stale revision fails instead of
overwriting a newer draft. Original resume content is source data, never agent
instructions.

The same operations are available to the assigned authenticated recruiter via
POST `/api/hr/recruiter/interview/coach/{context,state,draft,approve,progress,backup}`.
Every request includes `candidateId` and `interviewId`; authority is derived
from the recruiter session and the actual slot owner. Candidates and employers
cannot read scripts or internal notes.

## Notes and fallback communication

Notes autosave to SQLite with revision checks. One serial writer drains edits
that arrive during saving. Finishing/leaving through our controls flushes notes
first. Failed/conflicting writes leave the text visible and downloadable, with
an error; an unsaved tab has the browser's unload warning. Completed interview
details retain the pinned script and notes beside the existing recording.

Candidate email and Telegram come from their stored profile/intake. Email
opens a composer with a draft; Telegram opens the candidate's profile.
“Если кандидат не подключился” provides editable-by-copy message text.
Nothing sends automatically. The recruiter supplies a real HTTPS Zoom or
other meeting URL per interview; no meeting is invented or created remotely.
Moving to that URL does not imply our Daily recording/transcription captures
the external call.

Live transcript display uses Daily final events from the current connection.
It does not interpret answers, advance the script automatically, or replace
the provider's complete recording/transcript retrieval after the call.

## Release

Additive tables: `cp_interview_scripts`, `cp_interview_coach`. No destructive
migration or candidate data backfill. Before publishing, install the reviewed
receiver allowlist including `recruiter_coach.py`. The previous complete runtime
remains a valid rollback archive; its code leaves the new tables untouched.
