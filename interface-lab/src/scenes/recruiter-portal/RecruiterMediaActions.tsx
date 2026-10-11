import { useEffect, useState } from 'react';
import { PhoneOff, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { MediaPanel } from '../candidate-portal/MediaPanel';
import type { CallMaterials } from '../candidate-portal/api';

export type RecruiterMediaActionsProps = {
  kind: 'interview' | 'meeting';
  candidateId: string;
  applicationId?: string;
  meetingId?: string;
  status: 'booked' | 'live' | 'completed' | 'confirmed';
  token: string;
  onUpdated: () => void | Promise<void>;
};

export function RecruiterMediaActions(props: RecruiterMediaActionsProps) {
  return <Actions key={`${props.kind}:${props.candidateId}:${props.applicationId || ''}:${props.meetingId || ''}`} {...props}/>;
}

function Actions({ kind, candidateId, applicationId, meetingId, status, token, onUpdated }: RecruiterMediaActionsProps) {
  const [finishing, setFinishing] = useState(false);
  const [finished, setFinished] = useState(false);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState('');
  const completed = status === 'completed' || finished;
  const valid = !!candidateId && (kind === 'interview' || !!(applicationId && meetingId));
  const identity = kind === 'interview' ? { candidateId } : { candidateId, applicationId, meetingId };
  useEffect(() => {
    if (status !== 'live') setFinished(false);
    if (status === 'booked' || status === 'confirmed') setOpened(false);
  }, [status]);

  // This helper is scoped to this component so the parallel recruiter API work
  // is untouched. Material fetching can need two independent provider requests.
  async function request<T>(action: 'complete' | 'materials'): Promise<T> {
    if (!valid) throw new Error('Не удалось определить встречу. Обновите страницу.');
    const response = await fetch(`/api/hr/recruiter/${kind}/${action}`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(identity), signal: AbortSignal.timeout(60000),
    }).catch(cause => { throw new Error(cause instanceof Error && ['TimeoutError', 'AbortError'].includes(cause.name) ? 'Сервис не ответил вовремя. Попробуйте ещё раз.' : 'Не удалось связаться с сервером. Проверьте соединение.'); });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof data?.error === 'string' ? data.error : 'Не удалось выполнить действие. Попробуйте ещё раз.');
    if (!data || typeof data !== 'object') throw new Error('Сервис вернул неверный ответ. Попробуйте ещё раз.');
    return data as T;
  }
  async function finish() {
    if (finishing) return;
    setFinishing(true); setError('');
    try {
      await request('complete');
      setFinished(true); setOpened(true);
      try { await onUpdated(); } catch { setError('Встреча завершена. Обновите список, чтобы увидеть её новый статус.'); }
    } catch (cause) { setError((cause as Error).message); }
    finally { setFinishing(false); }
  }
  if (!completed && status !== 'live') return null;
  return <div style={{ display: 'grid', gap: 18, minWidth: 0 }}>
    {error && <p className="rp-error" role="alert">{error}</p>}
    {!valid && <p className="rp-error" role="alert">Не удалось определить встречу. Обновите страницу.</p>}
    <div className="rp-actions">
      {!completed && <Button variant="secondary" disabled={finishing || !valid} onClick={finish}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : 'Завершить встречу'}</Button>}
      {completed && <Button variant="secondary" disabled={!valid} aria-expanded={opened} onClick={() => setOpened(current => !current)}><Video size={16}/>{opened ? 'Скрыть материалы' : 'Материалы встречи'}</Button>}
    </div>
    {completed && valid && opened && <MediaPanel identity={`${kind}:${candidateId}:${applicationId || ''}:${meetingId || ''}`} load={() => request<CallMaterials>('materials')}/>}
  </div>;
}
