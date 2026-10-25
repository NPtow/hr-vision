import { useState } from 'react';
import { ArrowRight, Send } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { candidateRequest } from './api';
import './telegram-handoff.css';

/** A bot entry keeps its intent through registration and the existing CV flow. */
export function TelegramHandoff({ available, compact = false }: { available: boolean; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState('');
  async function connect() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const result = await candidateRequest<{ url: string | null }>('telegram/link', {});
      if (!result.url) throw new Error('Не удалось подключить Telegram. Попробуйте ещё раз.');
      const url = new URL(result.url);
      if (url.protocol !== 'https:' || !['t.me', 'telegram.me'].includes(url.hostname)) {
        throw new Error('Не удалось открыть бота. Попробуйте ещё раз.');
      }
      setLink(url.toString());
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <section className={`cp-telegram-handoff${compact ? ' cp-telegram-compact' : ''}`} aria-labelledby="cp-telegram-handoff-title">
    <Send size={21} aria-hidden="true"/>
    <div><h2 id="cp-telegram-handoff-title">Уведомления в Telegram</h2>
      <p>Чтобы не пропустить новые вакансии.</p>
      {error && <p className="cp-error" role="alert">{error}</p>}
      {!available && <p role="status">Подключение временно недоступно. Можно продолжить здесь.</p>}
    </div>
    {available && (link
      ? <a href={link} className="cp-contact-link">Продолжить в Telegram<ArrowRight size={16}/></a>
      : <Button variant="secondary" disabled={busy} onClick={() => void connect()}>{busy ? 'Подключаем…' : 'Подключить Telegram'}</Button>)}
  </section>;
}
