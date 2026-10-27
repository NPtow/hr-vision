/** Saved full-corpus result only. Never derives skill strength from confidence. */
export interface AssessmentLevel { id: string; label: string; description: string; probability: number }
export interface AssessmentCriterion {
  id: string; title: string; status: 'assessed' | 'insufficient_data';
  selectedLevel: AssessmentLevel | null; levels: AssessmentLevel[]; insufficientProbability: number;
}
export type AssessmentModel = {
  state: 'unavailable' | 'pending' | 'failed' | 'invalid'; message: string;
} | {
  state: 'ready'; criteria: AssessmentCriterion[];
  provenance: { runId: string; corpusHash: string; rubricVersion: string; model: string };
};
const record = (v: unknown): Record<string, unknown> | null => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null;
const nonempty = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0;
const probability = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
const invalid = (): AssessmentModel => ({ state: 'invalid', message: 'Не удалось подтвердить сохранённую оценку.' });
export function normalizeAssessment(assessment: unknown): AssessmentModel {
  const envelope = record(assessment);
  if (envelope?.state === 'stale') return { state: 'unavailable', message: 'Материалы изменились. Сохранённая оценка устарела.' };
  if (envelope?.state === 'unavailable') return { state: 'unavailable', message: 'Сохранённая оценка пока недоступна.' };
  const run = record(envelope?.coreRun);
  if (!run) return { state: 'unavailable', message: 'Сохранённая оценка пока недоступна.' };
  if (run.status !== 'completed') {
    const messages: Record<string, string> = { queued: 'Оценка в очереди.', sent: 'Оценка рассчитывается.', response_saved: 'Результат сохраняется.', failed: 'Расчёт оценки завершился ошибкой.', outcome_unknown: 'Результат расчёта не подтверждён.' };
    if (typeof run.status !== 'string' || !messages[run.status]) return invalid();
    return { state: run.status === 'failed' || run.status === 'outcome_unknown' ? 'failed' : 'pending', message: messages[run.status] };
  }
  const result = record(run.assessment);
  if (!result || result.contract_version !== 'full-corpus-level-choice-v1' ||
    !nonempty(result.run_id) || result.run_id !== run.run_id || !nonempty(result.candidate_id) ||
    !nonempty(result.corpus_sha256) || result.corpus_sha256 !== run.corpus_revision ||
    !nonempty(result.rubric_version) || result.rubric_version !== run.rubric_version ||
    !nonempty(result.model) || (nonempty(run.model) && result.model !== run.model) ||
    (nonempty(envelope?.candidateId) && result.candidate_id !== envelope.candidateId) || !Array.isArray(result.parameters) || result.parameters.length === 0) return invalid();
  const criteria: AssessmentCriterion[] = [];
  const ids = new Set<string>();
  for (const raw of result.parameters) {
    const p = record(raw);
    if (!p || !nonempty(p.parameter_id) || ids.has(p.parameter_id) || !nonempty(p.title) ||
      (p.status !== 'assessed' && p.status !== 'insufficient_data') ||
      !probability(p.insufficient_data_probability) || !Array.isArray(p.levels) || p.levels.length !== 5) return invalid();
    const levels: AssessmentLevel[] = [];
    const levelIds = new Set<string>();
    for (const rawLevel of p.levels) {
      const level = record(rawLevel);
      if (!level || !nonempty(level.level_id) || levelIds.has(level.level_id) || !nonempty(level.label) ||
        !nonempty(level.description) || !probability(level.probability)) return invalid();
      levelIds.add(level.level_id);
      levels.push({ id: level.level_id, label: level.label, description: level.description, probability: level.probability });
    }
    const selectedLevel = levels.find(l => l.id === p.selected_level_id) ?? null;
    if ((p.status === 'assessed' && !selectedLevel) || (p.status === 'insufficient_data' && p.selected_level_id !== null)) return invalid();
    ids.add(p.parameter_id);
    criteria.push({ id: p.parameter_id, title: p.title, status: p.status, selectedLevel: p.status === 'assessed' ? selectedLevel : null, levels, insufficientProbability: p.insufficient_data_probability });
  }
  return { state: 'ready', criteria, provenance: { runId: result.run_id, corpusHash: result.corpus_sha256, rubricVersion: result.rubric_version, model: result.model } };
}
/** Map consumers must keep selected level labels/descriptors; probability is not a skill score. */
export const normalizeAssessmentForSkillMap = normalizeAssessment;
