import { useCallback, useEffect, useRef, useState } from 'react';
import { Mail } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest } from './api';
import { InvitationControls } from './InvitationControls';
import { candidateAccessMessage, candidateAccessRequestId, type CandidateAccessState } from './candidate-access-model';
import './candidate-access.css';

function readAttempt(key: string) { try { return sessionStorage.getItem(key) || ''; } catch { return ''; } }
function storeAttempt(key: string, value: string) { try { sessionStorage.setItem(key, value); } catch { /* Server also deduplicates by candidate. */ } }

/** Shared by the existing interview details and call surface; credentials stay on the server. */
export function CandidateAccessControls({ candidateId, token, allowClaimFallback = false, onChanged }: {
  candidateId: string; token: string; allowClaimFallback?: boolean; onChanged?: () => void;
}) {
  const [state, setState] = useState<CandidateAccessState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const attemptKey = `hr-vision-candidate-access:${candidateId}`;
  const requestId = useRef(readAttempt(attemptKey));
  const onChangedRef = useRef(onChanged); onChangedRef.current = onChanged;

  const refresh = useCallback(async () => {
    const current = generation.current;
    try {
      const result = await recruiterRequest<CandidateAccessState>('candidate-access/state', token, { candidateId });
      if (current !== generation.current) return;
      setState(result); setError('');
      // If the request never reached the server, an identical explicit retry is safe.
      if (result.status !== 'not_requested') setUncertain(false);
      if (result.requestId) { requestId.current = result.requestId; storeAttempt(attemptKey, result.requestId); }
    } catch {
      if (current === generation.current) setError('Не удалось проверить отправку письма.');
    } finally { if (current === generation.current) setLoading(false); }
  }, [candidateId, token, attemptKey]);

  useEffect(() => {
    generation.current++; setState(null); setLoading(true); setError(''); setUncertain(false);
    requestId.current = readAttempt(attemptKey); void refresh();
    return () => { generation.current++; };
  }, [refresh, attemptKey, retry]);
  useEffect(() => {
    if (!state || !['queued', 'sending'].includes(state.status) || error) return;
    const timeout = window.setTimeout(() => { if (document.visibilityState === 'visible') void refresh(); }, 4000);
    return () => window.clearTimeout(timeout);
  }, [state, error, refresh]);

  useEffect(() => {
    const focused = () => { if (document.visibilityState === 'visible' && !inFlight.current) void refresh(); };
    window.addEventListener('focus', focused); document.addEventListener('visibilitychange', focused);
    return () => { window.removeEventListener('focus', focused); document.removeEventListener('visibilitychange', focused); };
  }, [refresh]);

  async function send() {
    if (!state || !state.canSend || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    const current = generation.current;
    requestId.current = candidateAccessRequestId(state, requestId.current, () => crypto.randomUUID(), uncertain);
    storeAttempt(attemptKey, requestId.current);
    try {
      const result = await recruiterRequest<CandidateAccessState>('candidate-access/send', token, { candidateId, requestId: requestId.current });
      if (current !== generation.current) return;
      setState(result); setUncertain(false); onChangedRef.current?.();
    } catch {
      if (current !== generation.current) return;
      setUncertain(true); setError('Ответ на отправку не получен. Проверим, было ли отправлено письмо.');
      await refresh();
    } finally {
      inFlight.current = false;
      if (current === generation.current) setBusy(false);
    }
  }

  if (loading) return <p className="rca-access-help" role="status">Проверяем доступ кандидата…</p>;
  if (!state) return <div className="rca-access"><p className="rca-access-error" role="alert">{error}</p><Button variant="secondary" onClick={() => setRetry(value => value + 1)}>Проверить доступ</Button></div>;
  if (allowClaimFallback && state.blockedReason === 'booking_consent_required') return <InvitationControls candidateId={candidateId} scope="claim" token={token} onChanged={onChanged}/>;
  return <section className="rca-access" aria-label="Доступ кандидата к профилю" aria-busy={busy}>
    <div><h3>Доступ кандидату</h3><p className="rca-access-help" role="status">{candidateAccessMessage(state)}</p></div>
    {state.canSend && <Button type="button" variant="secondary" disabled={busy} onClick={() => void send()}><Mail size={16}/>{busy ? 'Отправляем…' : uncertain ? 'Проверить и отправить' : state.status === 'failed' ? 'Повторить отправку' : 'Отправить доступ на почту'}</Button>}
    {(state.status === 'unknown' || error || (uncertain && !state.canSend)) && <Button type="button" variant="secondary" disabled={busy} onClick={() => void refresh()}>Проверить отправку</Button>}
    {error && <p className="rca-access-error" role="alert">{error}</p>}
  </section>;
}
