import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { recruiterRequest } from './api';

type Status = { linked: boolean; canLink: boolean };
/** One account action; requests and links are discarded when the account changes. */
export function RecruiterTelegramAction({ token, disabled, onError }: { token: string; disabled: boolean; onError: (message: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const [loadError, setLoadError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const active = useRef(true);
  const changing = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(() => {
    if (changing.current) return;
    const request = ++sequence.current;
    const current = () => active.current && request === sequence.current && !changing.current;
    setRefreshing(true); setLoadError(false);
    void recruiterRequest<{ telegram: Status }>('notifications', token).then(value => {
      if (current()) { setStatus(value.telegram); if (value.telegram.linked) setLink(''); }
    }).catch(() => {
      if (current()) setLoadError(true);
    }).finally(() => {
      if (current()) setRefreshing(false);
    });
  }, [token]);
  useEffect(() => {
    active.current = true;
    refresh(); window.addEventListener('focus', refresh);
    const visible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { active.current = false; sequence.current++; window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', visible); };
  }, [refresh]);
  async function act() {
    if (changing.current) return;
    changing.current = true; sequence.current++; setBusy(true);
    try {
      if (status?.linked) {
        await recruiterRequest('notifications/telegram/disconnect', token, {});
        if (active.current) { setStatus(current => current ? { ...current, linked: false } : current); setLink(''); }
      } else {
        const value = await recruiterRequest<{ url: string | null }>('notifications/telegram/link', token, {});
        if (active.current) setLink(value.url || '');
      }
    } catch (error) { if (active.current) onError((error as Error).message); }
    finally { if (active.current) { changing.current = false; setBusy(false); } }
  }
  if (loadError) return <>
    <span role="status">Не удалось проверить Telegram.</span>
    <Button type="button" variant="ghost" disabled={disabled || refreshing} onClick={refresh}>Повторить</Button>
  </>;
  if (!status) return <Button type="button" variant="ghost" disabled>Проверяем Telegram…</Button>;
  return <>
    <Button type="button" variant="ghost" disabled={disabled || busy || refreshing || (!status.linked && !status.canLink)} onClick={() => void act()}>
      {busy ? 'Подождите…' : status.linked ? 'Telegram подключён · отключить' : status.canLink ? 'Подключить Telegram' : 'Telegram временно недоступен'}
    </Button>
    {link && <a href={link} target="_blank" rel="noopener noreferrer">Открыть бота и подключить</a>}
  </>;
}
