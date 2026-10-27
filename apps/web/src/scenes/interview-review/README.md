# Interview review

Reusable review of the **initial recruiter interview**, using `Candidate.evidence`, `summary`, and `unknown`. It is not a manager meeting recording, a post-meeting questionnaire, or a new scoring system.

```tsx
import { InterviewReview } from './InterviewReview';
import { InterviewMediaProvider, LabMediaControl } from './InterviewMediaContext';

<InterviewMediaProvider>
  <LabMediaControl />
  <InterviewReview person={candidate} initialEvidenceIndex={0} compact />
</InterviewMediaProvider>
```

`InterviewReview` accepts `person: Candidate`, `initialEvidenceIndex?: number`, `compact?: boolean`, and optional `media?: { src: string; label?: string; localPreview?: boolean }`. An explicit media prop takes priority over provider media. CSS is imported by each exported component module.

`MeetingRecordingReview` reuses the player, accessible seek timeline and chapter view for a completed meeting. It receives an explicit `sourceId`, participant `name`, `chapters`, optional server-supplied `media` and `transcriptText`. It never reads the screening media context or fictional candidate evidence. Missing media keeps the review layout visible with a disabled timeline and a factual empty state. `connected-journey/transcript.ts` reads timestamps from the provider's WebVTT; plain text receives no invented timestamps. Multiple recordings are not assigned one transcript clock without a verified segment mapping.

- Without a source, chapter buttons select text; they do not claim video playback or a successful seek.
- `mm:ss` / `hh:mm:ss` source timestamps are parsed into seconds. Chapters follow actual `currentTime`. Source timestamps outside the loaded recording are reported instead of silently clamped.
- Short mode is a key-moment navigator for the same full recording. No fabricated clip durations, generated video, or automatic skipping. Switching modes preserves the same video element and playback time.
- Source/candidate changes and unmount pause the prior player. A player starting pauses other interview players on the same page.
- Provider-managed local files use only `blob:` URLs. They are not uploaded or persisted. URLs are revoked on replace, reset, and provider unmount. A local test file is explicitly not candidate evidence.
- Fictional quotes remain labelled candidate statements; recruiter conclusion has no invented author, date, or numerical score.
- Only backend-supplied manager-meeting materials may be used for a later meeting-review screen. Do not pass initial HR interview materials as that meeting's recording.
