# Recruiter interview surface

Local stories: `hr-vision-recruiter-coach--rybkina` and `hr-vision-recruiter-coach--kretova`. One workspace contains the video slot, local self-preview and a folding question/notes panel.

These stories are **local previews**, not actual candidate calls. They do not create Daily rooms, call recruiter/meeting-lab APIs, record or transmit media, infer answers from speech, or change hiring statuses. Original-file loading is a separate documented capability below.

## Files and ownership

| File | Responsibility |
|---|---|
| `RecruiterInterviewShell.tsx` | Video layout, questions, tabs, fullscreen and original-résumé sheet. Call transport stays outside the shell. |
| `RecruiterCoach.tsx` | Local fixture loading, existing browser draft namespace, note-history wiring and local media. Do not use this adapter as a production data layer. |
| `coach-types.ts` | Candidate, question, draft and transcript types. |
| `TiptapNoteEditor.tsx` | Rich-text editor, slash commands and selection formatting. |
| `note-document.ts` | Plain-text migration and plain-text extraction from a document. |
| `note-history.ts` | IndexedDB snapshots of the main document **and question notes**. |
| `CoachNotesPanel.tsx` | Editor/history/restoration and optional model-enhancement preview. |
| `OriginalResumeViewer.tsx` | Original PDF/image rendering, validated local import and optional same-origin file loading. |
| `useLocalCoachMedia.ts` | Explicit local device acquisition and cleanup. No permissions on mount. |

Private scenarios still come from the ignored `public/__local_prototypes__/recruiter-coach.json` on localhost. This adapter neither generates nor modifies that JSON. Do not embed candidate records into tracked source code or publish private fixtures.

The source JSON and original draft keys are preserved. Candidate facts, generated questions, manual notes and any future model suggestion are distinct records.

## Shell props

The authoritative type is exported as `RecruiterInterviewShellProps`.

| Input | Meaning |
|---|---|
| `person: CoachPerson` | Name, questions, retained source/provenance fields, optional `originalResume`. |
| `draft: CoachDraft` | Current question index, stable-ID `notes`, plain `general`, optional Tiptap `noteDocument`, and preserved legacy `status/startedAt`. |
| `onChange(Partial<CoachDraft>)` | Merge a partial draft update; do not replace the whole draft. |
| `mediaSlot`, `selfPreview?` | Candidate/remote media and separately labelled local self-video. Panel collapse keeps the media slot mounted. |
| `mediaControls?` | `micOn/cameraOn/screenOn`, optional `busy`, and toggle callbacks. Omit when Daily Prebuilt owns its controls. |
| `phase` | `preview`, `live`, `finishing`, `completed`. Local adapter uses preview/completed. |
| `onFinish`, `onResume?` | External lifecycle actions. Local finish stops device tracks and ends preview only. |
| `notesContent?(navigateToQuestion)` | Injects a complete notes panel. The local adapter supplies `CoachNotesPanel` with IDB history. Without this slot the shell renders only the Tiptap editor. |
| `onExport?` | Optional recovery export shown with `saveError`; no normal export menu remains. |
| `transcript?` | `{id,speaker,time,text}[]`. Omission removes the tab; an empty supplied array shows a genuine empty transcript state. |
| `notice?`, `saveError?` | Exceptional connection/device or persistence state. |
| `durationLabel?`, `recording?` | Externally confirmed call state; local preview supplies neither. |
| `writable?` | Guards shell draft edits; defaults to false during finishing. Injected `notesContent` must independently receive the same writable state. |
| `completedLabel?` | Defaults to “Интервью завершено”; local adapter supplies “Просмотр завершён”. |

Panel visibility, tabs, question-plan popover, profile sheet and fullscreen are shell UI state. With no questions only the question panel becomes empty; media and exit controls remain available.

Question navigation now consists of previous, next and the plan popover. Skip, answer-check controls and the ellipsis actions menu have been removed. Advancing does not mark an answer received. Legacy `draft.status` values remain stored for compatibility; the UI does not silently erase them.

One current question is shown, with follow-ups, evidence guidance and roleplay details behind disclosures. Per-question notes remain plain text keyed by stable question IDs.

## Current notes: localStorage and migration

The current draft retains its original key:

```text
hrv-recruiter-coach-v1:<person.id>[:<person.planVersion>]
```

`RecruiterCoach` loads the previous object and merges updates functionally. It does not clear old keys, notes, current position, statuses or legacy fields.

The rich-text migration is lazy and non-destructive:

1. If `draft.noteDocument` exists, use that Tiptap JSON document.
2. Otherwise derive paragraphs from existing `draft.general` through `documentFromText`.
3. An editor change stores both the rich `noteDocument` and plain `general` text in the same current-draft object.
4. Restoring a historical snapshot updates the document, recalculates `general` and restores its complete question-note map.

The plain string remains useful for existing server compatibility and recovery export. It is not an alternative source of truth when a rich document exists. Supported formatting includes H2/H3 headings, bullet/numbered lists, quotes, bold and italic. Slash commands and selection formatting use Tiptap primitives; no paid Notion template is used.

Current draft storage is browser-local. It does not synchronize to another device or employee account. Failed localStorage writes surface a recovery message and optional text export.

## Persistent local history

`useNoteHistory(scope, snapshot)` uses IndexedDB database `hrv-coach-note-history-v1`, store `versions`, indexed by `scope`. The local adapter uses the full existing draft key as the scope, separating candidate and plan versions.

```ts
type NoteSnapshot = {
  document: JSONContent;
  questionNotes: Record<string, string>;
};
type NoteVersion = {
  id: string;
  scope: string;
  createdAt: string;
  reason: 'manual' | 'before_restore' | 'restored' | 'before_ai' | 'ai';
  snapshot: NoteSnapshot;
};
```

Non-empty initial state is checkpointed when the history hook loads. Changes are checkpointed after 1.8 seconds without another change. Opening history also checkpoints the current state. Before the first editor keystroke the adapter attempts to preserve the pre-edit baseline, including an empty document. Deleting previously entered content is a versionable change.

Snapshots are copied at invocation and written through a serial queue. The latest comparison and append share an IDB transaction; identical consecutive contents are deduplicated. Timestamps remain monotonic within a scope. History is not silently truncated to a fixed count.

Restore checkpoints the current state, then persists the restored snapshot before replacing the editor. It restores question notes as well as the main document. History failures do not deliberately replace the current text; quota/open errors remain visible.

“Persistent” here means surviving normal reloads in the same browser origin. It is not a server backup or a promise against browser storage eviction, private-mode limits or user deletion. The latest edit may not yet have a separate history version inside the debounce window; current text is persisted independently in localStorage.

## Optional enhancement contract — not connected locally

`CoachNotesPanel` accepts an optional `onEnhance: EnhanceNotes`:

```ts
type EnhanceNotes = (input: {
  manualDocument: JSONContent;
  plainText: string;
  questionNotes: Record<string, string>;
  transcript: CoachTranscriptTurn[];
  baseRevision: string;
}) => Promise<{ document: JSONContent; baseRevision: string }>;
```

The local adapter supplies **neither `onEnhance` nor a transcript**. There is no recruiter-scoped model endpoint attached to this preview. No artificial response, locally invented “AI result”, or existing meeting-lab response is substituted.

When a real adapter is supplied, the action appears only because the callback exists, and requires transcript data and writable state. It saves the manual snapshot before requesting an enhancement. A matching `baseRevision` and unchanged current snapshot are required before showing/applying the result. This value is a local snapshot fingerprint, not a server revision number or authorization token.

The returned document is shown as a separate read-only proposal. “Оставить мои записи” discards it; “Применить” checkpoints it before updating the current main document. Question notes remain human content. Errors preserve manual notes.

Before production attachment the server/adapter must validate the entire returned document against the allowed Tiptap schema, bind the request to the authorized interview and immutable source snapshot, and provide real evidence-grounded generation. The component's top-level `doc/content` check is not a complete model-output validator. Keep server CAS revision control in addition to the UI fingerprint.

## Original résumé viewer

Clicking the candidate name opens `OriginalResumeViewer`; it no longer substitutes the extracted résumé text for an original file.

```ts
person.originalResume?: {
  url: string;
  name: string;
  mimeType?: string;
}
```

Accepted originals are PDF, PNG and JPEG, up to 20 MiB. Content signatures are inspected and the document/image must decode before replacing a stored local file. Password-protected or invalid PDFs have explicit errors.

- PDF pages render through bundled `pdfjs-dist` to canvas, with lazy page rendering.
- Images render from an owned object URL that is released on cleanup.
- No download, print or source-link UI is provided in this viewer.
- This is **not DRM**: the browser still receives file bytes, and users can capture the screen or recover accessible data.

Without a supplied original, the viewer offers explicit local file selection and stores the validated Blob in IndexedDB `hrv-coach-original-resumes-v1`, store `originals`, keyed by candidate `scope`. Local selection is never uploaded.

A supplied URL must be HTTP(S), same-origin, and must not redirect. It is fetched with same-origin credentials and a size limit. The viewer does not add a recruiter Bearer token. Production therefore needs an authorized same-origin file endpoint or a dedicated adapter; a cross-origin signed storage URL is not accepted directly.

Neither current source candidate has `originalResume`: the fixtures only contain public résumé text (`ALL-NEW016-public-resume.txt`, `ALL-UP020-public-resume.txt`). Actual original CVs are absent until an authorized file is supplied or the user explicitly selects one locally. A previously selected browser-local file can still display; it is not evidence of a server upload.

The shell now includes a viewer that performs file reads/IDB access. Although it does not own interview transport, it should no longer be described as having absolutely no data side effects.

## Local media

`useLocalCoachMedia()` exposes camera/screen streams, mic/camera/screen flags, busy/error, toggles and `stop`.

All devices begin off. Only explicit toggles call `getUserMedia` or `getDisplayMedia({video:true,audio:false})`. Camera and microphone streams are separate; microphone data is not played, recorded or transmitted. The camera appears muted and mirrored in the self-preview labelled “Вы”, never as Rybkina's footage. The main slot is a candidate-initials placeholder unless the user chooses local screen sharing.

Native track end and stop/unmount clear flags and owned tracks. Pending prompts are invalidated; late streams are stopped immediately. No recording/transcript result is claimed.

## Production migration

Keep `../recruiter-portal/RecruiterCall.tsx`, its interview identity and the existing recruiter endpoints:

- `/api/hr/recruiter/interview/join`
- `/api/hr/recruiter/interview/start`
- `/api/hr/recruiter/interview/complete`
- `/api/hr/recruiter/interview/coach/*`

Keep `../recruiter-portal/interview-coach.ts` (`useInterviewCoach`) for pinned script revisions, serialized saves, conflicts and `flush()`. Convert the shell's numeric current index into the stable question ID; keep note maps keyed by ID. Preserve `flush()` before leave/complete and hold `writable=false` during that sequence.

The existing recruiter call uses `DailyIframe.createFrame`. Put that mount into `mediaSlot` and omit external media controls, or adapt the custom track layout from meeting-notes-demo to the same recruiter endpoints. Do not change the media slot key when collapsing the panel.

**Do not mount `MeetingNotesDemo` or use `/api/hr/meeting-lab/sessions` as the data layer.** It creates a separate meeting entity, not the recruiter's interview with this candidate.

The current recruiter server persists plain `general` and answer notes; it does not yet persist this rich `noteDocument`, local history, original-file association or enhancement proposals. Add explicit server contracts/migrations before claiming cross-account or cross-device persistence. Passing the local IDB hook into production alone is insufficient.

Likewise, production script questions currently store `id/topic/question/why/followups`; richer local case/evidence fields need their own compatibility decision.

Real recording state comes only from confirmed provider events. Transcript turns from the current Daily connection are not complete server history or proof that a question was answered. Retain consent/start handling, error/retry controls and an exit path in the outer call container.

Injected `notesContent` is responsible for its own writable guard and persistence errors. The shell's `writable` prop does not automatically lock arbitrary injected content.

## Design provenance

The layout follows the existing product meeting/player vocabulary: dark video stage, white folding panel, icon tabs and profile sheet. Apple Design guidance informs hierarchy, grouping, immediate feedback and accessibility; web dimensions are local design choices.

Earlier behavior references remain [VideoDB Sales Copilot](https://github.com/video-db/sales-copilot/blob/60d22a043c400ae567e78ea97939cf561f7cae18/src/main/services/copilot/playbook-tracker.service.ts) and [Cue](https://github.com/Suryanandx/cue/blob/0f4b093b4857eb66875fe33e3a51ca9b791fcfa3/cue/agents/builtins/sales.toml). These references do not make this preview an automatic live copilot.
