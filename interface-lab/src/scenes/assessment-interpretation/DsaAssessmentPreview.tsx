import { useEffect, useState } from 'react';
import { MotionConfig } from 'motion/react';
import { Dialog, Tooltip } from 'radix-ui';
import { ArrowLeft, ArrowUpRight, ChevronDown, ClipboardList, FileText, RotateCcw, Users, X } from 'lucide-react';
import { RadarCandidatePreview, type AssessmentChapter, type AssessmentTurn } from './RadarCandidatePreview';
import { AssessmentProfiles, type AssessmentProfileVariant } from './profiles/ProfileVariants';
import { skillName } from './radar/AssessmentRadar';
import type { AssessmentCriterion, AssessmentEvidence, CandidateAssessmentView } from './assessment-view';
import { graphValue, type AssessmentGraphVariant } from './explorations/graph-contract';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../employer-workspace/fonts.css';
import '../employer-workspace/employer-candidate-profile.css';
import './assessment-interpretation.css';
import './dsa-assessment.css';

type DsaMethodUnit = {
  episodeId: string;
  sourceId: string;
  evidenceClass: string;
  classAnswer?: { choice?: string; confidence?: number; probabilities?: Record<string, number> };
  relevanceProbability?: number;
  rawLevel?: number | null;
  displayLevel?: number | null;
  matchedActions?: { action_id: string; text_ru: string; probability: number }[];
  matchedConcerns?: { action_id: string; text_ru: string; probability: number }[];
  actionProbabilities?: Record<string, number>;
  actionLabels?: Record<string, string>;
};
type DsaAssessmentCriterion = AssessmentCriterion & {
  methodDetails?: { units?: DsaMethodUnit[]; previewPolicy?: string; anchorStatus?: string | null; model?: string | null };
};

export type DsaAssessmentPayload = {
  vacancy: {
    id: string;
    title: string;
    company: string;
    text: string;
    selectedSkills: { id: string; label: string; importance?: number; reason?: string }[];
  };
  candidates: {
    id: string;
    assessment: Omit<CandidateAssessmentView, 'criteria'> & { criteria: DsaAssessmentCriterion[] };
    turns: AssessmentTurn[];
    media: { src: string };
    resumeUrl?: string;
    chapters: AssessmentChapter[];
    provenance: { model: string; method: string; sourceRole: string; interviewDate: string; methodDetails?: string };
  }[];
  generatedAt: string;
};

const graphVariants: { id: AssessmentGraphVariant; label: string }[] = [
  { id: 'orbit', label: 'Орбита' }, { id: 'matrix', label: 'Матрица' },
];

const ENDPOINT = 'http://127.0.0.1:6041/assessment';
const number = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value) : '—';
const classes: Record<string, string> = { observed: 'Показанное действие', narrated: 'Конкретный прошлый опыт', partial_narrative: 'Неполный рассказ о случае', generic: 'Общее заявление', assisted: 'Ответ с подсказкой', none: 'Нет подходящего действия' };

function MethodEvidence({ criteria }: { criteria: DsaAssessmentCriterion[] }) {
  return <div className="dsa-method-evidence"><h3>Оценка и основания</h3><p>Балл отражает подход, описанный в выбранных источниках. Уверенность JEV характеризует распределение модели и не является вероятностью правильности оценки. В деталях сохранены и предыдущие проверки метода.</p>{criteria.filter(criterion => criterion.evidence.length > 0).map(criterion => <details className="dsa-criterion-method" key={criterion.id}><summary><strong>{skillName(criterion)}</strong><span>{graphValue(criterion) === null ? '—' : `${number(graphValue(criterion))} / 5`}</span></summary>{criterion.modelEstimate && <section className="dsa-unit-method"><dl><dt>Прямой балл JEV</dt><dd>{number(criterion.modelEstimate.value)} / 5</dd><dt>Уверенность JEV</dt><dd>{number(criterion.modelEstimate.confidence * 100)}%</dd><dt>Распределение уровней 1–5</dt><dd>{criterion.modelEstimate.probabilities.map(value => `${number(value * 100)}%`).join(' · ')}</dd></dl></section>}{criterion.methodDetails?.units?.length ? criterion.methodDetails.units.map((unit, index) => {
    const matched = [...unit.matchedActions ?? [], ...unit.matchedConcerns ?? []];
    const names = { ...unit.actionLabels, ...Object.fromEntries(matched.map(item => [item.action_id, item.text_ru])) };
    return <section className="dsa-unit-method" key={unit.episodeId}><h4>Фрагмент {index + 1}</h4><dl><dt>Тип свидетельства</dt><dd>{classes[unit.evidenceClass] || unit.evidenceClass}</dd><dt>Уверенность выбора типа JEV</dt><dd>{number(unit.classAnswer?.confidence)}</dd><dt>Связь реплики с навыком JEV</dt><dd>{number(unit.relevanceProbability)}</dd><dt>Уровень после правил метода</dt><dd>{unit.displayLevel == null ? 'Без числовой оценки' : `${number(unit.displayLevel)} / 5`}</dd></dl>{matched.length > 0 && <table><thead><tr><th>Подтверждённый признак</th><th>Поддержка признака JEV</th></tr></thead><tbody>{matched.map(action => <tr key={action.action_id}><td>{action.text_ru}</td><td>{number(action.probability)}</td></tr>)}</tbody></table>}{unit.actionProbabilities && Object.keys(unit.actionProbabilities).length > 0 && <details className="dsa-all-checks"><summary>Все проверки признаков</summary><table><thead><tr><th>Признак матрицы</th><th>Поддержка признака JEV</th></tr></thead><tbody>{Object.entries(unit.actionProbabilities).map(([id, value]) => <tr key={id}><td>{names[id] || id}</td><td>{number(value)}</td></tr>)}</tbody></table></details>}</section>;
  }) : <p>{criterion.missingReason === 'no_presale_anchors' ? 'В матрице нет шкалы уровня для этого навыка.' : 'Подробные проверки для этого навыка не переданы.'}</p>}</details>)}</div>;
}

/** Private candidate data is loaded at runtime from a loopback-only service, never bundled. */
export function DsaAssessmentPreview() {
  const [payload, setPayload] = useState<DsaAssessmentPayload | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState('');
  const [page, setPage] = useState<'card' | 'profile'>('card');
  const [profile, setProfile] = useState<AssessmentProfileVariant>('brief');
  const [graph, setGraph] = useState<AssessmentGraphVariant>(() => {
    const requested = new URLSearchParams(window.location.search).get('graph');
    return graphVariants.find(item => item.id === requested)?.id ?? 'orbit';
  });
  const selectGraph = (id: AssessmentGraphVariant) => {
    setGraph(id);
    const url = new URL(window.location.href); url.searchParams.set('graph', id);
    window.history.replaceState(null, '', url);
  };
  const [dialog, setDialog] = useState<'vacancy' | 'method' | null>(null);
  const [source, setSource] = useState<AssessmentEvidence | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    setError('');
    setPayload(null);
    fetch(ENDPOINT, { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error(`Сервис данных ответил ${response.status}.`);
        const value = await response.json() as DsaAssessmentPayload;
        if (!value.vacancy?.title || !Array.isArray(value.candidates) || !value.candidates.length || value.candidates.some(candidate => !candidate.assessment?.candidate?.name || !Array.isArray(candidate.turns) || !Array.isArray(candidate.assessment.criteria))) {
          throw new Error('Сервис ещё не подготовил вакансию и кандидатов.');
        }
        if (controller.signal.aborted) return;
        setPayload(value);
        setSelected(id => value.candidates.some(candidate => candidate.id === id) ? id : value.candidates[0].id);
      })
      .catch(reason => {
        if (controller.signal.aborted && !timedOut) return;
        setError(timedOut ? 'Локальный сервис не ответил за 15 секунд.' : reason instanceof Error ? reason.message : 'Не удалось загрузить данные.');
      })
      .finally(() => window.clearTimeout(timeout));
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [reload]);

  const candidate = payload?.candidates.find(item => item.id === selected) ?? payload?.candidates[0];
  const selectCandidate = (id: string) => { setSelected(id); setSource(null); };
  const openSource = (evidence: AssessmentEvidence) => { setSource(evidence); setPage('card'); };

  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={650}>
    <div className="aip-preview dsa-preview">
      <div className="aip-labbar">
        <div className="aip-lab-group" aria-label="Вид кандидата"><button aria-pressed={page === 'card'} onClick={() => setPage('card')}>Карточка</button><button aria-pressed={page === 'profile'} onClick={() => setPage('profile')}>Профиль</button></div>
        {page === 'card' && <nav className="dsa-graph-options" aria-label="Вариант графика">{graphVariants.map(item => <button key={item.id} aria-current={graph === item.id ? 'page' : undefined} onClick={() => selectGraph(item.id)}>{item.label}</button>)}</nav>}
        {page === 'profile' && <nav aria-label="Вариант профиля">{([{ id: 'brief', label: 'Кратко' }, { id: 'evidence', label: 'Основания' }, { id: 'plan', label: 'Следующая встреча' }] as const).map(item => <button key={item.id} aria-current={profile === item.id ? 'page' : undefined} onClick={() => setProfile(item.id)}>{item.label}</button>)}</nav>}
        <span>Реальные интервью · Локальный макет</span>
        <button className="dsa-method-trigger" onClick={() => setDialog('method')} disabled={!candidate}>Оценка JEV</button>
        <button className="aip-lab-reset" aria-label="Обновить данные ДСА" onClick={() => { setSource(null); setReload(value => value + 1); }}><RotateCcw size={14}/></button>
      </div>
      <header className="aip-brand"><img src={logo} alt="HR Vision"/><span>Никита<ChevronDown size={14}/></span></header>
      {!payload || !candidate ? <main className="dsa-loading" aria-live="polite"><h1>{error ? 'Данные пока недоступны' : 'Загружаем интервью ДСА'}</h1><p>{error || 'Вакансия, расшифровки и оценки из локального сервиса.'}</p>{error && <><p>Проверьте, что локальный сервис на порту 6041 запущен.</p><button className="aip-primary" onClick={() => setReload(value => value + 1)}>Попробовать снова</button></>}</main> :
        <div className="aip-cabinet"><nav className="aip-rail" aria-label="Разделы кабинета"><span><ClipboardList size={21}/></span><span className="aip-rail-selected"><Users size={21}/></span><span><FileText size={20}/></span></nav>
          <div className="aip-content dsa-content">
            <div className="aip-vacancy dsa-vacancy"><div><h1>{payload.vacancy.title}</h1><span>{payload.vacancy.company}</span></div><div className="dsa-vacancy-actions"><button onClick={() => setDialog('vacancy')}><FileText size={14}/>ТЗ вакансии</button>{page === 'profile' ? <button onClick={() => setPage('card')}><ArrowLeft size={14}/>К интервью</button> : <button onClick={() => setPage('profile')}>Профиль<ArrowUpRight size={14}/></button>}</div></div>
            <nav className="dsa-candidates" aria-label="Кандидаты ДСА">{payload.candidates.map(item => <button key={item.id} aria-current={selected === item.id ? 'page' : undefined} onClick={() => selectCandidate(item.id)}>{item.assessment.candidate.name}</button>)}</nav>
            {page === 'card' ? <RadarCandidatePreview key={`${candidate.id}:${candidate.assessment.revisionId}`} data={candidate.assessment} turns={candidate.turns} mediaSrc={candidate.media?.src ?? ''} resumeUrl={candidate.resumeUrl ?? `http://127.0.0.1:6041/resume/${encodeURIComponent(candidate.id)}.pdf`} chapters={candidate.chapters ?? []} realData analysisInSidebar graphVariant={graph} initialEvidence={source} onProfile={() => setPage('profile')}/> : <div className="aip-profile-scroll"><AssessmentProfiles variant={profile} data={candidate.assessment} persistFeedback realData resumeUrl={candidate.resumeUrl ?? `http://127.0.0.1:6041/resume/${encodeURIComponent(candidate.id)}.pdf`} onSource={openSource}/></div>}
          </div>
        </div>}
    </div>
    <Dialog.Root open={dialog !== null} onOpenChange={open => { if (!open) setDialog(null); }}><Dialog.Portal><Dialog.Overlay className="aip-scrim"/><Dialog.Content className="aip-dialog dsa-document"><header><Dialog.Title>{dialog === 'vacancy' ? payload?.vacancy.title : 'Как получена оценка'}</Dialog.Title><Dialog.Close className="aip-icon" aria-label="Закрыть"><X size={19}/></Dialog.Close></header><Dialog.Description>{dialog === 'vacancy' ? payload?.vacancy.company : 'Интерпретация интервью. Решение о кандидате принимает человек.'}</Dialog.Description>{dialog === 'vacancy' ? <div className="dsa-vacancy-text">{payload?.vacancy.text}</div> : <div className="dsa-method"><dl><dt>Модель</dt><dd>{candidate?.provenance.model}</dd><dt>Метод</dt><dd>JEV Score · профессиональные источники · шкала 1–5</dd><dt>Исходная роль интервью</dt><dd>{candidate?.provenance.sourceRole}</dd><dt>Дата интервью</dt><dd>{candidate?.provenance.interviewDate}</dd></dl><p>На графиках — прямая оценка JEV по шкале 1–5 для выбранных профессиональных фрагментов. Балл и уверенность показаны раздельно. Прежние пороги допуска к отображению не применяются.</p>{candidate?.assessment.criteria.some(criterion => criterion.id === 'NEW-01' && criterion.modelEstimate) && <p>Для «Передача лида» используется отдельная черновая шкала макета. Для остальных навыков взяты описания уровней из ядра.</p>}{candidate && <MethodEvidence criteria={candidate.assessment.criteria}/>}<h3>Параметры этой вакансии</h3><ol>{payload?.vacancy.selectedSkills.map(skill => <li key={skill.id}><strong>{skillName(skill)}</strong>{skill.reason && <p>{skill.reason}</p>}</li>)}</ol><p>Оценка навыка и уверенность модели — разные показатели. Источники открывают исходную реплику. Ваше мнение сохраняется только в этом браузере, отдельно от результата модели.</p><p>Кнопки приглашения и отказа показывают механику макета. Кандидатам ничего не отправляется.</p></div>}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </Tooltip.Provider></MotionConfig>;
}
