import { useCallback, useEffect, useRef, useState } from 'react';
import type { CandidateId as SferaCandidateId } from '../agency-product/model';
import type { RecordingChapter } from '../interview-review/InterviewReview';
export type Scope = 'sfera' | 'dsa';
export type CandidateId = SferaCandidateId | `dsa-${string}`;
export type Actor = CandidateId | 'manager';
export type AccountList = { scope: Scope; company: string; accounts: { id: Actor; name: string; role: string }[] };
export type SourceProfile = {
  id: string; name: string; initials: string; role: string; interviewRole: string; interviewDate: string;
  score: number | null; duration: number | null; photo: string | null; video: string | null;
  shortChapters: RecordingChapter[]; fullChapters: RecordingChapter[]; shortSummary: string;
  profile: { label: string; value: string }[]; cv: string; conclusion: string[]; questions: string[];
};
export type Offer = { status: 'draft' | 'sent' | 'accepted' | 'declined'; role: string; compensation: string; format: string; expectations: string; startDate: string; version: number };
export type FeedbackQuestion = { id: string; question: string; seconds: number | null; source: 'live' | 'transcript' | 'manual' };
export type FeedbackAnswer = { rating: 'clear' | 'unclear' | 'unanswered' | 'not_asked'; comment: string };
export type Questionnaire = { id: string; meetingId: string; questions: FeedbackQuestion[] };
export type Meeting = { id: string; start: string; startedAt?: string; endedAt?: string; status: 'pending' | 'confirmed' | 'live' | 'cancelled' | 'completed'; testSkip: boolean; joined: Actor[]; questionnaire?: Questionnaire | null; feedbackDraft?: Record<string, FeedbackAnswer> };
export type Person = { id: CandidateId; name: string; role?: string; summary?: string; photo?: string | null; sourceProfile?: SourceProfile; interest: 'pending' | 'accepted' | 'declined'; auto: boolean; slots?: string[]; busy?: string[]; freeSlots: string[]; meeting: Meeting | null; feedback?: { example?: string; doubts?: string; check?: string; at: string; questions?: FeedbackQuestion[]; answers?: Record<string, FeedbackAnswer> } | null; afterInterest: 'pending' | 'yes' | 'no'; decision: 'review' | 'pool' | 'declined'; offer: Offer | null; messages: { id: string; actor: string; text: string; at: string; system: boolean }[] };
export type SharedState = { scope: Scope; generation: string; revision: number; actor: Actor; actorName: string; closedBy: CandidateId | null; videoConfigured: boolean; vacancy: { company: string; role: string; problem: string; salary: string; format: string; expectations: string }; candidates: Partial<Record<CandidateId, Person>> };
export const sessionKey = 'hr-vision-connected-session';
const scopedSessionKey = (scope: Scope) => scope === 'sfera' ? sessionKey : `${sessionKey}:${scope}`;
export const teamKeyName = 'hr-vision-team';

class RequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function request<T>(path: string, token?: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/hr/' + path, { method: body === undefined ? 'GET' : 'POST',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(30000) });
  const data = await response.json().catch(() => ({ error: 'Сервис временно недоступен.' }));
  if (!response.ok) throw new RequestError(data.error || 'Не удалось выполнить действие.', response.status);
  return data;
}

export function useEntryAccess() {
  const [status, setStatus] = useState<'checking' | 'ready' | 'missing' | 'error'>('checking');
  const sequence = useRef(0);
  const check = useCallback(async () => {
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
  }, []);
  useEffect(() => {
    void check();
    const onHash = () => { void check(); };
    window.addEventListener('hashchange', onHash);
    return () => { sequence.current++; window.removeEventListener('hashchange', onHash); };
  }, [check]);
  return { status, check };
}

export function useJourney(scope: Scope = 'sfera') {
  const [session, setSession] = useState(() => ({ scope, token: sessionStorage.getItem(scopedSessionKey(scope)) || '' }));
  const token = session.scope === scope ? session.token : sessionStorage.getItem(scopedSessionKey(scope)) || '';
  const [state, setState] = useState<SharedState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    if (!token) return;
    const seq = ++sequence.current;
    try { const data = await request<SharedState>('state', token); if (seq === sequence.current && data.scope === scope) setState(data); }
    catch (e) { if (seq === sequence.current) setError((e as Error).message); }
  }, [token, scope]);
  useEffect(() => { void refresh(); const timer = setInterval(() => { if (!busy) void refresh(); }, 2500); return () => { sequence.current++; clearInterval(timer); }; }, [refresh, busy]);
  async function login(actor: Actor, targetScope: Scope = scope) {
    sequence.current++; setBusy(true); setError('');
    try {
      const next = await request<{ token: string }>('session', undefined, { actor, scope: targetScope });
      sequence.current++; setState(null);
      sessionStorage.setItem(scopedSessionKey(targetScope), next.token);
      setSession({ scope: targetScope, token: next.token }); return true;
    } catch (e) { setError((e as Error).message); return false; } finally { setBusy(false); }
  }
  async function act(action: string, candidate?: CandidateId, values: Record<string, unknown> = {}) {
    if (!state || state.scope !== scope || busy) return false;
    sequence.current++; setBusy(true); setError('');
    try {
      const data = await request<SharedState>('action', token, { action, candidate, generation: state.generation, ...values });
      sequence.current++; setState(data); return true;
    } catch (e) { setError((e as Error).message); return false; } finally { setBusy(false); }
  }
  return { state: state?.scope === scope ? state : null, token, error, setError, busy, login, act, refresh };
}
export type Journey = ReturnType<typeof useJourney>;
export const dateTime = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value)) + ' МСК';
export const names: Record<string, string> = { manager: 'Иван Петров', anna: 'Анна Миронова', mikhail: 'Михаил Белов', elena: 'Елена Орлова' };
export const initials = (name: string) => name.split(' ').slice(0, 2).map(part => part[0]).join('');
export const actorName = (state: SharedState, actor: string) => state.candidates[actor as CandidateId]?.name || names[actor] || state.actorName;
export const meetingLabels = { pending: 'Ждём подтверждения', confirmed: 'Встреча подтверждена', live: 'Идёт встреча', cancelled: 'Нужно новое время', completed: 'Встреча завершена' };
export const offerLabels = { draft: 'Черновик оффера', sent: 'Оффер отправлен', accepted: 'Оффер принят', declined: 'Отказ от оффера' };
export const candidateStatus = (p: Person) => p.offer ? offerLabels[p.offer.status] : p.decision === 'pool' ? 'В пуле для оффера' : p.decision === 'declined' ? 'Не продолжаем' : p.meeting ? meetingLabels[p.meeting.status] : 'Готов знакомиться';
