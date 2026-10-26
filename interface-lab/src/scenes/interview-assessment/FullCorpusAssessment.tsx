import './full-corpus-assessment.css';
import { ModelDistribution, SkillDisclosure } from '../selection-workbench/ModelDistribution';
import { probability } from '../selection-workbench/distribution-data';

/** Private assessment-core/API.md, full-corpus-level-choice-v1.
 * The authorised portal adapter supplies a current run; this view never starts
 * jobs, normalises probabilities, invents citations or calculates a score.
 */
export type FullCorpusAssessmentResult = {
  run_id: string; candidate_id: string; contract_version: 'full-corpus-level-choice-v1';
  corpus_sha256: string; rubric_id: string; rubric_version: string; rubric_sha256: string;
  model: string; chunk_count: number; usage: Record<string, number>;
  sources: { source_id: string; version: string; kind: 'cv' | 'interview'; sha256: string }[];
  parameters: {
    parameter_id: string; title: string; status: 'assessed' | 'insufficient_data'; selected_level_id: string | null;
    levels: { level_id: string; label: string; description: string; probability: number }[];
    insufficient_data_probability: number; model_confidence: number;
  }[];
};
export type FullCorpusRun = {
  run_id: string;
  status: 'queued' | 'sent' | 'response_saved' | 'completed' | 'failed' | 'outcome_unknown';
  assessment: FullCorpusAssessmentResult | null; error: { code: string; status?: number | null } | null;
  corpus_revision: string; rubric_version: string; model: string;
};
export const fullCorpusStatusLabels: Record<FullCorpusRun['status'], string> = {
  queued: 'В очереди', sent: 'Считается', response_saved: 'Сохраняется результат',
  completed: 'Расчёт завершён', failed: 'Ошибка расчёта', outcome_unknown: 'Исход расчёта неизвестен',
};

export function FullCorpusAssessment({ run, candidateId }: { run: FullCorpusRun; candidateId: string }) {
  const result = run.assessment;
  if (run.status !== 'completed') return <section className="fc-assessment" aria-label="Оценка по параметрам">
    <p role="status">{fullCorpusStatusLabels[run.status] ?? 'Статус неизвестен'}</p>
    {run.error?.code === 'context_limit' && <p>Полный объём материалов превышает лимит модели. Материалы не сокращались; оценка не получена.</p>}
    {run.status === 'outcome_unknown' && <p>Ответ расчёта не подтверждён. Автоматический повтор не запускается.</p>}
  </section>;
  if (!result || result.candidate_id !== candidateId || result.run_id !== run.run_id ||
      result.corpus_sha256 !== run.corpus_revision || result.rubric_version !== run.rubric_version ||
      result.contract_version !== 'full-corpus-level-choice-v1') {
    return <p className="fc-error" role="alert">Не удалось подтвердить версию оценки. Обновите карточку.</p>;
  }
  return <section className="fc-assessment md-candidate-skills" aria-label="Оценка по параметрам">
    <h3>Оценка навыков</h3>
    {result.parameters.map(parameter => {
      const selected = parameter.levels.find(level => level.level_id === parameter.selected_level_id);
      return <SkillDisclosure key={parameter.parameter_id} name={`candidate-skills-${candidateId}`} summary={<span><strong>{parameter.title}</strong><small>{parameter.status === 'insufficient_data' ? 'Недостаточно данных' : selected?.label ?? 'Уровень не подтверждён'}</small></span>}>
        <ModelDistribution title="Уровень кандидата" data={{
          options: [...parameter.levels.map(level => ({ id: level.level_id, label: level.label, description: level.description, probability: probability(level.probability) })), { id: 'insufficient_data', label: 'Недостаточно данных', probability: probability(parameter.insufficient_data_probability), unknown: true }],
          choice: parameter.status === 'insufficient_data' ? 'insufficient_data' : parameter.selected_level_id,
          confidence: probability(parameter.model_confidence),
        }}/>
      </SkillDisclosure>;
    })}
    <details className="fc-version"><summary>Версия и состав материалов</summary>
      <p>Шкала: {result.rubric_version} · модель: {result.model}</p>
      <p>Резюме: {result.sources.filter(source => source.kind === 'cv').length} · частей интервью: {result.sources.filter(source => source.kind === 'interview').length}</p>
      <p>Это состав материала для всего расчёта, а не цитаты в подтверждение отдельных параметров.</p>
      <p className="fc-hash">Расчёт: {result.run_id}<br/>Корпус: {result.corpus_sha256}</p>
    </details>
  </section>;
}
