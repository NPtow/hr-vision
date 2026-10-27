import { normalizeAssessment } from '../assessment-model';

const percent = (value: number) => new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 }).format(value);

export function AssessmentPanel({ assessment }: { assessment: unknown }) {
  const model = normalizeAssessment(assessment);
  return <section className="cp-card cp-assessment" aria-label="Оценка по материалам">
    <div className="cp-section-heading"><div><p className="cp-eyebrow">Сохранённый результат</p><h2>Оценка по материалам</h2></div></div>
    {model.state !== 'ready' ? <p className="cp-muted" role={model.state === 'invalid' || model.state === 'failed' ? 'alert' : 'status'}>{model.message}</p> : <>
      <div className="cp-assessment-list">{model.criteria.map(criterion => <article className="cp-assessment-criterion" key={criterion.id} id={`assessment-${encodeURIComponent(criterion.id)}`} data-status={criterion.status}>
        <h3>{criterion.title}</h3>
        {criterion.status === 'insufficient_data' ? <p className="cp-badge cp-assessment-insufficient">Недостаточно данных</p> : <>
          <p className="cp-assessment-level">{criterion.selectedLevel?.label}</p>
          <p className="cp-assessment-description">{criterion.selectedLevel?.description}</p>
        </>}
        <details className="cp-assessment-probabilities"><summary>Вероятности уровней</summary>
          <dl>{criterion.levels.map(level => <div key={level.id} data-selected={criterion.selectedLevel?.id === level.id}>
            <dt>{level.label}<small>{level.description}</small></dt><dd>{percent(level.probability)}</dd>
          </div>)}<div data-selected={criterion.status === 'insufficient_data'}><dt>Недостаточно данных</dt><dd>{percent(criterion.insufficientProbability)}</dd></div></dl>
        </details>
      </article>)}</div>
      <details className="cp-assessment-provenance"><summary>Версия расчёта</summary><p className="cp-muted">Шкала: {model.provenance.rubricVersion} · модель: {model.provenance.model}</p><p>Расчёт: <code>{model.provenance.runId}</code><br/>Корпус: <code>{model.provenance.corpusHash}</code></p></details>
    </>}
  </section>;
}
export default AssessmentPanel;
