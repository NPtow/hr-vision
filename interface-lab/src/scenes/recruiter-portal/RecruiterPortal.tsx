import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Check, ChevronDown, UserRound } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../employer-workspace/employer.css';
import './recruiter.css';
import { formatDate, recruiterRequest, RecruiterRequestError, type RecruiterCandidate, type RecruiterState } from './api';
import { RecruiterCall, type ActiveCall } from './RecruiterCall';
import { RecruiterAuth } from './RecruiterAuth';
import { RecruiterProfile, type RecruiterChannels, type RecruiterChannelActionResult } from './RecruiterProfile';
import { InterviewList } from './InterviewList';
import { useRecruiterSession } from './session';
import { RecruiterMediaActions } from './RecruiterMediaActions';
import { InvitationControls } from './InvitationControls';

type SessionIdentity = { token: string; generation: number };

/** Personal interview queue. Ownership and calendar assignment are enforced by the API. */
export function RecruiterPortal() {
  const auth = useRecruiterSession();
  const token = auth.token;
  const identityRef = useRef<SessionIdentity>({ token, generation: 0 });
  if (identityRef.current.token !== token) identityRef.current = { token, generation: identityRef.current.generation + 1 };
  const identity = identityRef.current;
  const mounted = useRef(true);
  const [activationToken, setActivationToken] = useState(() => new URLSearchParams(location.search).get('recruiterActivate') || '');
  const [stateResult, setStateResult] = useState<{ identity: SessionIdentity; value: RecruiterState } | null>(null);
  // A new account must never render the preceding account's cached data, even
  // for the render before the token-change effect clears local controls.
  const state = stateResult?.identity === identity ? stateResult.value : null;
  const [channels, setChannels] = useState<RecruiterChannels>();
  const [page, setPage] = useState<'interviews' | 'profile'>('interviews');
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [callResult, setCallResult] = useState<{ identity: SessionIdentity; value: ActiveCall } | null>(null);
  const activeCall = callResult?.identity === identity ? callResult.value : null;
  const [completedOpen, setCompletedOpen] = useState(false);
  const [completedFocusId, setCompletedFocusId] = useState('');
  const completedSection = useRef<HTMLElement>(null);
  const sequence = useRef(0);
  const mutation = useRef(false);
  const verifyingEmail = useRef(false);
  const isCurrent = (captured: SessionIdentity) => mounted.current && identityRef.current === captured;
  const setActiveCall = (call: ActiveCall | null) => { if (isCurrent(identity)) setCallResult(call ? { identity, value: call } : null); };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; sequence.current++; };
  }, []);

  const refresh = useCallback(async () => {
    const captured = identityRef.current;
    if (!token || captured.token !== token || mutation.current || !mounted.current) return;
    const seq = ++sequence.current;
    try {
      const next = await recruiterRequest<RecruiterState>('state', token);
      if (isCurrent(captured) && seq === sequence.current) { setStateResult({ identity: captured, value: next }); setError(''); }
    } catch (cause) {
      if (!isCurrent(captured) || seq !== sequence.current) return;
      setError((cause as Error).message);
      if (cause instanceof RecruiterRequestError && cause.status === 401) auth.expire();
    }
  }, [token]);
  useEffect(() => {
    sequence.current++; mutation.current = false; verifyingEmail.current = false;
    setStateResult(null); setCallResult(null); setBusy(false); setSelectedId(''); setPage('interviews'); setError(''); setNotice('');
    setCompletedOpen(false); setCompletedFocusId('');
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 8000);
    return () => { clearInterval(timer); sequence.current++; };
  }, [refresh]);
  useEffect(() => {
    const captured = identityRef.current;
    let active = true;
    setChannels(undefined);
    if (!token) return;
    const load = async () => {
      try {
        const next = await recruiterRequest<RecruiterChannels>('notifications', token);
        if (active && isCurrent(captured)) setChannels(next);
      } catch (cause) { if (active && isCurrent(captured)) setError((cause as Error).message); }
    };
    void load();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 10000);
    return () => { active = false; clearInterval(timer); };
  }, [token]);
  useEffect(() => {
    const captured = identityRef.current;
    const verification = new URLSearchParams(location.search).get('recruiterVerify');
    if (!token || !verification || verifyingEmail.current) return;
    verifyingEmail.current = true;
    void recruiterRequest('notifications/email/verify', token, { token: verification }).then(async () => {
      if (!isCurrent(captured)) return;
      const next = await recruiterRequest<RecruiterChannels>('notifications', token);
      if (!isCurrent(captured)) return;
      const url = new URL(location.href); url.searchParams.delete('recruiterVerify'); history.replaceState(history.state, '', url);
      setChannels(next);
      setPage('profile'); setNotice('Почта подтверждена.');
    }).catch(cause => { if (isCurrent(captured)) setError((cause as Error).message); }).finally(() => { if (isCurrent(captured)) verifyingEmail.current = false; });
  }, [token]);

  useEffect(() => {
    if (!completedFocusId || !state?.candidates.some(candidate => candidate.id === completedFocusId && candidate.interview?.status === 'completed')) return;
    completedSection.current?.scrollIntoView({ block: 'start' });
    setCompletedFocusId('');
  }, [completedFocusId, state]);

  async function run(path: string, body: unknown) {
    const captured = identity;
    if (!isCurrent(captured) || mutation.current || !token) return false;
    mutation.current = true; sequence.current++; setBusy(true); setError(''); setNotice('');
    try {
      const next = await recruiterRequest<RecruiterState>(path, token, body);
      if (!isCurrent(captured)) return false;
      setStateResult({ identity: captured, value: next });
      return true;
    } catch (cause) {
      if (!isCurrent(captured)) return false;
      setError((cause as Error).message);
      if (cause instanceof RecruiterRequestError && cause.status === 401) auth.expire();
      return false;
    } finally { if (isCurrent(captured)) { mutation.current = false; setBusy(false); } }
  }
  async function channelAction(action: 'email' | 'telegram') {
    const captured = identity;
    if (!isCurrent(captured) || !token) return null;
    const next = await recruiterRequest<RecruiterChannelActionResult>(action === 'email' ? 'notifications/email/request' : 'notifications/telegram/link', token, {});
    if (!isCurrent(captured)) return null;
    const updated = await recruiterRequest<RecruiterChannels>('notifications', token);
    if (!isCurrent(captured)) return null;
    setChannels(updated);
    return next;
  }

  if (auth.loading) return <div className="ew-root rp-root"><main className="rp-access"><img src={logo} alt="HR Vision"/><p role="status">Открываем кабинет…</p></main></div>;
  if (activationToken || !auth.recruiter) return <RecruiterAuth busy={auth.busy} error={auth.error} activationToken={activationToken || undefined}
    onSubmit={async (_mode, values) => auth.authenticate('login', values)}
    onActivate={async (activation, password) => {
      if (!await auth.authenticate('activate', { token: activation, password })) return false;
      setActivationToken('');
      const url = new URL(location.href); url.searchParams.delete('recruiterActivate'); history.replaceState(history.state, '', url);
      return true;
    }}/>;

  const navigate = (next: 'interviews' | 'profile') => { setPage(next); setNotice(''); };
  const completed = (state?.candidates || []).filter(candidate => candidate.interview?.status === 'completed')
    .sort((a, b) => Date.parse(b.interview!.start) - Date.parse(a.interview!.start));
  const renderCandidateDetails = (candidate: RecruiterCandidate) => <CandidateDetails key={`${identity.generation}-${candidate.id}`} candidate={candidate} busy={busy} run={run} onNotice={message => { if (isCurrent(identity)) setNotice(message); }} token={token} onUpdated={refresh}/>;
  return <div className="ew-root rp-root">
    <div className="rp-shell" inert={activeCall ? true : undefined} aria-hidden={activeCall ? true : undefined}>
      <header className="ew-topbar rp-topbar">
        <button className="ew-logo" onClick={() => navigate('interviews')} aria-label="К интервью"><img src={logo} alt="HR Vision"/></button>
        <button className="rp-personal-account" onClick={() => navigate('profile')} aria-label="Открыть личный кабинет" aria-current={page === 'profile' ? 'page' : undefined}><UserRound size={18}/>{auth.recruiter.name}</button>
      </header>
      <main className="rp-main">
        <header className="rp-page-heading"><h1>{page === 'interviews' ? 'Интервью' : 'Личный кабинет'}</h1></header>
        {(error || auth.error) && <div className="rp-error" role="alert">{error || auth.error}</div>}
        {notice && <p className="rp-notice" role="status"><Check size={16}/>{notice}</p>}
        {!state ? <div className="rp-empty"><p>{error ? 'Данные не загрузились.' : 'Загружаем…'}</p>{error && <Button variant="secondary" onClick={() => void refresh()}>Повторить</Button>}</div> : <>
          {page === 'interviews' && <><InterviewList candidates={state.candidates} busy={busy} videoConfigured={state.videoConfigured}
            selectedId={selectedId} onDetails={id => setSelectedId(current => current === id ? '' : id)} onCall={setActiveCall}
            renderDetails={renderCandidateDetails}/>
            {!!completed.length && <section className="rp-completed-interviews" ref={completedSection} aria-label="Завершённые интервью">
              <button className="rp-completed-toggle" type="button" aria-expanded={completedOpen} aria-controls="rp-completed-list" onClick={() => setCompletedOpen(open => !open)}><ChevronDown size={16} aria-hidden="true"/>Завершённые интервью <span>{completed.length}</span></button>
              {completedOpen && <ul id="rp-completed-list" className="rp-completed-list">{completed.map(candidate => {
                const expanded = selectedId === candidate.id;
                const detailId = `rp-completed-${candidate.interview!.id}`;
                return <li key={candidate.interview!.id}>
                  <button type="button" className="rp-completed-row" aria-expanded={expanded} aria-controls={expanded ? detailId : undefined} onClick={() => setSelectedId(current => current === candidate.id ? '' : candidate.id)}>
                    <span><strong>{candidate.name || candidate.email || candidate.intake?.contactEmail || 'Кандидат'}</strong><span>{formatDate(candidate.interview!.start)}{candidate.source?.label ? ` · ${candidate.source.label}` : ''}</span></span><ChevronDown size={16} aria-hidden="true"/>
                  </button>
                  {expanded && <div id={detailId}>{renderCandidateDetails(candidate)}</div>}
                </li>;
              })}</ul>}
            </section>}
          </>}
          {page === 'profile' && <RecruiterProfile key={identity.generation} recruiter={auth.recruiter} blocks={state.availabilityBlocks || []} busy={busy || auth.busy} run={run} onLogout={() => { void auth.logout(); }} channels={channels} onChannelAction={channelAction}/ >}
        </>}
      </main>
    </div>
    {activeCall && <RecruiterCall key={`${identity.generation}-${activeCall.kind}-${activeCall.applicationId || activeCall.candidateId}`} active={{ ...activeCall, accountActive: state?.candidates.find(person => person.id === activeCall.candidateId)?.accountActive ?? activeCall.accountActive }} token={token} onClose={() => { setActiveCall(null); void refresh(); }} onJoined={() => void refresh()} onCompleted={() => {
      if (!isCurrent(identity)) return;
      setActiveCall(null); setPage('interviews'); setSelectedId(activeCall.candidateId); setCompletedOpen(true); setCompletedFocusId(activeCall.candidateId);
      setNotice('Интервью завершено.'); void refresh();
    }}/ >}
  </div>;
}

function CandidateDetails({ candidate, busy, run, onNotice, token, onUpdated }: {
  candidate: RecruiterCandidate; busy: boolean; run: (path: string, body: unknown) => Promise<boolean>; onNotice: (value: string) => void;
  token: string; onUpdated: () => void | Promise<void>;
}) {
  const [summary, setSummary] = useState(candidate.internalProfile?.summary || '');
  const [evaluation, setEvaluation] = useState(candidate.internalProfile?.evaluation || '');
  async function saveInternal(event: FormEvent) {
    event.preventDefault();
    if (await run('profile', { candidateId: candidate.id, summary, evaluation })) onNotice('Заметки сохранены.');
  }
  return <div className="rp-interview-profile">
    {candidate.interview?.status === 'completed' && <section className="rp-interview-results" aria-label="Результаты интервью">
      <RecruiterMediaActions kind="interview" candidateId={candidate.id} status={candidate.interview.status} token={token} onUpdated={onUpdated}/>
      {candidate.accountActive === false && <InvitationControls candidateId={candidate.id} scope="claim" token={token} onChanged={() => { void onUpdated(); }}/ >}
    </section>}
    <section className="rp-info-grid">
      <div><h3>О кандидате</h3><p>{[candidate.desiredRole, candidate.city].filter(Boolean).join(' · ')}</p>{candidate.about && <p className="rp-prewrap">{candidate.about}</p>}{candidate.resume && <p className="rp-muted">{candidate.resume.filename}</p>}</div>
      <div><h3>Контакты и предпочтения</h3><p>{candidate.email || candidate.intake?.contactEmail}</p>{candidate.telegram && <p>{candidate.telegram}</p>}<p>{candidate.geographies.join(', ') || candidate.city}</p>{candidate.workMode && <p className="rp-muted">{{ office: 'В офисе', hybrid: 'Гибрид', remote: 'Удалённо', any: 'Любой формат работы' }[candidate.workMode]}{candidate.desiredSalary ? ` · ${candidate.desiredSalary}` : ''}</p>}</div>
    </section>
    <details className="rp-interview-notes"><summary>Заметки рекрутера</summary><form className="rp-internal" onSubmit={saveInternal}>
      <label>Краткое резюме<textarea value={summary} onChange={e => setSummary(e.target.value)} maxLength={12000} rows={3}/></label>
      <label>Оценка и заметки<textarea value={evaluation} onChange={e => setEvaluation(e.target.value)} maxLength={12000} rows={3}/></label>
      <Button variant="secondary" disabled={busy}>Сохранить</Button>
    </form></details>
  </div>;
}
