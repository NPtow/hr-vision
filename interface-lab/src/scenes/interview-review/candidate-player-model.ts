import type { RecordingChapter } from './InterviewReview';

export type InterviewParagraph = { start: number; end: number; speaker?: string; text: string };

/** Presentation grouping only. Keep source wording, speaker boundaries and real timestamps. */
export function interviewParagraphs(cues: readonly RecordingChapter[]): InterviewParagraph[] {
  const paragraphs: InterviewParagraph[] = [];
  for (const cue of cues) {
    if (cue.seconds === null || !Number.isFinite(cue.seconds) || cue.seconds < 0 || !cue.fragment.trim()) continue;
    const end = cue.endSeconds ?? cue.seconds;
    if (!Number.isFinite(end) || end < cue.seconds) continue;
    const previous = paragraphs.at(-1);
    if (cue.speaker && previous?.speaker === cue.speaker && cue.seconds >= previous.end
      && cue.seconds - previous.end <= 2 && end - previous.start <= 18
      && previous.text.length + cue.fragment.length < 340) {
      previous.text += ' ' + cue.fragment.trim(); previous.end = end;
    } else paragraphs.push({ start: cue.seconds, end, speaker: cue.speaker, text: cue.fragment.trim() });
  }
  return paragraphs;
}

export function interviewTime(seconds: number) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const hours = Math.floor(value / 3600);
  return `${hours ? `${hours}:` : ''}${String(Math.floor(value / 60) % 60).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

export function interviewTimeline(chapters: readonly RecordingChapter[], duration: number) {
  const starts = [...new Set([0, ...chapters.map(c => c.seconds).filter((s): s is number => s !== null && Number.isFinite(s) && s >= 0 && s < duration)])].sort((a, b) => a - b);
  return duration > 0 && Number.isFinite(duration) ? starts.map((start, i) => ({ start, end: starts[i + 1] ?? duration })) : [];
}
