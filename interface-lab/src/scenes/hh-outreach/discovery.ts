import { useCallback, useEffect, useRef, useState } from 'react';
import { outreachRequest, OutreachError, type DiscoveryState, type DiscoveryStarted } from './api';

const isPending = (state: DiscoveryState | null) => state?.status === 'queued' || state?.status === 'running';

/** Restores persisted discovery after reload; polling never creates another import. */
export function useDiscovery(token: string, campaignId: string, onExpired: () => void, onSettled: () => Promise<void>) {
  const [state, setState] = useState<DiscoveryState | null>(null);
  const [error, setError] = useState('');
  const current = useRef<DiscoveryState | null>(null);
  const sequence = useRef(0);
  const read = useCallback(async () => {
    if (!campaignId) return;
    const seq = ++sequence.current;
    try {
      const next = await outreachRequest<DiscoveryState>('campaigns/' + encodeURIComponent(campaignId) + '/discovery', token);
      if (seq !== sequence.current) return;
      const settled = isPending(current.current) && !isPending(next);
      current.current = next; setState(next); setError('');
      if (settled) await onSettled();
    } catch (cause) {
      if (seq !== sequence.current) return;
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) onExpired();
      else setError('Не удалось проверить подготовку кандидатов. ' + (cause as Error).message);
    }
  }, [token, campaignId, onExpired, onSettled]);
  useEffect(() => {
    current.current = null; setState(null); setError(''); void read();
    return () => { sequence.current++; };
  }, [read]);
  const pending = isPending(state);
  useEffect(() => {
    if (!pending && !error) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await read();
      if (!stopped) timer = setTimeout(() => void poll(), 2000);
    };
    timer = setTimeout(() => void poll(), 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [pending, error, read]);
  function started(next: DiscoveryStarted) {
    sequence.current++;
    current.current = next; setState(next); setError('');
  }
  return { state, error, pending, read, started };
}
