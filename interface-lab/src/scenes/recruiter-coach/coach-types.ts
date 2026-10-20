import type { JSONContent } from '@tiptap/core';

export type CoachQuestion = {
  id: string; topic: string; question: string; why?: string; followups?: string[];
  caseGuide?: { setup: string; client_facts: string[]; concern: string; acceptance_condition: string; stop_condition: string };
  minutes?: number; evidenceNeeded?: string[]; exitRule?: string;
};
export type CoachPerson = {
  id: string; name: string; sourceSummary: string; promptSource: string; questions: CoachQuestion[];
  resume?: { text: string; originalText: string; filename: string; sources: string[] };
  originalResume?: { url: string; name: string; mimeType?: string };
  planVersion?: string;
};
export type CoachDraft = {
  current: number; status: Record<string, 'answered' | 'skipped'>;
  notes: Record<string, string>; general: string; startedAt?: number;
  noteDocument?: JSONContent;
};
export type CoachTranscriptTurn = { id: string; speaker: string; time: string; text: string };
