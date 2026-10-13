import { useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { AvailabilitySchedule } from './AvailabilitySchedule';
import type { AvailabilityBlock } from './api';
import './recruiter-account.css';

export type RecruiterChannels = {
  email: { configured: boolean; verified: boolean; address: string; status: string };
  telegram: { configured: boolean; linked: boolean; canLink: boolean; status: string };
};
export type RecruiterChannelActionResult = {
  status: string; queued?: boolean; retryAfter?: number; url?: string | null; expiresAt?: number | null;
};

export function RecruiterProfile({ recruiter, blocks, busy, run, onLogout, channels, onChannelAction }: {
  recruiter: { id: string; name: string; email: string };
  blocks: AvailabilityBlock[];
  busy: boolean;
  run: (path: string, body: unknown) => Promise<boolean>;
  onLogout: () => void;
  channels?: RecruiterChannels;
  onChannelAction?: (action: 'email' | 'telegram') => Promise<RecruiterChannelActionResult | null>;
}) {
  const [channelBusy, setChannelBusy] = useState<'email' | 'telegram' | null>(null);
  const [channelMessage, setChannelMessage] = useState('');
  const [channelError, setChannelError] = useState('');
  const [telegramUrl, setTelegramUrl] = useState('');
  const pending = useRef(false);
  useEffect(() => { setChannelMessage(''); setChannelError(''); setTelegramUrl(''); }, [recruiter.id]);
  useEffect(() => { if (channels?.telegram.linked) setTelegramUrl(''); }, [channels?.telegram.linked]);

  async function channelAction(action: 'email' | 'telegram') {
    if (busy || pending.current || !onChannelAction) return;
    pending.current = true; setChannelBusy(action); setChannelMessage(''); setChannelError('');
    try {
      const result = await onChannelAction(action);
      if (!result) return;
      if (action === 'telegram') {
        if (result.url?.startsWith('https://t.me/')) setTelegramUrl(result.url);
        else setChannelError('Подключить Telegram сейчас не удалось. Попробуйте позже.');
      } else if (result.queued) {
        setChannelMessage('Запрос на подтверждение принят. Проверьте почту.');
      } else if (result.status === 'verified') {
        setChannelMessage('Почта подтверждена.');
      } else if (result.status === 'rate_limited') {
        setChannelMessage('Письмо уже запрошено. Проверьте почту или повторите позже.');
      } else {
        setChannelError('Подтвердить почту сейчас не удалось. Попробуйте позже.');
      }
    } catch { setChannelError('Не удалось выполнить действие. Попробуйте ещё раз.'); }
    finally { pending.current = false; setChannelBusy(null); }
  }

  return <div className="rp-page-content rp-profile">
    <section className="rp-profile-identity" aria-label="Профиль рекрутера">
      <div><h2>{recruiter.name}</h2><p>{recruiter.email}</p></div>
      <div className="rp-profile-actions"><Button type="button" variant="ghost" disabled={busy || channelBusy !== null} onClick={onLogout}>Выйти</Button></div>
    </section>
    <AvailabilitySchedule key={recruiter.id} recruiterName={recruiter.name} blocks={blocks} busy={busy} run={run}/>
    {channels && <section className="rp-profile-channels" aria-labelledby="rp-profile-channels-title">
      <h2 id="rp-profile-channels-title">Уведомления об интервью</h2>
      <div className="rp-profile-channel"><div><h3>Почта</h3><p>{channels.email.verified && channels.email.configured ? 'Подключена' : channels.email.configured ? 'Нужно подтвердить почту' : 'Уведомления пока недоступны'}</p></div>
        {channels.email.configured && !channels.email.verified && onChannelAction && <Button type="button" variant="secondary" disabled={busy || channelBusy !== null} onClick={() => void channelAction('email')}>{channelBusy === 'email' ? 'Отправляем запрос…' : 'Подтвердить почту'}</Button>}
      </div>
      <div className="rp-profile-channel"><div><h3>Telegram</h3><p>{channels.telegram.linked && channels.telegram.configured ? 'Подключён' : channels.telegram.configured ? 'Не подключён' : 'Уведомления пока недоступны'}</p></div>
        {channels.telegram.configured && channels.telegram.canLink && !channels.telegram.linked && onChannelAction && <Button type="button" variant="secondary" disabled={busy || channelBusy !== null} onClick={() => void channelAction('telegram')}>{channelBusy === 'telegram' ? 'Готовим ссылку…' : 'Подключить Telegram'}</Button>}
      </div>
      {telegramUrl && !channels.telegram.linked && <div className="rp-profile-telegram"><a href={telegramUrl} target="_blank" rel="noopener noreferrer">Открыть Telegram</a><p>Нажмите «Запустить» в боте, чтобы получать уведомления.</p></div>}
      {channelMessage && <p className="rp-profile-channel-message" role="status">{channelMessage}</p>}
      {channelError && <p className="rp-error" role="alert">{channelError}</p>}
    </section>}
  </div>;
}
