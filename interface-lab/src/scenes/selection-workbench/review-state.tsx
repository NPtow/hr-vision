import { hasHRInterview } from './matching-contract';
import type { SelectionPerson, Workflow, WorkflowState, Readiness } from './api';
import { FullCorpusAssessment, fullCorpusStatusLabels } from '../interview-assessment/FullCorpusAssessment';

export const assessmentLabels: Record<string, string> = {
  dispatch_unknown: 'Проверить запуск', outcome_unknown: 'Исход расчёта неизвестен',
  not_started: 'Не рассчитано', waiting_sources: 'Ждёт материалы', queued: 'В очереди',
  processing: 'Считается', saved: 'Расчёт сохранён', ready: 'Готово',
  insufficient_data: 'Недостаточно данных', stale: 'Результат устарел', error: 'Ошибка расчёта',
  cancelled: 'Остановлено', expired: 'Срок хранения истёк', legacy: 'Архивная оценка',
  unavailable: 'Не удалось проверить', unknown: 'Статус неизвестен', checking: 'Проверяем оценку…',
};
export const interviewLabels: Record<string, string> = {
  not_scheduled: 'Не назначено', scheduled: 'Назначено', open: 'Комната открыта',
  closed: 'Встреча закрыта', unknown: 'Статус неизвестен',
};
export const materialLabels: Record<string, string> = {
  loading: 'Проверяем материалы…', ready: 'Есть выбранный текст', processing: 'Обрабатываются',
  needs_transcript: 'Нет основного текста', missing: 'Нет материалов в каталоге',
  unavailable: 'Не удалось проверить', error: 'Ошибка обработки', unknown: 'Готовность не подтверждена',
};
export const matchLabels: Record<string, string> = {
  no_vacancy: 'Выберите вакансию', not_compared: 'Не сравнивали', ranked: 'Рекомендует',
  needs_assessment: 'Нужна оценка', non_matching: 'Не соответствует', conflicting: 'Противоречия',
  failed_processing: 'Ошибка сравнения', stale: 'Сравнение устарело',
  comparison_saved: 'Сравнение сохранено', compared: 'Сравнение готово', waiting_assessment: 'Ждёт оценку',
  processing: 'Сравнивается', cancelled: 'Остановлено', unknown: 'Статус неизвестен',
  checking: 'Проверяем сравнение…', unavailable: 'Не удалось проверить',
};

// Rolling releases may briefly return the preceding API. It may describe a
// closed room or a legacy result, but cannot prove a conducted interview.
export function workflowFor(p: SelectionPerson): Workflow {
  return p.workflow ?? {
    interview: { state: ({ booked: 'scheduled', live: 'open', completed: 'closed' } as Record<string, string>)[p.interviewStatus ?? ''] ?? (p.interviewStatus ? 'unknown' : 'not_scheduled') },
    resume: { state: p.resumeAvailable ? 'available' : 'missing' },
    assessment: { state: p.assessment?.stale ? 'stale' : p.assessment ? 'legacy' : 'not_started', origin: p.assessment ? 'engine-v1' : null },
    match: { state: p.assessment?.recommendation === 'not_matched' ? 'not_compared' : p.assessment?.recommendation ?? 'not_compared' },
  };
}

export function StateBadge({ state, labels }: { state: WorkflowState; labels: Record<string, string> }) {
  const tone = ['ready', 'ranked'].includes(state.state) ? 'sl-green'
    : ['error', 'failed_processing', 'unavailable'].includes(state.state) ? 'sl-red' : '';
  return <span className={'sl-status ' + tone}>{labels[state.state] ?? 'Статус неизвестен'}</span>;
}

export function ReviewState({ person, readiness, loading, error }: {
  person: SelectionPerson; readiness: Readiness | null; loading: boolean; error: string;
}) {
  const workflow = workflowFor(person);
  const assessment = readiness ? { state: readiness.assessment.state } : workflow.assessment;
  const sources = readiness?.materials;
  const result = readiness?.assessment.result;
  const coreRun = readiness?.assessment.coreRun;
  const showResult = !loading && !error && !['stale', 'unavailable', 'error', 'expired', 'cancelled'].includes(assessment.state);
  return <>
    <dl className="sl-readiness">
      <div><dt>HR-интервью</dt><dd><HRInterviewBadge person={readiness?.hrInterview ? { ...person, hrInterview: readiness.hrInterview } : person}/></dd>
        <small>Нужны запись и готовый основной текст всех частей HR-интервью.</small></div>
      <div><dt>Резюме</dt><dd>{person.resumeAvailable ? 'Есть в базе' : 'Не загружено'}</dd></div>
      <div><dt>Материалы</dt><dd><StateBadge state={{ state: loading ? 'loading' : error ? 'unavailable' : sources?.state ?? 'unknown' }} labels={materialLabels}/></dd>
        {sources && sources.recordings !== null && <small>Записей в каталоге: {sources.recordings} · основных текстов: {sources.transcripts}</small>}
        {sources?.items.map((item, i) => <small key={item.kind + item.id}>{item.kind === 'interview' ? 'Интервью' : 'Встреча'} {i + 1}: {item.recordings} записей · {({ ready: 'основной текст выбран', processing: 'текст обрабатывается', error: 'ошибка текста', not_requested: 'основной текст не выбран' } as Record<string, string>)[item.transcriptState] ?? 'статус текста неизвестен'}</small>)}</div>
      <div><dt>Оценка навыков</dt><dd><StateBadge state={loading ? { state: 'checking' } : error ? { state: 'unavailable' } : coreRun && showResult ? { state: coreRun.status } : assessment} labels={coreRun ? { ...assessmentLabels, ...fullCorpusStatusLabels } : assessmentLabels}/></dd>
        {assessment.state === 'stale' && <small>Материалы изменились. Предыдущие баллы не показаны как актуальные.</small>}
        {assessment.state === 'insufficient_data' && <small>В материалах недостаточно оснований для оценки. Это не нулевой балл.</small>}
        {assessment.state === 'not_started' && <small>Просмотр карточки не запускает расчёт.</small>}</div>
    </dl>
    {error && <p className="sl-error" role="alert">{error}</p>}
    {coreRun && showResult && <FullCorpusAssessment run={coreRun} candidateId={person.id}/>}
    {result && !coreRun && showResult && <section aria-label="Сохранённая оценка навыков">
      <div className="sl-criteria"><table><thead><tr><th>Параметр</th><th>Уровень</th><th>Основание</th></tr></thead><tbody>
        {result.criteria.map(c => <tr key={c.id}><td>{c.label}</td><td>{c.state === 'supported' && c.level !== null ? c.level : '—'}</td><td>{c.summary || c.missingReason || 'Недостаточно данных'}</td></tr>)}
      </tbody></table></div>
      <details><summary>Версия результата</summary><p className="sl-hash">{result.revisionId}</p></details>
    </section>}
  </>;
}

export function HRInterviewBadge({ person }: { person: SelectionPerson }) {
  const ready = hasHRInterview(person);
  const state = person.hrInterview?.state;
  const text = ready ? 'Проведено' : state === 'processing' ? 'Текст обрабатывается'
    : state === 'incomplete' || state === 'ready' ? 'Не все части готовы'
    : state === 'missing' ? 'Нет материалов' : 'Не подтверждено';
  return <span className={'sl-status ' + (ready ? 'sl-green' : '')}>{text}</span>;
}
