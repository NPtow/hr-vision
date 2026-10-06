import type { RecordingChapter } from '../interview-review/InterviewReview';

function seconds(value: string) {
  const parts = value.replace(',', '.').split(':').map(Number);
  if (parts.some(n => !Number.isFinite(n) || n < 0) || parts.slice(1).some(n => n >= 60)) return null;
  return parts.reduce((total, n) => total * 60 + n, 0);
}

/** Daily stores realtime transcripts as WebVTT. Plain text never gets invented timestamps. */
export function parseMeetingTranscript(text: string): RecordingChapter[] {
  return text.replace(/\r\n?/g, '\n').split(/\n\s*\n/).flatMap(block => {
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
      detail: fragment, fragment, seconds: start, source: `${match[1]} · расшифровка встречи` }];
  }).sort((a,b) => a.seconds! - b.seconds!);
}
