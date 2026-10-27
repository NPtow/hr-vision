import type { Transcript, Materials } from '../types';
export type TranscriptTurn = { text: string; speaker?: string; start?: number; end?: number };
export function timestampSeconds(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : undefined;
  if (typeof value !== 'string' || !/^\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d+)?$/.test(value.trim())) return undefined;
  const parts = value.replace(',', '.').split(':').map(Number);
  if (parts.slice(1).some(n => n >= 60)) return undefined;
  return parts.reduce((sum, n) => sum * 60 + n, 0);
}
export function formatTimestamp(seconds: number): string {
  const n = Math.floor(seconds); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}
export function selectTranscript(recordingId: string, recordings: { id: string }[], transcripts: Transcript[]): Transcript | undefined {
  const bound = transcripts.filter(t => t.recordingId === recordingId);
  if (bound.length === 1) return bound[0];
  if (bound.length) return undefined;
  return recordings.length === 1 && transcripts.length === 1 && !transcripts[0].recordingId ? transcripts[0] : undefined;
}
export function parseTranscript(input: unknown): TranscriptTurn[] {
  if (!input || typeof input !== 'object') return [];
  const source = input as Record<string, unknown>;
  const turns = Array.isArray(source.turns) ? source.turns : Array.isArray(source.segments) ? source.segments : undefined;
  const parsedTurns = turns?.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const turn = item as Record<string, unknown>;
    if (typeof turn.text !== 'string' || !turn.text.trim()) return [];
    const milliseconds = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value / 1000 : undefined;
    const start = turn.start_ms !== undefined ? milliseconds(turn.start_ms) : timestampSeconds(turn.startSeconds ?? turn.start);
    const end = turn.end_ms !== undefined ? milliseconds(turn.end_ms) : timestampSeconds(turn.endSeconds ?? turn.end);
    const validBounds = start === undefined || end === undefined || end >= start;
    return [{ text: turn.text, speaker: typeof turn.speaker === 'string' ? turn.speaker : undefined, start: validBounds ? start : undefined, end: validBounds ? end : undefined }];
  });
  if (parsedTurns?.length) return parsedTurns;
  if (typeof source.text !== 'string' || !source.text.trim()) return [];
  const text = source.text.replace(/\r/g, '');
  const cues: TranscriptTurn[] = [];
  for (const block of text.split(/\n\s*\n/)) {
    const lines = block.split('\n');
    const index = lines.findIndex(line => line.includes('-->'));
    if (index < 0) continue;
    const [left, right] = lines[index].split('-->');
    const start = timestampSeconds(left.trim());
    if (start === undefined) continue;
    const content = lines.slice(index + 1).join('\n');
    const speaker = content.match(/<v\s+([^>]+)>/)?.[1];
    cues.push({ start, end: timestampSeconds(right.trim().split(/\s/)[0]), speaker, text: content.replace(/<[^>]*>/g, '').trim() });
  }
  return cues.length ? cues : [{ text }];
}

export function selectMaterialsTranscript(data: Materials | null, recordingId: string): Transcript | undefined {
  if (!data) return undefined;
  const recording = data.recordings.find(item => item.id === recordingId);
  if (!recording) return undefined;
  const matchingParts = data.parts.filter(item => item.recordingId === recordingId || item.id === recordingId);
  if (matchingParts.length > 1) return undefined;
  const part = matchingParts[0];
  if (part) {
    if (part.transcriptStatus !== 'ready' || part.selectedTranscriptAvailable !== true || typeof part.transcriptId !== 'string') return undefined;
    const matches = data.partTranscripts?.filter(t => t.id === part.transcriptId && t.recordingId === recordingId) || [];
    return matches.length === 1 ? matches[0] : undefined;
  }
  const status = data.transcription as { status?: string; state?: string } | undefined;
  if (status?.status !== 'ready' && status?.state !== 'ready') return undefined;
  return selectTranscript(recordingId, data.recordings, data.transcripts);
}
