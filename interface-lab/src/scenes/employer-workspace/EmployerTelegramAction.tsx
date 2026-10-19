import { useCallback, useEffect, useRef, useState } from 'react';
import { DropdownMenu } from 'radix-ui';
import { request } from '../connected-journey/api';

type Status = { linked: boolean; available: boolean };
type Props = { token: string; disabled?: boolean; onError: (message: string) => void };
export function EmployerTelegramAction(props: Props) {
  // A token change discards the previous account's state and pending operations.
  return <EmployerTelegramAccountAction key={props.token} {...props}/>;
}
function EmployerTelegramAccountAction({ token, disabled, onError }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const [loadError, setLoadError] = useState(false);
  const active = useRef(true);
  const changing = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(() => {
    if (changing.current) return;
    const read = ++sequence.current;
    const current = () => active.current && read === sequence.current && !changing.current;
    setLoadError(false);
    void request<Status>('employer/telegram', token).then(value => {
      if (current()) { setStatus(value); if (value.linked) setLink(''); }
    }).catch(() => {
      if (current()) setLoadError(true);
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
    if (disabled || changing.current || !status || (!status.linked && !status.available)) return;
    changing.current = true;
    const mutation = ++sequence.current;
    const current = () => active.current && mutation === sequence.current;
    setBusy(true); setLoadError(false);
    try {
      if (status.linked) {
        await request('employer/telegram/disconnect', token, {});
        if (current()) { setStatus({ ...status, linked: false }); setLink(''); }
      } else {
        const result = await request<{ url: string }>('employer/telegram/link', token, {});
        if (current()) setLink(result.url);
        // Opening remains a direct click, so browsers do not block an async popup.
      }
    } catch (error) { if (current()) onError((error as Error).message); }
    finally { if (current()) { changing.current = false; setBusy(false); } }
  }
  if (loadError) return <>
    <DropdownMenu.Label role="status">Не удалось проверить Telegram.</DropdownMenu.Label>
    <DropdownMenu.Item disabled={disabled} onSelect={event => { event.preventDefault(); refresh(); }}>Повторить</DropdownMenu.Item>
  </>;
  if (!status) return <DropdownMenu.Item disabled>Проверяем Telegram…</DropdownMenu.Item>;
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
