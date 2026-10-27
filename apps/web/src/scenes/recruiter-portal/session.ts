import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from '../connected-journey/api';
import { recruiterRequest } from './api';

export type RecruiterIdentity = { id: string; name: string; email: string };
type Session = { authenticated: boolean; recruiter: RecruiterIdentity | null; token?: string; canManageAccounts?: boolean };
const empty: Session = { authenticated: false, recruiter: null };

export function useRecruiterSession() {
  const [session, setSession] = useState<Session>(empty);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const sequence = useRef(0);

  const restore = useCallback(async () => {
    const seq = ++sequence.current;
    setLoading(true); setError('');
    try {
      const hash = new URLSearchParams(location.hash.slice(1));
      const team = hash.get('team');
      if (team) {
        await request('access', undefined, { teamKey: team });
        hash.delete('team');
        history.replaceState(history.state, '', location.pathname + location.search + (hash.size ? '#' + hash.toString() : ''));
      }
      const next = await recruiterRequest<Session>('auth/session', '');
      if (seq === sequence.current) setSession(next);
    } catch (cause) {
      if (seq === sequence.current) { setSession(empty); setError((cause as Error).message); }
    } finally { if (seq === sequence.current) setLoading(false); }
  }, []);
  useEffect(() => { void restore(); return () => { sequence.current++; }; }, [restore]);

  async function authenticate(path: 'login' | 'activate', values: Record<string, unknown>) {
    if (pending.current) return false;
    pending.current = true; const seq = ++sequence.current; setBusy(true); setError('');
    try {
      await recruiterRequest<Session>('auth/' + path, '', values);
      // Restore the cookie and trusted team capability from the server, never from form fields.
      const next = await recruiterRequest<Session>('auth/session', '');
      if (seq !== sequence.current) return false;
      setSession(next);
      return true;
    } catch (cause) { if (seq === sequence.current) setError((cause as Error).message); return false; }
    finally { if (seq === sequence.current) { pending.current = false; setBusy(false); } }
  }

  async function logout() {
    if (pending.current) return false;
    pending.current = true; const seq = ++sequence.current; setBusy(true); setError('');
    try {
      await recruiterRequest('auth/logout', session.token || '', {});
      if (seq !== sequence.current) return false;
      setSession({ ...empty, canManageAccounts: session.canManageAccounts });
      return true;
    } catch (cause) { if (seq === sequence.current) setError((cause as Error).message); return false; }
    finally { if (seq === sequence.current) { pending.current = false; setBusy(false); } }
  }
  function expire() {
    sequence.current++;
    pending.current = false; setBusy(false);
    setSession({ ...empty, canManageAccounts: session.canManageAccounts });
    setError('Войдите в кабинет ещё раз.');
  }
  return { recruiter: session.recruiter, token: session.token || '', canManageAccounts: !!session.canManageAccounts,
    loading, busy, error, setError, restore, authenticate, logout, expire };
}
