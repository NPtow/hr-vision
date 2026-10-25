import type { AssessmentEvidence } from '../interview-assessment/types';

export type MaterialRecording = { id: string; url: string; partIndex?: number; duration?: number | null; expires?: number | null; name?: string };
export type MaterialTranscript = { id: string; text: string; recordingId?: string; selectionRevision?: number; origin?: string; model?: string };
export type TranscriptMaterials = { recordings: MaterialRecording[]; transcripts: MaterialTranscript[];
  transcription?: { status: string }; transcriptStatus?: string };

export function orderedRecordings<T extends { id: string; partIndex?: number }>(recordings: T[]): T[] {
  const ordinals = recordings.map(row => row.partIndex);
  const explicit = ordinals.every(n => typeof n === 'number' && Number.isInteger(n) && n >= 0) && new Set(ordinals).size === ordinals.length;
  return [...recordings].sort((a, b) => explicit ? a.partIndex! - b.partIndex! || a.id.localeCompare(b.id) : a.id.localeCompare(b.id));
}

/** Never join parts or substitute an earlier/capture text during a new selection. */
export function selectedTranscript(materials: TranscriptMaterials | null, recordingId?: string): MaterialTranscript | undefined {
  if (!materials || !recordingId) return undefined;
  const state = materials.transcription?.status ?? materials.transcriptStatus;
  if (state !== 'ready') return undefined;
  const recordings = materials.recordings;
  if (!recordings.some(row => row.id === recordingId)) return undefined;
  const linked = materials.transcripts.filter(row => row.recordingId === recordingId);
  if (linked.length === 1) return linked[0];
  // The historical single-record projection predates explicit source links.
  if (linked.length === 0 && recordings.length === 1 && materials.transcripts.length === 1 && !materials.transcripts[0].recordingId) return materials.transcripts[0];
  return undefined;
}

export function evidenceRecording(materials: TranscriptMaterials | null, evidence: AssessmentEvidence): string | undefined {
  if (!materials || evidence.seconds === null || !Number.isFinite(evidence.seconds) || evidence.seconds < 0) return undefined;
  const recordingId = evidence.recordingId ?? (materials.recordings.length === 1 ? materials.recordings[0].id : undefined);
  const selected = selectedTranscript(materials, recordingId);
  if (!selected || evidence.transcriptId && evidence.transcriptId !== selected.id) return undefined;
  const recording = materials.recordings.find(row => row.id === recordingId);
  if (recording?.duration != null && evidence.seconds >= recording.duration) return undefined;
  return recordingId;
}

export function transcriptStatusMessage(status?: string): string {
  return status === 'error' ? 'Не удалось подготовить основную расшифровку.'
    : status === 'not_recorded' ? 'Расшифровка этой записи не создавалась.'
    : status === 'ready' ? 'Расшифровка этой части пока недоступна.'
    : 'Основная расшифровка ещё обрабатывается.';
}
