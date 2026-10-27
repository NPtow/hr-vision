import type { RecordingChapter } from '../interview-review/InterviewReview';

function seconds(value: string) {
  const parts = value.replace(',', '.').split(':').map(Number);
  if (parts.some(n => !Number.isFinite(n) || n < 0) || parts.slice(1).some(n => n >= 60)) return null;
  return parts.reduce((total, n) => total * 60 + n, 0);
}

/** Daily WebVTT and DSA's explicit [start-end] speaker lines. Plain text has no invented timing. */
export function parseMeetingTranscript(text: string): RecordingChapter[] {
  const normalized = text.replace(/\r\n?/g, '\n');
  if (!/^\s*WEBVTT\b/.test(normalized) && !/^.*-->.*$/m.test(normalized)) {
    return normalized.split('\n').flatMap(line => {
      const match = line.match(/^\s*\[((?:\d+:)?\d{2}:\d{2})-((?:\d+:)?\d{2}:\d{2})\]\s*([РК]):\s*(\S.*?)\s*$/);
      if (!match) return [];
      const start = seconds(match[1]);
      const end = seconds(match[2]);
      // DSA's second-rounded export includes valid point cues such as [01:01-01:01].
      if (start === null || end === null || end < start) return [];
      const speaker = match[3] === 'Р' ? 'Рекрутер' : 'Кандидат';
      return [{ title: speaker, detail: match[4], fragment: match[4], seconds: start, endSeconds: end,
        speaker, isQuote: true, source: `${match[1]} — ${match[2]}` }];
    }).sort((a, b) => a.seconds! - b.seconds!);
  }
  return normalized.split(/\n\s*\n/).flatMap(block => {
    const lines = block.trim().split('\n');
    if (/^(NOTE|STYLE|REGION)(?:\s|$)/.test(lines[0])) return [];
    const index = lines.findIndex(line => line.includes('-->'));
    if (index < 0) return [];
    const match = lines[index].match(/^\s*((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d{3})(?:\s|$)/);
    if (!match) return [];
    const start = seconds(match[1]);
    const end = seconds(match[2]);
    if (start === null || end === null || end <= start) return [];
    const raw = lines.slice(index + 1).join(' ');
    // Daily's stored VTT can use <v>Speaker:</v>Text as well as standard
    // WebVTT's <v Speaker>Text</v>. Keep the speaker separate in both formats.
    const label = raw.match(/^\s*<v>([^<]+)<\/v>/);
    const voice = raw.match(/<v(?:\.[^\s>]*)?\s+([^>]+)>/)?.[1]
      || label?.[1].replace(/:\s*$/, '').trim();
    const spoken = label ? raw.slice(label[0].length) : raw;
    const fragment = spoken.replace(/<[^>]*>/g, '').replace(/&(?:amp|lt|gt|quot|apos|nbsp);/g, s => ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&nbsp;':' '}[s] || s)).trim();
    if (!fragment) return [];
    return [{ title: voice || (fragment.length > 75 ? fragment.slice(0,72) + '…' : fragment),
      detail: fragment, fragment, seconds: start, endSeconds: end, speaker: voice, source: `${match[1]} · расшифровка встречи` }];
  }).sort((a,b) => a.seconds! - b.seconds!);
}

/** Half-open clip interval. A rounded zero-length cue is a point, never an invented duration. */
export function transcriptInRange(cues: RecordingChapter[], start: number, end: number | null): RecordingChapter[] {
  if (!Number.isFinite(start) || start < 0 || (end !== null && (!Number.isFinite(end) || end <= start))) return [];
  return cues.filter(cue => {
    if (cue.seconds === null || !Number.isFinite(cue.seconds)) return false;
    const cueEnd = cue.endSeconds ?? cue.seconds;
    if (!Number.isFinite(cueEnd) || cueEnd < cue.seconds) return false;
    return (cueEnd === cue.seconds ? cue.seconds >= start : cueEnd > start)
      && (end === null || cue.seconds < end);
  });
}

/** Presentation only: retain every cue's words while showing a speaker once per consecutive turn. */
export function groupTranscriptTurns(cues: RecordingChapter[]): { speaker?: string; fragments: string[] }[] {
  const turns: { speaker?: string; fragments: string[] }[] = [];
  for (const cue of cues) {
    const previous = turns.at(-1);
    if (cue.speaker && previous?.speaker === cue.speaker) previous.fragments.push(cue.fragment);
    else turns.push({ speaker: cue.speaker, fragments: [cue.fragment] });
  }
  return turns;
}
