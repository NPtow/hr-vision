import { useCallback, useEffect, useRef, useState } from 'react';
import { hrRequest, OutreachError } from './api';

type AccessState = { status: 'checking' | 'ready' | 'missing' | 'error'; token: string; error: string };
/** Global outreach always requires the private team grant, never a recruiter or employer cookie. */
export function useOperatorSession() {
  const [state, setState] = useState<AccessState>({ status: 'checking', token: '', error: '' });
  const sequence = useRef(0);
  const check = useCallback(async () => {
    const seq = ++sequence.current;
    setState({ status: 'checking', token: '', error: '' });
    try {
      const fragment = new URLSearchParams(location.hash.slice(1));
      const suppliedKey = fragment.get('team');
      // Remove the supplied capability before any requests or rendering. Do not retain it in storage.
      if (fragment.has('team')) {
        fragment.delete('team');
        history.replaceState(history.state, '', location.pathname + location.search + (fragment.size ? '#' + fragment.toString() : ''));
      }
      const access = await hrRequest<{ ready: boolean }>('access', undefined, suppliedKey ? { teamKey: suppliedKey } : undefined);
      if (seq !== sequence.current) return;
      if (!access.ready) { setState({ status: 'missing', token: '', error: '' }); return; }
      const result = await hrRequest<{ token: string }>('session', undefined, { actor: 'manager', scope: 'sfera' });
      if (!result.token) throw new Error('Сервис не создал сессию оператора.');
      if (seq === sequence.current) setState({ status: 'ready', token: result.token, error: '' });
    } catch (cause) {
      if (seq !== sequence.current) return;
      const denied = cause instanceof OutreachError && [401, 403].includes(cause.status);
      setState({ status: denied ? 'missing' : 'error', token: '', error: denied ? '' : (cause as Error).message });
    }
  }, []);
  useEffect(() => {
    void check();
    const onHashChange = () => { void check(); };
    window.addEventListener('hashchange', onHashChange);
    return () => { sequence.current++; window.removeEventListener('hashchange', onHashChange); };
  }, [check]);
  const expire = useCallback(() => {
    sequence.current++;
    setState({ status: 'missing', token: '', error: 'Доступ к команде истёк. Откройте рабочую ссылку HR Vision.' });
  }, []);
  return { ...state, check, expire };
}
