import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, UserRound } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../employer-workspace/employer.css';
import './recruiter.css';
import { recruiterRequest, RecruiterRequestError, type RecruiterCandidate, type RecruiterState } from './api';
import { RecruiterCall, type ActiveCall } from './RecruiterCall';
import { RecruiterAuth } from './RecruiterAuth';
import { RecruiterProfile } from './RecruiterProfile';
import { InterviewList } from './InterviewList';
import { useRecruiterSession } from './session';
import { RecruiterCandidateDetails } from './RecruiterCandidateDetails';
import { RecruiterInterviewWorkspace } from './RecruiterInterviewWorkspace';
import { startZoomSessionReconcile } from '../../components/call/zoom-session-reconcile';

type SessionIdentity = { token: string; generation: number };

/** Personal interview queue. Ownership and calendar assignment are enforced by the API. */
export function RecruiterPortal() {
  return <RecruiterWorkspace/>;
}

function RecruiterWorkspace() {
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
  const [page, setPage] = useState<'interviews' | 'profile'>('interviews');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [callResult, setCallResult] = useState<{ identity: SessionIdentity; value: ActiveCall } | null>(null);
  const activeCall = callResult?.identity === identity ? callResult.value : null;
  const [completedOpen, setCompletedOpen] = useState(false);
  const [completedFocusId, setCompletedFocusId] = useState('');
  const completedSection = useRef<HTMLElement>(null);
  const cancelZoomReconcile = useRef<(() => void) | null>(null);
  const sequence = useRef(0);
  const mutation = useRef(false);
  const verifyingEmail = useRef(false);
  const isCurrent = (captured: SessionIdentity) => mounted.current && identityRef.current === captured;
  const setActiveCall = (call: ActiveCall | null) => { if (isCurrent(identity)) setCallResult(call ? { identity, value: call } : null); };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; sequence.current++; cancelZoomReconcile.current?.(); };
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
    cancelZoomReconcile.current?.();
    sequence.current++; mutation.current = false; verifyingEmail.current = false;
    setStateResult(null); setCallResult(null); setBusy(false); setPage('interviews'); setError(''); setNotice('');
    setCompletedOpen(false); setCompletedFocusId('');
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 8000);
    return () => { clearInterval(timer); sequence.current++; };
  }, [refresh]);
  useEffect(() => {
    const captured = identityRef.current;
    const verification = new URLSearchParams(location.search).get('recruiterVerify');
    if (!token || !verification || verifyingEmail.current) return;
    verifyingEmail.current = true;
    void recruiterRequest('notifications/email/verify', token, { token: verification }).then(async () => {
      if (!isCurrent(captured)) return;
      const url = new URL(location.href); url.searchParams.delete('recruiterVerify'); history.replaceState(history.state, '', url);
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
  function openCandidate(candidate: RecruiterCandidate, materials = false) {
    cancelZoomReconcile.current?.();
    setActiveCall({ kind: 'interview', callProvider: candidate.interview!.callProvider || 'daily',
      candidateId: candidate.id, interviewId: candidate.interview!.id, title: candidate.name,
      candidate, recording: candidate.interview!.recording,
      preview: materials || candidate.interview!.callProvider !== 'zoom' || candidate.interview!.status === 'completed' });
  }
  function markCompleted(candidateId: string) {
    if (!isCurrent(identity)) return;
    setActiveCall(null); setPage('interviews'); setCompletedOpen(true); setCompletedFocusId(candidateId);
    setNotice('Интервью завершено.'); void refresh();
  }
  function reconcileZoom() {
    if (!activeCall) return;
    const captured = identity, call = activeCall;
    const body = call.kind === 'interview' ? { candidateId: call.candidateId, interviewId: call.interviewId }
      : { candidateId: call.candidateId, applicationId: call.applicationId, meetingId: call.meetingId };
    cancelZoomReconcile.current?.();
    cancelZoomReconcile.current = startZoomSessionReconcile({
      request: signal => recruiterRequest(`${call.kind}/reconcile`, token, body, signal),
      onCompleted: () => { if (isCurrent(captured)) markCompleted(call.candidateId); },
    });
  }
  const renderCandidateDetails = (candidate: RecruiterCandidate) => <RecruiterCandidateDetails key={`${identity.generation}-${candidate.id}`} candidate={candidate} token={token}
    onOpen={() => openCandidate(candidate)} onMaterials={() => openCandidate(candidate, true)}
    onProvider={provider => run('interview/provider', { candidateId: candidate.id, provider })}/>;
  return <div className="ew-root rp-root">
    <div className="rp-shell" inert={activeCall ? true : undefined} aria-hidden={activeCall ? true : undefined}>
      <header className="ew-topbar rp-topbar">
        <button className="ew-logo" onClick={() => navigate('interviews')} aria-label="К интервью"><img src={logo} alt="HR Vision"/></button>
        <button className="rp-personal-account" onClick={() => navigate('profile')} aria-label="Открыть личный кабинет" aria-current={page === 'profile' ? 'page' : undefined}><UserRound size={18}/>{auth.recruiter.name}</button>
      </header>
      <main className="rp-main" aria-labelledby="rp-page-title">
        {page === 'profile' ? <header className="rp-page-heading"><h1 id="rp-page-title">Личный кабинет</h1></header> : <h1 id="rp-page-title" className="rp-visually-hidden">Интервью</h1>}
        {(error || auth.error) && <div className="rp-error" role="alert">{error || auth.error}</div>}
        {notice && <p className="rp-notice" role="status"><Check size={16}/>{notice}</p>}
        {!state ? <div className="rp-empty"><p>{error ? 'Данные не загрузились.' : 'Загружаем…'}</p>{error && <Button variant="secondary" onClick={() => void refresh()}>Повторить</Button>}</div> : <>
          {page === 'interviews' && <><InterviewList candidates={state.candidates} renderDetails={renderCandidateDetails}/>
            {!!completed.length && <section className="rp-completed-interviews" ref={completedSection} aria-label="Завершённые интервью">
              <button className="rp-completed-toggle" type="button" aria-expanded={completedOpen} aria-controls="rp-completed-list" onClick={() => setCompletedOpen(open => !open)}><ChevronDown size={16} aria-hidden="true"/>Завершённые интервью <span>{completed.length}</span></button>
              {completedOpen && <ul id="rp-completed-list" className="rp-completed-list">{completed.map(candidate => <li key={candidate.interview!.id}>{renderCandidateDetails(candidate)}</li>)}</ul>}
            </section>}
          </>}
          {page === 'profile' && <RecruiterProfile key={identity.generation} token={token} onError={setError} recruiter={auth.recruiter} blocks={state.availabilityBlocks || []} dates={state.availabilityDates || []} slots={state.slots} busy={busy || auth.busy} run={run} onLogout={() => { void auth.logout(); }}/ >}
        </>}
      </main>
    </div>
    {activeCall?.preview && activeCall.candidate ? <RecruiterInterviewWorkspace
      key={`${identity.generation}-${activeCall.interviewId}`} candidate={state?.candidates.find(person => person.id === activeCall.candidateId) || activeCall.candidate}
      token={token} videoConfigured={state?.videoConfigured || false} materialsOnly={activeCall.callProvider === 'zoom'} onClose={() => setActiveCall(null)} onUpdated={refresh}
      onJoin={(callProvider, recordingConsent) => setActiveCall({ ...activeCall, callProvider, recordingConsent, preview: false })}/>
      : activeCall && <RecruiterCall key={`${identity.generation}-${activeCall.kind}-${activeCall.interviewId || activeCall.meetingId || activeCall.applicationId || activeCall.candidateId}`} active={{ ...activeCall, accountActive: state?.candidates.find(person => person.id === activeCall.candidateId)?.accountActive ?? activeCall.accountActive }} token={token} onClose={() => { setActiveCall(null); void refresh(); }} onJoined={() => void refresh()} onZoomHostLeft={reconcileZoom} onCompleted={() => markCompleted(activeCall.candidateId)}/ >}
  </div>;
}
