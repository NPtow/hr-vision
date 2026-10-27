import { useCallback, useEffect, useRef, useState } from 'react';
import { loadEmployerTelegram, prepareEmployerTelegram, restoreEmployerIdentity } from '../../lib/telegram-employer';
import type { RecordingChapter } from '../interview-review/InterviewReview';
import type { CandidateStage } from '../employer-workspace/candidate-stage-types';
export type Scope = 'sfera' | 'dsa';
export type CandidateId = string;
export type Actor = CandidateId | 'manager';
export type AccountList = { scope: Scope; company: string; accounts: { id: Actor; name: string; role: string }[] };
export type SourceProfile = {
  id: string; name: string; initials: string; role: string; interviewRole: string; interviewDate: string;
  score: number | null; duration: number | null; photo: string | null; video: string | null;
  shortChapters: RecordingChapter[]; fullChapters: RecordingChapter[]; shortSummary: string;
  profile: { label: string; value: string }[]; cv: string; conclusion: string[]; questions: string[];
  interviewId?: string | null;
  contacts?: { kind: 'email' | 'phone' | 'telegram'; value: string }[];
  resume?: { filename: string; mime: string; size: number; uploadedAt: string } | null;
};
export type ScreeningComment = { id: string; interviewId: string; recordingId: string | null; seconds: number; chapterTitle?: string; text: string; createdAt: string };
export type RejectionAnswers = { reason: string; missing: string; improve: string };
export type EmployerReviewResponse = { decision: 'invite' | 'reject'; createdAt: string;
  meetingStart?: string; meetingStatus?: 'draft' | 'pending' | 'confirmed' | 'live' | 'completed' | 'cancelled' | 'declined' };
export type EmployerReviewReceipt = EmployerReviewResponse & {
  source: 'application'; id: string; applicationId: string; candidateId: string;
  vacancyId: string; name: string; reviewRejection?: (RejectionAnswers & { createdAt?: string }) | null;
};
export type Offer = { id?: string; createdAt?: string; status: 'draft' | 'sent' | 'accepted' | 'declined' | 'withdrawn' | 'superseded'; role: string; compensation: string; format: string; expectations: string; startDate: string; version: number };
export type FeedbackQuestion = { id: string; question: string; seconds: number | null; source: 'live' | 'transcript' | 'manual' };
export type FeedbackAnswer = { rating: 'clear' | 'unclear' | 'unanswered' | 'not_asked'; comment: string };
export type Questionnaire = { id: string; meetingId: string; questions: FeedbackQuestion[] };
export type Meeting = { id: string; revision?: number; start: string; end?: string; startedAt?: string; endedAt?: string; status: 'pending' | 'confirmed' | 'live' | 'cancelled' | 'completed'; testSkip: boolean; joined: Actor[]; questionnaire?: Questionnaire | null; feedbackDraft?: Record<string, FeedbackAnswer> };
export type Person = { id: CandidateId; candidateId?: string; applicationId?: string; vacancyId?: string; vacancyRole?: string; reviewState?: string; name: string; role?: string; summary?: string; photo?: string | null; sourceProfile?: SourceProfile; interest: 'pending' | 'accepted' | 'declined'; auto: boolean; slots?: string[]; busy?: string[]; freeSlots: string[]; meeting: Meeting | null; meetings?: Meeting[]; offers?: Offer[]; stages?: CandidateStage[]; workflowRevision?: number; reviewResponse?: EmployerReviewResponse | null; screeningComments?: ScreeningComment[]; reviewRejection?: (RejectionAnswers & { createdAt?: string }) | null; feedback?: { example?: string; doubts?: string; check?: string; at: string; questions?: FeedbackQuestion[]; answers?: Record<string, FeedbackAnswer> } | null; afterInterest: 'pending' | 'yes' | 'no'; decision: 'review' | 'pool' | 'declined'; offer: Offer | null; messages: { id: string; actor: string; text: string; at: string; system: boolean }[] };
export type Vacancy = { id?: string; company: string; role: string; problem: string; salary: string; format: string; expectations: string; proposedCount?: number; selectedCount?: number };
export type EmployerVacancyDetails = {
  address: string;
  employmentType: '' | 'full-time' | 'part-time' | 'project' | 'internship' | 'temporary';
  schedule: string;
  salaryMin: number | null;
  salaryMax: number | null;
  currency: '' | 'RUB' | 'USD' | 'EUR' | 'KZT' | 'BYN';
  salaryPeriod: '' | 'month' | 'year' | 'hour' | 'shift';
  salaryTax: '' | 'gross' | 'net';
  responsibilities: string;
  requirements: string;
  niceToHave: string;
  benefits: string;
  experience: string;
};
export type EmployerVacancyValues = {
  title: string; company: string; city: string; remote: boolean; format: string;
  salary: string; description: string; details: EmployerVacancyDetails; importId?: string;
};
export type EmployerVacancyImportInput =
  | { kind: 'file'; filename: string; contentBase64: string }
  | { kind: 'url'; url: string; source: 'hh' | 'superjob' | 'other' }
  | { kind: 'text'; text: string };
export type EmployerVacancyImport = {
  fields: EmployerVacancyValues;
  source: { kind: 'file' | 'url' | 'text'; label: string; url?: string };
  warnings: string[];
  importId: string;
};
export type SharedState = { mode?: 'employer'; workspaceId?: string; workspaceName?: string; scope: Scope; generation: string; revision: number; actor: Actor; actorName: string; closedBy: CandidateId | null; videoConfigured: boolean; vacancy: Vacancy; vacancies?: Vacancy[]; candidates: Partial<Record<CandidateId, Person>>; meetings?: Person[]; offerCandidates?: Person[]; pendingFeedback?: Person[]; reviewResponses?: Record<string, EmployerReviewReceipt> };
export type EmployerCredentials = { email: string; password: string; name?: string; workspaceName?: string };
type EmployerSession = { token: string; state: SharedState };
export const sessionKey = 'hr-vision-connected-session';
export const employerSessionKey = 'hr-vision-employer-session';
const employerVacancyKey = 'hr-vision-employer-vacancy';
const scopedSessionKey = (scope: Scope) => scope === 'sfera' ? sessionKey : `${sessionKey}:${scope}`;
export const teamKeyName = 'hr-vision-team';

// Existing screens keep their action contract. Only requests authenticated by the
// employer session use the persisted employer domain; recruiter/demo calls do not.
const employerContexts = new Map<string, { vacancyId: string }>();
const employerRoutes = new Set(['state', 'action', 'room', 'materials', 'feedback/questions', 'intake', 'intake/archive', 'invite']);
export function journeyRequestTarget(path: string, token?: string, body?: unknown) {
  const context = token ? employerContexts.get(token) : undefined;
  const [base, query = ''] = path.split('?');
  if (!context || (!employerRoutes.has(base) && !base.startsWith('intake/files/'))) return { path, body };
  const params = new URLSearchParams(query);
  if (body === undefined && context.vacancyId) params.set('vacancyId', context.vacancyId);
  return {
    path: `employer/${base}${params.size ? '?' + params.toString() : ''}`,
    body: body && typeof body === 'object' && !Array.isArray(body) ? { vacancyId: context.vacancyId, ...body } : body,
  };
}

class RequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function request<T>(path: string, token?: string, body?: unknown): Promise<T> {
  const target = journeyRequestTarget(path, token, body);
  if (target.path === 'employer/action' && target.body && typeof target.body === 'object') {
    target.body = { requestId: crypto.randomUUID(), ...target.body };
  }
  const response = await fetch('/api/hr/' + target.path, { method: target.body === undefined ? 'GET' : 'POST',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(target.body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: target.body === undefined ? undefined : JSON.stringify(target.body), cache: 'no-store', signal: AbortSignal.timeout(target.path === 'employer/vacancies/import' ? 150000 : target.path.endsWith('/materials') || target.path === 'materials' ? 60000 : 30000) });
  const data = await response.json().catch(() => ({ error: 'Сервис временно недоступен.' }));
  if (!response.ok) throw new RequestError(data.error || 'Не удалось выполнить действие.', response.status);
  return data;
}

export function importEmployerVacancy(token: string, input: EmployerVacancyImportInput): Promise<EmployerVacancyImport> {
  return request<EmployerVacancyImport>('employer/vacancies/import', token, input);
}

export function useEntryAccess(options: { employer?: boolean } = {}) {
  const employer = !!options.employer;
  const [status, setStatus] = useState<'checking' | 'ready' | 'missing' | 'error'>('checking');
  const sequence = useRef(0);
  const check = useCallback(async () => {
    if (employer) { setStatus('ready'); return; }
    const seq = ++sequence.current;
    setStatus('checking');
    try {
      const params = new URLSearchParams(location.hash.slice(1));
      const linkKey = params.get('team');
      let ready = false;
      if (linkKey) {
        ready = (await request<{ ready: boolean }>('access', undefined, { teamKey: linkKey })).ready;
      } else {
        ready = (await request<{ ready: boolean }>('access')).ready;
        if (!ready && employer) {
          try {
            await request<AccountList>('employer/accounts', sessionStorage.getItem(employerSessionKey) || undefined);
            ready = true;
          } catch (cause) {
            if (!(cause instanceof RequestError) || ![401, 403].includes(cause.status)) throw cause;
          }
        }
        const legacyKey = sessionStorage.getItem(teamKeyName);
        const session = sessionStorage.getItem(sessionKey);
        if (!ready && (legacyKey || session)) {
          ready = (await request<{ ready: boolean }>('access', session || undefined, { teamKey: legacyKey || '' })).ready;
        }
      }
      if (seq !== sequence.current) return;
      if (ready) {
        sessionStorage.removeItem(teamKeyName);
        params.delete('team');
        const hash = params.toString();
        history.replaceState(history.state, '', location.pathname + location.search + (hash ? '#' + hash : ''));
      }
      setStatus(ready ? 'ready' : 'missing');
    } catch (error) {
      if (seq === sequence.current) setStatus(error instanceof RequestError && [401, 403].includes(error.status) ? 'missing' : 'error');
    }
  }, [employer]);
  useEffect(() => {
    void check();
    const onHash = () => { void check(); };
    window.addEventListener('hashchange', onHash);
    return () => { sequence.current++; window.removeEventListener('hashchange', onHash); };
  }, [check]);
  return { status, check };
}

export function useJourney(scope: Scope = 'sfera', options: { employer?: boolean } = {}) {
  const employer = !!options.employer;
  const storageKey = employer ? employerSessionKey : scopedSessionKey(scope);
  const [session, setSession] = useState(() => ({ key: storageKey, token: sessionStorage.getItem(storageKey) || '' }));
  const token = session.key === storageKey ? session.token : sessionStorage.getItem(storageKey) || '';
  const vacancyId = useRef(new URLSearchParams(location.search).get('vacancyId') || sessionStorage.getItem(employerVacancyKey) || '');
  const [state, setState] = useState<SharedState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [initializing, setInitializing] = useState(employer);
  const [entryWarning, setEntryWarning] = useState('');
  const busyRef = useRef(false);
  const operation = useRef(false);
  busyRef.current = busy;
  const sequence = useRef(0);
  const vacancyCommand = useRef<{ payload: string; requestId: string } | null>(null);
  const candidateCommand = useRef<{ intent: string; body: Record<string, unknown>; requestId: string } | null>(null);
  const acceptState = useCallback((data: SharedState, auth: string) => {
    if (employer) {
      vacancyId.current = data.vacancy.id || '';
      employerContexts.set(auth, { vacancyId: vacancyId.current });
      sessionStorage.setItem(employerVacancyKey, vacancyId.current);
    }
    setState(data); setError('');
  }, [employer]);
  useEffect(() => {
    if (!employer) return;
    let current = true;
    setInitializing(true);
    // Restore the HttpOnly session even in a new tab. A team grant/cookie is not
    // a registration and must never bypass the public employer entry.
    let cleanupTelegram: (() => void) | undefined;
    const restore = async () => {
      const app = await loadEmployerTelegram();
      if (!current) return null;
      if (app && (app.initData || app.platform && app.platform !== 'unknown')) {
        cleanupTelegram = prepareEmployerTelegram(app);
      }
      return restoreEmployerIdentity(app,
        () => request<EmployerSession>('employer/session?personal=1'),
        initData => request<EmployerSession>('employer/telegram/session', undefined, { initData }));
    };
    void restore().then(async next => {
      if (!next) return;
      let restored = next.state;
      const wanted = vacancyId.current;
      if (wanted && wanted !== restored.vacancy.id && restored.vacancies?.some(v => v.id === wanted)) {
        restored = await request<SharedState>('employer/state?vacancyId=' + encodeURIComponent(wanted), next.token);
      }
      if (!current) return;
      if (wanted && wanted !== restored.vacancy.id) setEntryWarning('Подборка по этой ссылке недоступна. Выберите другую вакансию в своём кабинете.');
      sessionStorage.setItem(employerSessionKey, next.token);
      acceptState(restored, next.token);
      setSession({ key: employerSessionKey, token: next.token });
    }).catch(cause => {
      if (!current) return;
      sessionStorage.removeItem(employerSessionKey);
      setSession({ key: employerSessionKey, token: '' }); setState(null);
      if (!(cause instanceof RequestError && cause.status === 401) || new URLSearchParams(location.search).get('miniapp') === 'employer') setError((cause as Error).message);
    }).finally(() => { if (current) setInitializing(false); });
    return () => { current = false; cleanupTelegram?.(); };
  }, [employer, acceptState]);
  const refresh = useCallback(async () => {
    if (!token || initializing) return;
    const seq = ++sequence.current;
    try {
      const path = employer ? `employer/state${vacancyId.current ? '?vacancyId=' + encodeURIComponent(vacancyId.current) : ''}` : 'state';
      const data = await request<SharedState>(path, token);
      if (seq === sequence.current && (employer || data.scope === scope)) acceptState(data, token);
    } catch (e) {
      if (seq !== sequence.current) return;
      setError((e as Error).message);
      if (employer && e instanceof RequestError && e.status === 401) {
        sessionStorage.removeItem(employerSessionKey); employerContexts.delete(token);
        setSession({ key: employerSessionKey, token: '' }); setState(null);
      }
    }
  }, [token, scope, employer, acceptState, initializing]);
  useEffect(() => { void refresh(); const timer = setInterval(() => { if (!busyRef.current) void refresh(); }, 2500); return () => { sequence.current++; clearInterval(timer); }; }, [refresh]);
  async function login(actor: Actor, targetScope: Scope = scope) {
    if (operation.current) return false;
    operation.current = true;
    sequence.current++; setBusy(true); setError('');
    try {
      const next = await request<{ token: string; state?: SharedState }>(employer ? 'employer/session' : 'session', undefined, employer ? {} : { actor, scope: targetScope });
      sequence.current++; setState(null);
      const key = employer ? employerSessionKey : scopedSessionKey(targetScope);
      sessionStorage.setItem(key, next.token);
      if (employer) employerContexts.set(next.token, { vacancyId: vacancyId.current });
      if (next.state) acceptState(next.state, next.token);
      setSession({ key, token: next.token }); return true;
    } catch (e) { setError((e as Error).message); return false; } finally { operation.current = false; setBusy(false); }
  }
  const clearLocation = () => {
    const url = new URL(location.href);
    for (const key of ['vacancyId', 'applicationId', 'intakeId', 'screen', 'pool', 'task', 'stageId', 'meetingId']) url.searchParams.delete(key);
    history.replaceState(history.state, '', url);
  };
  async function authenticate(mode: 'register' | 'login', credentials: EmployerCredentials) {
    if (!employer || operation.current) return false;
    operation.current = true;
    sequence.current++; setBusy(true); setError('');
    try {
      const next = await request<EmployerSession>('employer/' + mode, undefined, credentials);
      sequence.current++; clearLocation(); vacancyCommand.current = null; candidateCommand.current = null;
      if (token) employerContexts.delete(token);
      sessionStorage.setItem(employerSessionKey, next.token);
      acceptState(next.state, next.token);
      setSession({ key: employerSessionKey, token: next.token }); return true;
    } catch (e) { setError((e as Error).message); return false; }
    finally { operation.current = false; setBusy(false); }
  }
  async function logout() {
    if (!employer || operation.current) return false;
    operation.current = true;
    sequence.current++; setBusy(true); setError('');
    try {
      await request('employer/logout', token, {});
      sequence.current++; clearLocation(); vacancyCommand.current = null; candidateCommand.current = null;
      employerContexts.delete(token); vacancyId.current = '';
      sessionStorage.removeItem(employerSessionKey); sessionStorage.removeItem(employerVacancyKey);
      setSession({ key: employerSessionKey, token: '' }); setState(null); return true;
    } catch (e) { setError((e as Error).message); return false; }
    finally { operation.current = false; setBusy(false); }
  }
  async function createVacancy(values: Record<string, unknown>) {
    if (!employer || !token || operation.current) return false;
    operation.current = true;
    sequence.current++; setBusy(true); setError('');
    const payload = JSON.stringify(values);
    if (vacancyCommand.current?.payload !== payload) vacancyCommand.current = { payload, requestId: crypto.randomUUID() };
    try {
      const created = await request<{ vacancy: Vacancy }>('employer/vacancies', token, { ...values, requestId: vacancyCommand.current.requestId });
      const data = await request<SharedState>('employer/state?vacancyId=' + encodeURIComponent(created.vacancy.id!), token);
      sequence.current++; acceptState(data, token); vacancyCommand.current = null;
      clearLocation();
      const url = new URL(location.href); url.searchParams.set('vacancyId', data.vacancy.id!);
      history.replaceState(history.state, '', url); return true;
    } catch (e) { setError((e as Error).message); return false; }
    finally { operation.current = false; setBusy(false); }
  }
  async function act(action: string, candidate?: CandidateId, values: Record<string, unknown> = {}) {
    if (!state || (!employer && state.scope !== scope) || operation.current) return false;
    operation.current = true;
    sequence.current++; setBusy(true); setError('');
    try {
      const person = candidate ? state.candidates[candidate] : undefined;
      const payload = { action, candidate, generation: state.generation, ...(employer ? { vacancyId: state.vacancy.id, applicationId: person?.applicationId, ...(action.startsWith('offer.') && person?.offer ? { version: person.offer.version } : {}), ...(action.startsWith('meeting.') && person?.meeting ? { meetingId: person.meeting.id, revision: person.meeting.revision } : {}) } : {}), ...values };
      if (employer) {
        // A retry after a lost response must not append the same stage twice.
        // Retain the original version as polling may already see the committed write.
        const intent = JSON.stringify({ action, candidate, generation: state.generation, vacancyId: state.vacancy.id, values });
        if (candidateCommand.current?.intent !== intent) candidateCommand.current = { intent, body: payload, requestId: typeof values.requestId === 'string' ? values.requestId : crypto.randomUUID() };
      }
      const data = await request<SharedState>(employer ? 'employer/action' : 'action', token, employer ? { ...candidateCommand.current!.body, requestId: candidateCommand.current!.requestId } : payload);
      candidateCommand.current = null;
      sequence.current++; acceptState(data, token); return true;
    } catch (e) {
      if (e instanceof RequestError && e.status < 500) candidateCommand.current = null;
      setError((e as Error).message); return false;
    } finally { operation.current = false; setBusy(false); }
  }
  async function openVacancy(id: string, { updateLocation = true }: { updateLocation?: boolean } = {}) {
    if (!employer || !token || operation.current) return false;
    operation.current = true;
    const seq = ++sequence.current; setBusy(true); setError('');
    try {
      const data = await request<SharedState>('employer/state?vacancyId=' + encodeURIComponent(id), token);
      if (seq !== sequence.current) return false;
      acceptState(data, token);
      if (updateLocation) {
        const url = new URL(location.href); url.searchParams.set('vacancyId', id);
        history.replaceState(history.state, '', url);
      }
      return true;
    } catch (e) { if (seq === sequence.current) setError((e as Error).message); return false; }
    finally { operation.current = false; setBusy(false); }
  }
  return { entryWarning, clearEntryWarning: () => setEntryWarning(''), state: employer || state?.scope === scope ? state : null, token, error, setError, busy, initializing, login, authenticate, logout, createVacancy, act, refresh, openVacancy, employer };
}
export type Journey = ReturnType<typeof useJourney>;
export const dateTime = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value)) + ' МСК';
export const names: Record<string, string> = { manager: 'Иван Петров', anna: 'Анна Миронова', mikhail: 'Михаил Белов', elena: 'Елена Орлова' };
export const initials = (name: string) => name.split(' ').slice(0, 2).map(part => part[0]).join('');
export const actorName = (state: SharedState, actor: string) => (state.mode === 'employer' && actor === 'manager' ? state.actorName : undefined)
  || state.candidates[actor]?.name || Object.values(state.candidates).find(person => person?.candidateId === actor)?.name || names[actor] || state.actorName;
export const meetingLabels = { pending: 'Ждём подтверждения', confirmed: 'Встреча подтверждена', live: 'Идёт встреча', cancelled: 'Нужно новое время', completed: 'Встреча завершена' };
export const offerLabels = { draft: 'Черновик оффера', sent: 'Оффер отправлен', accepted: 'Оффер принят', declined: 'Отказ от оффера', withdrawn: 'Оффер отозван', superseded: 'Предыдущая версия оффера' };
export const candidateStatus = (p: Person) => p.offer ? offerLabels[p.offer.status] : p.decision === 'pool' ? 'В пуле для оффера' : p.decision === 'declined' ? 'Не продолжаем' : p.meeting ? meetingLabels[p.meeting.status] : p.reviewResponse?.decision === 'invite' ? 'Выбран для встречи' : 'Готов знакомиться';
