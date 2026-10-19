import { useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { recruiterRequest } from './api';

type Status = { linked: boolean; canLink: boolean };
/** One account action; requests and links are discarded when the account changes. */
export function RecruiterTelegramAction({ token, disabled, onError }: { token: string; disabled: boolean; onError: (message: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const active = useRef(true);
  const changing = useRef(false);
  const sequence = useRef(0);
  useEffect(() => {
    active.current = true;
    const refresh = () => {
      if (changing.current) return;
      const request = ++sequence.current;
      void recruiterRequest<{ telegram: Status }>('notifications', token).then(value => {
        if (active.current && request === sequence.current && !changing.current) { setStatus(value.telegram); if (value.telegram.linked) setLink(''); }
      }).catch(() => {});
    };
    refresh(); window.addEventListener('focus', refresh);
    const visible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { active.current = false; sequence.current++; window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', visible); };
  }, [token]);
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
  if (!status) return null;
  return <>
    <Button type="button" variant="ghost" disabled={disabled || busy || (!status.linked && !status.canLink)} onClick={() => void act()}>
      {busy ? 'Подождите…' : status.linked ? 'Telegram подключён · отключить' : status.canLink ? 'Подключить Telegram' : 'Telegram временно недоступен'}
    </Button>
    {link && <a href={link} target="_blank" rel="noopener noreferrer">Открыть бота и подключить</a>}
  </>;
}
