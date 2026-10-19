import { useEffect, useState } from 'react';
import { DropdownMenu } from 'radix-ui';
import { request } from '../connected-journey/api';

type Status = { linked: boolean; available: boolean };
export function EmployerTelegramAction({ token, disabled, onError }: { token: string; disabled?: boolean; onError: (message: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  useEffect(() => {
    let current = true;
    const refresh = () => { void request<Status>('employer/telegram', token).then(value => { if (current) { setStatus(value); if (value.linked) setLink(''); } }).catch(() => {}); };
    refresh(); window.addEventListener('focus', refresh);
    const visible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { current = false; window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', visible); };
  }, [token]);
  async function act() {
    setBusy(true);
    try {
      if (status?.linked) {
        await request('employer/telegram/disconnect', token, {});
        setStatus(current => current ? { ...current, linked: false } : current); setLink('');
      } else {
        const result = await request<{ url: string }>('employer/telegram/link', token, {});
        setLink(result.url);
        // Opening remains a direct click, so browsers do not block an async popup.
      }
    } catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  if (!status) return null;
  return <>
    <DropdownMenu.Item disabled={disabled || busy || (!status.linked && !status.available)} onSelect={event => { event.preventDefault(); void act(); }}>
      {busy ? 'Подождите…' : status.linked ? 'Telegram подключён · отключить' : status.available ? 'Подключить Telegram' : 'Telegram временно недоступен'}
    </DropdownMenu.Item>
    {link && <DropdownMenu.Item asChild><a href={link} target="_blank" rel="noopener noreferrer" onClick={event => {
      const app = window.Telegram?.WebApp;
      if (app?.initData && app.openTelegramLink) { event.preventDefault(); app.openTelegramLink(link); }
    }}>Открыть бота и подключить</a></DropdownMenu.Item>}
  </>;
}
