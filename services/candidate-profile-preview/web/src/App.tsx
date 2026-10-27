import { useEffect, useRef, useState } from 'react';
import { getDirectory, getReadiness, getInterviews, signIn, isAuthError } from './api';
import type { Candidate } from './types';
import { ProfileHeader } from './components/ProfileHeader';
import { normalizeAssessment } from './assessment-model';
import { ProfileFacts } from './components/ProfileFacts';
import { InterviewPanel, type InterviewPosition } from './components/InterviewPanel';
import { SkillsMap } from './components/SkillsMap';

const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось получить данные.';
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};

export default function App() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [activeSkill, setActiveSkill] = useState('');
  const [fullInterview, setFullInterview] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const interviewPosition = useRef<InterviewPosition | undefined>(undefined);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadedRevision, setLoadedRevision] = useState(-1);
  const directoryReady = loadedRevision === revision && !loading;
  const [error, setError] = useState('');
  const [auth, setAuth] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [details, setDetails] = useState<{ id: string; readiness?: unknown; interviews?: unknown; errors: string[]; loading: boolean }>({ id: '', errors: [], loading: false });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(''); setAuth(false);
    getDirectory(controller.signal).then(directory => {
      if (!active) return;
      setCandidates(directory.candidates);
      setLoadedRevision(revision);
      setSelectedId(previous => directory.candidates.some(candidate => candidate.id === previous) ? previous : directory.candidates[0]?.id ?? '');
    }).catch(reason => {
      if (!active || controller.signal.aborted) return;
      setCandidates([]); setSelectedId(''); setError(message(reason)); setAuth(isAuthError(reason));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setDetails({ id: selectedId, errors: [], loading: Boolean(selectedId) });
    if (directoryReady && selectedId) Promise.allSettled([getReadiness(selectedId, controller.signal), getInterviews(selectedId, controller.signal)]).then(results => {
      if (!active || controller.signal.aborted) return;
      const errors: string[] = [];
      for (const result of results) if (result.status === 'rejected') {
        errors.push(message(result.reason));
        if (isAuthError(result.reason)) { setAuth(true); setCandidates([]); setSelectedId(''); }
      }
      setDetails({ id: selectedId, readiness: results[0].status === 'fulfilled' ? results[0].value : undefined, interviews: results[1].status === 'fulfilled' ? results[1].value : undefined, errors, loading: false });
    });
    return () => { active = false; controller.abort(); };
  }, [selectedId, revision, directoryReady]);

  const selected = candidates.find(candidate => candidate.id === selectedId);
  const current = details.id === selectedId ? details : { loading: true, errors: [], readiness: undefined, interviews: undefined };
  const rawAssessment = record(current.readiness).assessment;
  const assessment = rawAssessment ? { ...record(rawAssessment), candidateId: selectedId } : undefined;
  const model = normalizeAssessment(assessment);
  const selectedSkill = model.state === 'ready' ? activeSkill ? model.criteria.find(criterion => criterion.id === activeSkill) : model.criteria.find(criterion => criterion.id === 'A06.1') ?? model.criteria[0] : undefined;
  useEffect(() => { setFullInterview(false); setActiveSkill(''); interviewPosition.current = undefined; }, [selectedId]);
  useEffect(() => {
    if (!fullInterview) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { dialog.current?.close(); previous?.focus(); };
  }, [fullInterview]);
  function handleMaterialError(_message: string, reason?: unknown) {
    if (isAuthError(reason)) {
      setCandidates([]); setSelectedId(''); setDetails({ id: '', errors: [], loading: false }); setAuth(true);
    }
  }
  async function handleSignIn() {
    setSigningIn(true); setError('');
    try { await signIn(); setRevision(value => value + 1); }
    catch (reason) { setError(message(reason)); }
    finally { setSigningIn(false); }
  }

  return <div className="cp-app cp-explore">
    <header className="cp-topbar cp-explore-topbar">
      <img className="cp-logo" src="/candidate-preview/media/candidate-profile-directions/hr-vision-logo.svg" alt="HR Vision" width="158" />
      <label className="cp-person-select"><span>Кандидат</span><select aria-label="Выбрать кандидата" value={selectedId} disabled={!directoryReady || !candidates.length} onChange={event => setSelectedId(event.target.value)}>
        {!candidates.length && <option value="">{loading ? 'Загрузка…' : 'Нет доступных кандидатов'}</option>}
        {candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name || 'Имя не указано'}</option>)}
      </select></label>
      <button className="cp-button" disabled={loading || signingIn} onClick={() => setRevision(value => value + 1)}>Обновить</button>
    </header>
    <main className="cp-main cp-explore-main">
      {error && <p className="cp-error" role="alert">{error}</p>}
      {auth ? <section className="cp-auth"><h1>Войдите как менеджер</h1><p>Для просмотра нужен действующий доступ HR Vision. Вход использует приглашение в этом браузере.</p><button className="cp-button" disabled={signingIn} onClick={handleSignIn}>{signingIn ? 'Входим…' : 'Войти через HR Vision'}</button></section> : !directoryReady && !error ? <p className="cp-empty" role="status">Загружаем кандидатов…</p> : directoryReady && selected ? <>
        <ProfileHeader candidate={selected} readiness={current.readiness} />
        {current.errors.map((text, index) => <p key={index} className="cp-error" role="alert">{text}</p>)}
        <div className="cp-profile-grid cp-skills-layout">
          <aside className="cp-about-column"><ProfileFacts candidate={selected} /></aside>
          <div className="cp-skills-center">{current.loading ? <p role="status">Получаем сохранённую оценку…</p> : <SkillsMap assessment={assessment} candidateName={selected.name} selectedId={activeSkill || selectedSkill?.id} onSelect={setActiveSkill} />}</div>
          <aside className="cp-detail-column">
            {selectedSkill ? <section className="cp-selected-skill" aria-label="Выбранный навык"><h2 className="cp-selected-skill-title">{selectedSkill.title}</h2><p className="cp-selected-level">{selectedSkill.selectedLevel ? `${selectedSkill.levels.findIndex(level => level.id === selectedSkill.selectedLevel?.id) + 1}/5 · ${selectedSkill.selectedLevel.label}` : 'Недостаточно данных'}</p><p className="cp-selected-description">{selectedSkill.selectedLevel?.description || 'В материалах недостаточно данных для выбора уровня этого навыка.'}</p></section> : <p className="cp-empty">{current.loading ? 'Получаем данные…' : model.state !== 'ready' ? model.message : activeSkill ? 'В сохранённой оценке нет данных для выбранного навыка.' : 'Выберите навык на карте.'}</p>}
            <section className="cp-interview-preview"><p className="cp-interview-context">Запись целиком; фрагмент к навыку не привязан.</p>{current.loading ? <p role="status">Получаем интервью…</p> : current.interviews !== undefined ? !fullInterview && <InterviewPanel key={selected.id} candidateId={selected.id} interviews={current.interviews} initialPosition={interviewPosition.current} onPositionChange={position => { interviewPosition.current = position; }} compact onExpand={() => setFullInterview(true)} onError={handleMaterialError} /> : <p className="cp-empty">Интервью недоступны. Обновите данные, чтобы повторить запрос.</p>}</section>
          </aside>
        </div>
        {fullInterview && <dialog className="cp-interview-dialog" ref={dialog} aria-labelledby="cp-full-interview-title" onCancel={() => setFullInterview(false)}><header className="cp-dialog-heading"><h2 id="cp-full-interview-title">HR-интервью · {selected.name || 'Кандидат'}</h2><button type="button" className="cp-button" onClick={() => setFullInterview(false)} aria-label="Закрыть полное интервью">Закрыть</button></header><div className="cp-dialog-body"><InterviewPanel key={selected.id} candidateId={selected.id} interviews={current.interviews} initialPosition={interviewPosition.current} onPositionChange={position => { interviewPosition.current = position; }} onError={handleMaterialError} /></div></dialog>}
      </> : !error && <p className="cp-empty">Доступных кандидатов пока нет.</p>}
    </main>
  </div>;
}
