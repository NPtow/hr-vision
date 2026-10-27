import { useEffect, useState } from 'react';
import { EmployerAuth, type EmployerAuthMode, type EmployerAuthValues } from '../employer-workspace/EmployerAuth';
import { employerSessionKey, request } from '../connected-journey/api';
import { MeetingNotesDemo } from './MeetingNotesDemo';

type EmployerSession = { token: string; state?: { actorName?: string } };

/** The separate meeting demo uses the existing employer account and its cookie. */
export function MeetingNotesDemoEntry() {
  const [token, setToken] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void request<EmployerSession>('employer/session?personal=1').then(session => {
      if (active) {
        sessionStorage.setItem(employerSessionKey, session.token);
        setToken(session.token);
        setDisplayName(session.state?.actorName || '');
      }
    }).catch(() => {
      if (active) sessionStorage.removeItem(employerSessionKey);
    }).finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, []);

  async function authenticate(mode: EmployerAuthMode, values: EmployerAuthValues) {
    setBusy(true); setError('');
    try {
      const session = await request<EmployerSession>(`employer/${mode}`, undefined, values);
      sessionStorage.setItem(employerSessionKey, session.token);
      setToken(session.token);
      setDisplayName(session.state?.actorName || '');
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось войти.');
      return false;
    } finally { setBusy(false); }
  }

  function exit() {
    const url = new URL(location.href);
    url.searchParams.delete('demo');
    url.searchParams.delete('sessionId');
    url.searchParams.delete('meetingSession');
    location.assign(url.href);
  }

  if (checking) return <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center' }} aria-busy="true"><span role="status">Подключаемся…</span></main>;
  if (!token) return <EmployerAuth initialMode="login" busy={busy} error={error} onSubmit={authenticate} onModeChange={() => setError('')}/>;
  return <MeetingNotesDemo token={token} displayName={displayName || undefined} onExit={exit}/>;
}
