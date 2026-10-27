import type { RecordingChapter } from './InterviewReview';

export type InterviewParagraph = { start: number; end: number; speaker?: string; text: string };

/** Presentation grouping only. Keep source wording, speaker boundaries and real timestamps. */
export function interviewParagraphs(cues: readonly RecordingChapter[], boundaries: readonly number[] = []): InterviewParagraph[] {
  const paragraphs: InterviewParagraph[] = [];
  for (const cue of cues) {
    if (cue.seconds === null || !Number.isFinite(cue.seconds) || cue.seconds < 0 || !cue.fragment.trim()) continue;
    const end = cue.endSeconds ?? cue.seconds;
    if (!Number.isFinite(end) || end < cue.seconds) continue;
    const previous = paragraphs.at(-1);
    if (cue.speaker && previous?.speaker === cue.speaker
      && !boundaries.some(start => start > previous.start && start <= cue.seconds!) && cue.seconds >= previous.end
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


export type InterviewTranscriptSection = {
  id: string;
  chapters: RecordingChapter[];
  start: number | null;
  rows: { row: InterviewParagraph; index: number }[];
};

/** The source clock is authoritative. Group for reading without inventing or repeating speech. */
export function interviewTranscriptSections(cues: readonly RecordingChapter[], chapters: readonly RecordingChapter[]) {
  const sections: InterviewTranscriptSection[] = [];
  const timed = chapters.filter(chapter => chapter.kind !== 'transcript' && chapter.seconds !== null
    && Number.isFinite(chapter.seconds) && chapter.seconds >= 0).sort((a, b) => a.seconds! - b.seconds!);
  for (const chapter of timed) {
    const previous = sections.at(-1);
    if (previous?.start === chapter.seconds) {
      if (!previous.chapters.some(other => other.title === chapter.title && other.detail === chapter.detail)) previous.chapters.push(chapter);
    } else sections.push({ id: `chapter-${chapter.seconds}`, start: chapter.seconds, chapters: [chapter], rows: [] });
  }
  const paragraphs = interviewParagraphs(cues, sections.map(section => section.start!));
  const prelude: InterviewTranscriptSection = { id: 'transcript-start', start: null, chapters: [], rows: [] };
  for (const [index, row] of paragraphs.entries()) {
    const section = [...sections].reverse().find(section => section.start! <= row.start) ?? prelude;
    section.rows.push({ row, index });
  }
  if (prelude.rows.length || !sections.length) sections.unshift(prelude);
  // Untimed chapter descriptions remain readable, but never become made-up video jumps.
  chapters.filter(chapter => chapter.kind !== 'transcript' && (chapter.seconds === null
    || !Number.isFinite(chapter.seconds) || chapter.seconds < 0)).forEach((chapter, index) => {
    sections.push({ id: `untimed-${index}`, start: null, chapters: [chapter], rows: [] });
  });
  return { paragraphs, sections };
}
