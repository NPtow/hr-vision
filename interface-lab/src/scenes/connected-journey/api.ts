import { useCallback, useEffect, useRef, useState } from 'react';
import type { CandidateId } from '../agency-product/model';
export type Actor = CandidateId | 'manager';
export type Offer = { status: 'draft' | 'sent' | 'accepted' | 'declined'; role: string; compensation: string; format: string; expectations: string; startDate: string; version: number };
export type FeedbackQuestion = { id: string; question: string; seconds: number | null; source: 'live' | 'transcript' | 'manual' };
export type FeedbackAnswer = { rating: 'clear' | 'unclear' | 'unanswered' | 'not_asked'; comment: string };
export type Questionnaire = { id: string; meetingId: string; questions: FeedbackQuestion[] };
export type Meeting = { id: string; start: string; startedAt?: string; endedAt?: string; status: 'pending' | 'confirmed' | 'live' | 'cancelled' | 'completed'; testSkip: boolean; joined: Actor[]; questionnaire?: Questionnaire | null; feedbackDraft?: Record<string, FeedbackAnswer> };
export type Person = { id: CandidateId; name: string; interest: 'pending' | 'accepted' | 'declined'; auto: boolean; slots?: string[]; busy?: string[]; freeSlots: string[]; meeting: Meeting | null; feedback?: { example?: string; doubts?: string; check?: string; at: string; questions?: FeedbackQuestion[]; answers?: Record<string, FeedbackAnswer> } | null; afterInterest: 'pending' | 'yes' | 'no'; decision: 'review' | 'pool' | 'declined'; offer: Offer | null; messages: { id: string; actor: string; text: string; at: string; system: boolean }[] };
export type SharedState = { generation: string; revision: number; actor: Actor; actorName: string; closedBy: CandidateId | null; videoConfigured: boolean; vacancy: { company: string; role: string; problem: string; salary: string; format: string; expectations: string }; candidates: Partial<Record<CandidateId, Person>> };
export const sessionKey = 'hr-vision-connected-session';
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

export function useJourney() {
  const [token, setToken] = useState(() => sessionStorage.getItem(sessionKey) || '');
  const [state, setState] = useState<SharedState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    if (!token) return;
    const seq = ++sequence.current;
    try { const data = await request<SharedState>('state', token); if (seq === sequence.current) setState(data); }
    catch (e) { if (seq === sequence.current) setError((e as Error).message); }
  }, [token]);
  useEffect(() => { void refresh(); const timer = setInterval(() => { if (!busy) void refresh(); }, 2500); return () => clearInterval(timer); }, [refresh, busy]);
  async function login(actor: Actor) {
    setBusy(true); setError('');
    try {
      const session = await request<{ token: string }>('session', undefined, { actor });
      sequence.current++; setState(null);
      sessionStorage.setItem(sessionKey, session.token);
      setToken(session.token); return true;
    } catch (e) { setError((e as Error).message); return false; } finally { setBusy(false); }
  }
  async function act(action: string, candidate?: CandidateId, values: Record<string, unknown> = {}) {
    if (!state || busy) return false;
    sequence.current++; setBusy(true); setError('');
    try {
      const data = await request<SharedState>('action', token, { action, candidate, generation: state.generation, ...values });
      sequence.current++; setState(data); return true;
    } catch (e) { setError((e as Error).message); return false; } finally { setBusy(false); }
  }
  return { state, token, error, setError, busy, login, act, refresh };
}
export type Journey = ReturnType<typeof useJourney>;
export const dateTime = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value)) + ' МСК';
export const names: Record<Actor, string> = { manager: 'Иван Петров', anna: 'Анна Миронова', mikhail: 'Михаил Белов', elena: 'Елена Орлова' };
