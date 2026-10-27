import type { InterviewAnswer } from './interview-assistant-api';

export type AnswerSource = InterviewAnswer['evidence'][number] & { number: number; startMs: number };
export type AnswerPart = { text: string; sources: AnswerSource[] };

/** Only server-supplied sources in this response can become seek controls. */
export function interviewAnswerParts(answer: InterviewAnswer): AnswerPart[] {
  const sources = new Map<string, AnswerSource>();
  for (const source of answer.evidence) {
    if (typeof source.id !== 'string' || sources.has(source.id) || typeof source.startMs !== 'number'
      || !Number.isFinite(source.startMs) || source.startMs < 0) continue;
    sources.set(source.id, { ...source, startMs: source.startMs, number: sources.size + 1 });
  }
  const parts = answer.parts?.length ? answer.parts : [{ text: answer.text, sourceIds: [...sources.keys()] }];
  return parts.filter(part => typeof part.text === 'string' && part.text.trim()).map(part => ({
    text: part.text,
    sources: [...new Set(Array.isArray(part.sourceIds) ? part.sourceIds : [])].flatMap(id => {
      const source = sources.get(id);
      return source ? [source] : [];
    }),
  }));
}
