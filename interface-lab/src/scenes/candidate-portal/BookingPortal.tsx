import { useCallback, useEffect, useRef, useState } from 'react';
import { Calendar, CalendarDays, Check, Download, ExternalLink, FileText, Mail, Send, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { bookingAttempt, restoreBookingAttempt, type BookingAttempt } from './booking-attempt';
import { PooledBookingCalendar } from './PooledBookingCalendar';
import { googleCalendarUrl, downloadIcsFile } from './calendar-export';
import type { BookingSlot } from './pooled-booking-model';
import './booking-portal.css';

type Slot = BookingSlot;
type Booking = Slot & { slotId: string; status: string; canOpenCall: boolean };
type BookingState = { status: 'available' | 'booked'; candidateName: string; contactEmail: string; profileReady: boolean; timezone: string; slots: Slot[]; booking: Booking | null;
  delivery: { email: string | null; telegramAvailable: boolean; telegramLinked: boolean } };
const tokenKey = 'hr-vision-invitation:booking';
const attemptKey = 'hr-vision-invitation:booking-request';

function stored(key: string) { try { return sessionStorage.getItem(key) || ''; } catch { return ''; } }
function remember(key: string, value: string) { try { sessionStorage.setItem(key, value); } catch { /* This tab still works when storage is restricted. */ } }
function initialToken() { return new URLSearchParams(location.hash.slice(1)).get('booking') ?? stored(tokenKey); }
function previousAttempt(token: string) { return restoreBookingAttempt(stored(attemptKey), token); }

class BookingError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}
async function request<T>(path: string, body: unknown, signal: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 20000);
  try {
    const response = await fetch(`/api/hr/booking/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', signal: controller.signal,
    });
    const value = await response.json().catch(() => null);
    if (!response.ok) {
      const serverMessage = typeof value?.error === 'string' && value.error.length <= 300 ? value.error : '';
      const message = path === 'book' && [400, 409].includes(response.status) && serverMessage ? serverMessage
        : response.status === 410 ? 'Ссылка больше не действует. Попросите рекрутера новое приглашение.'
        : [400, 401, 403, 404].includes(response.status) ? 'Приглашение недоступно. Проверьте ссылку из сообщения рекрутера.'
          : response.status === 429 ? 'Слишком много запросов. Попробуйте ещё раз через минуту.'
            : 'Не удалось связаться с сервисом. Попробуйте ещё раз.';
      throw new BookingError(response.status, message);
    }
    if (!value || typeof value !== 'object') throw new Error('invalid_response');
    return value as T;
  } catch (cause) {
    if (cause instanceof BookingError) throw cause;
    throw new BookingError(0, 'Ответ не получен. Проверьте интернет и попробуйте ещё раз.');
  } finally {
    window.clearTimeout(timeout); signal.removeEventListener('abort', abort);
  }
}
function unavailable(cause: unknown) { return cause instanceof BookingError && [400, 401, 403, 404, 410].includes(cause.status); }
function message(cause: unknown) { return cause instanceof BookingError ? cause.message : 'Не удалось загрузить расписание. Попробуйте ещё раз.'; }
function label(value: string, timezone: string, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone: timezone }).format(new Date(value));
}
function time(value: string, timezone: string) { return label(value, timezone, { hour: '2-digit', minute: '2-digit' }); }
/** Public booking capability: no login, CV upload, or candidate session is needed. */
export function BookingPortal() {
  const [token, setToken] = useState(initialToken);
  useEffect(() => {
    const changed = () => {
      const supplied = new URLSearchParams(location.hash.slice(1)).get('booking');
      if (supplied === null) return;
      // Keep the secret out of history, referrers, and subsequent page URLs.
      const url = new URL(location.href); url.hash = ''; history.replaceState(history.state, '', url);
      remember(tokenKey, supplied); setToken(supplied);
    };
    changed(); window.addEventListener('hashchange', changed); window.addEventListener('popstate', changed);
    return () => { window.removeEventListener('hashchange', changed); window.removeEventListener('popstate', changed); };
  }, []);
  return <div className="agency-product-theme bp-root">
    <header className="bp-header"><img src={logo} alt="HR Vision"/></header>
    <main className="bp-main">
      {token ? <BookingInvitation key={token} token={token}/> : <section className="bp-surface"><h1>Откройте приглашение</h1><p className="bp-muted">Перейдите по полной ссылке из сообщения рекрутера. Здесь вы сможете выбрать время интервью.</p></section>}
    </main>
  </div>;
}

function BookingInvitation({ token }: { token: string }) {
  const [state, setState] = useState<BookingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [entering, setEntering] = useState(false);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(() => previousAttempt(token)?.slotId || '');
  const [email, setEmail] = useState(() => previousAttempt(token)?.email || '');
  const [consent, setConsent] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [editingEmail, setEditingEmail] = useState(false);
  const [connectingTelegram, setConnectingTelegram] = useState(false);
  const [tgDeclined, setTgDeclined] = useState(false);
  const [timezone, setTimezone] = useState('Europe/Moscow');
  const attempt = useRef<BookingAttempt | null>(previousAttempt(token));
  const controller = useRef(new AbortController());
  const sequence = useRef(0);
  const pending = useRef(false);
  const refresh = useCallback(async (keepError = false) => {
    const current = ++sequence.current;
    const signal = controller.current.signal;
    setLoading(true);
    try {
      const next = await request<BookingState>('state', { token }, signal);
      if (signal.aborted || current !== sequence.current) return;
      if (!Array.isArray(next.slots) || !['available', 'booked'].includes(next.status)) throw new Error('invalid_state');
      setState(next); setSelectedId(value => next.slots.some(slot => slot.id === value) ? value : '');
      setEmail(value => value || next.contactEmail || '');
      if (!keepError) setError('');
    } catch (cause) {
      if (signal.aborted || current !== sequence.current) return;
      if (unavailable(cause)) setState(null);
      setError(message(cause));
    } finally { if (!signal.aborted && current === sequence.current) setLoading(false); }
  }, [token]);
  useEffect(() => {
    controller.current = new AbortController(); void refresh();
    return () => { controller.current.abort(); sequence.current++; };
  }, [refresh]);
  useEffect(() => {
    if (!state?.booking) return;
    const onReturn = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('focus', onReturn);
    let checks = 0;
    const timer = ['pending', 'sending'].includes(state.delivery.email || '') ? window.setInterval(() => {
      if (++checks >= 6) window.clearInterval(timer);
      void refresh();
    }, 5000) : undefined;
    return () => { window.removeEventListener('focus', onReturn); window.clearInterval(timer); };
  }, [state?.booking?.id, state?.delivery.email, refresh]);
  async function book() {
    const slotId = state?.booking && !state.profileReady ? state.booking.slotId : selectedId;
    if (pending.current || !slotId || !email.trim() || !consent) return;
    pending.current = true; sequence.current++; setBusy(true); setError('');
    const signal = controller.current.signal;
    attempt.current = bookingAttempt(attempt.current, token, slotId, email, () => crypto.randomUUID());
    remember(attemptKey, JSON.stringify(attempt.current));
    try {
      const next = await request<BookingState>('book', attempt.current, signal);
      if (signal.aborted) return;
      if (next.status !== 'booked' || !next.booking) throw new Error('invalid_booking');
      setState(next); setSelectedId(''); setUncertain(false); remember(attemptKey, '');
    } catch (cause) {
      if (signal.aborted) return;
      if (cause instanceof BookingError && [401, 403, 404, 410].includes(cause.status)) setState(null);
      if (cause instanceof BookingError && cause.status === 409) {
        setUncertain(false); setError(message(cause)); await refresh(true);
      } else {
        setUncertain(!(cause instanceof BookingError) || cause.status === 0 || cause.status >= 500);
        setError(message(cause));
      }
      // Keep requestId after an uncertain response: retry cannot create a second booking.
    } finally { pending.current = false; if (!signal.aborted) { setBusy(false); setLoading(false); } }
  }
  async function openCall() {
    if (pending.current) return;
    pending.current = true; setEntering(true); setError('');
    const signal = controller.current.signal;
    try {
      const result = await request<{ url: string }>('call', { token }, signal);
      if (signal.aborted) return;
      const url = new URL(result.url, location.href);
      if (url.origin !== location.origin || url.searchParams.get('cp') !== 'guest' || !new URLSearchParams(url.hash.slice(1)).get('invite')) throw new Error('invalid_call_url');
      location.assign(url.href);
    } catch (cause) {
      if (!signal.aborted) {
        if (unavailable(cause)) setState(null);
        setError(cause instanceof BookingError && cause.status === 409 ? 'Встреча пока недоступна. Обновите приглашение или свяжитесь с рекрутером.' : message(cause));
      }
    } finally { pending.current = false; if (!signal.aborted) setEntering(false); }
  }
  async function connectTelegram() {
    if (connectingTelegram) return;
    setConnectingTelegram(true); setError('');
    try {
      const result = await request<{ status: string; url: string | null }>('telegram', { token }, controller.current.signal);
      if (controller.current.signal.aborted) return;
      if (result.status === 'linked') { await refresh(); return; }
      const url = new URL(result.url || '');
      if (url.protocol !== 'https:' || url.hostname !== 't.me' || !url.searchParams.get('start')?.startsWith('bk_')) throw new Error('invalid_telegram_link');
      location.assign(url.href);
    } catch { if (!controller.current.signal.aborted) setError('Не удалось подключить Telegram. Попробуйте ещё раз.'); }
    finally { if (!controller.current.signal.aborted) setConnectingTelegram(false); }
  }
  const selected = state?.slots.find(slot => slot.id === selectedId);
  const booking = state?.booking;
  const contactFields = <div className="bp-booking-contact">
        {state?.contactEmail && !editingEmail ? <div className="bp-known-email"><span><small>Приглашение придёт на</small>{email}</span><button type="button" disabled={busy || loading || uncertain} onClick={() => setEditingEmail(true)}>Изменить</button></div> : <>
          <label htmlFor="bp-email">Почта для приглашения</label>
          <input id="bp-email" type="email" name="email" autoComplete="email" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={busy || loading || uncertain} placeholder="you@example.com"/>
        </>}
        <label className="bp-consent"><input type="checkbox" name="consentProfile" required checked={consent} onChange={event => setConsent(event.target.checked)} disabled={busy || loading || uncertain}/><span>Согласен на интервью и подготовку профиля из резюме.</span></label>
      </div>;
  if (!state) return <section className="bp-surface" aria-busy={loading}>
    <span className="bp-symbol"><CalendarDays size={26}/></span>
    <h1>{loading ? 'Открываем приглашение…' : 'Приглашение недоступно'}</h1>
    {loading ? <p className="bp-muted" role="status">Проверяем свободное время рекрутеров.</p> : <><p className="bp-error" role="alert">{error}</p><Button variant="secondary" onClick={() => void refresh()}>Проверить ещё раз</Button></>}
  </section>;
  if (booking) {
    const eventDetails = {
      title: 'Интервью-знакомство · МашаДаша',
      description: `30-минутное видеоинтервью с рекрутером МашаДаша.\nВход: ${location.origin}/interview?booking=${token}\nНапоминание придёт за 1 час.`,
      location: location.href,
      start: new Date(booking.start),
      end: new Date(booking.end),
    };

    return <section className="bp-surface bp-confirmation">
      <span className="bp-symbol bp-success"><Check size={26}/></span>
      {booking.status === 'completed' ? <>
        <h1>Интервью завершено</h1>
        <div className="bp-debrief-profile">
          <div className="bp-debrief-header">
            <h3>Профиль</h3>
            <button type="button" className="bp-resume-badge" onClick={() => alert('Резюме кандидата сохранено в профиле.')}>
              <FileText size={13} color="#6756ce"/>Резюме.pdf
            </button>
          </div>
          <div className="bp-debrief-summary">
            <div><strong>{state.candidateName ? 'Квалифицированный специалист' : 'Специалист'}</strong> · По договоренности · Удаленно</div>
            <small>Данные сохранены и подтверждены на интервью</small>
          </div>
        </div>
        <div className="bp-timeline-compact">
          <span className="bp-timeline-pill bp-done">✓ Интервью</span>
          <span className="bp-timeline-arrow">→</span>
          <span className="bp-timeline-pill">Матчинг (до 24ч)</span>
          <span className="bp-timeline-arrow">→</span>
          <span className="bp-timeline-pill">Предложения</span>
        </div>
        {state.profileReady && state.delivery.telegramAvailable && (
          <div className="bp-channel-card" data-mode={state.delivery.telegramLinked ? 'linked' : tgDeclined ? 'email' : 'default'}>
            {state.delivery.telegramLinked ? (
              <>
                <h3>✓ Telegram подключён</h3>
                <p>Вакансии и приглашения поступят в чат с ботом.</p>
              </>
            ) : tgDeclined ? (
              <>
                <h3>Предложения придут на почту</h3>
                <p>Подходящие вакансии отправим на <strong>{state.contactEmail}</strong>.</p>
                <div className="bp-channel-reconnect">
                  <button type="button" onClick={() => setTgDeclined(false)}>В Telegram</button>
                </div>
              </>
            ) : (
              <>
                <h3>Предложения о работе</h3>
                <p>Пришлём вакансии с вилкой и условиями прямо в Telegram.</p>
                <div className="bp-channel-actions">
                  <Button variant="default" disabled={connectingTelegram} onClick={() => void connectTelegram()}>
                    {connectingTelegram ? 'Подключаем…' : 'Получать в Telegram'}
                  </Button>
                  <button type="button" className="bp-optout-btn" onClick={() => setTgDeclined(true)}>
                    На почту
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </> : <>
        <h1>Вы записаны</h1>
        <div className="bp-booked-time">
          <strong>{label(booking.start, timezone, { day: 'numeric', month: 'long', weekday: 'long' })}</strong>
          <span>{time(booking.start, timezone)}–{time(booking.end, timezone)}</span>
        </div>
        {state.profileReady && state.delivery.telegramAvailable && (
          <div className="bp-channel-card" data-mode={state.delivery.telegramLinked ? 'linked' : tgDeclined ? 'email' : 'default'}>
            {state.delivery.telegramLinked ? (
              <>
                <h3>✓ Telegram подключён</h3>
                <p>Бот пришлёт ссылку на вход за 1 час до встречи.</p>
              </>
            ) : tgDeclined ? (
              <>
                <h3>Напоминание придёт на почту</h3>
                <p>Ссылку отправим на <strong>{state.contactEmail}</strong> за 1 час до встречи.</p>
                <div className="bp-channel-reconnect">
                  <button type="button" onClick={() => setTgDeclined(false)}>В Telegram</button>
                </div>
              </>
            ) : (
              <>
                <h3>Напоминание за 1 час</h3>
                <p>Пришлём ссылку прямо в Telegram, чтобы не искать в почте.</p>
                <div className="bp-channel-actions">
                  <Button variant="default" disabled={connectingTelegram} onClick={() => void connectTelegram()}>
                    {connectingTelegram ? 'Подключаем…' : 'Подключить Telegram'}
                  </Button>
                  <button type="button" className="bp-optout-btn" onClick={() => setTgDeclined(true)}>
                    На почту
                  </button>
                </div>
              </>
            )}
          </div>
        )}
        {booking.canOpenCall && (
          <div className="bp-call-box">
            <span>Вход в видеокомнату</span>
            <Button disabled={entering} onClick={() => void openCall()}>
              <Video size={15}/>{entering ? 'Открываем…' : 'Войти'}
            </Button>
          </div>
        )}
      </>}
      {!state.profileReady && booking.slotId && <form className="bp-profile-confirmation" onSubmit={event => { event.preventDefault(); void book(); }} aria-busy={busy}>
        <h2>Подготовим ваш профиль</h2>
        {contactFields}
        <div className="bp-actions"><Button type="submit" disabled={!email.trim() || !consent || busy}>{busy ? 'Сохраняем…' : uncertain ? 'Проверить подтверждение' : 'Подтвердить профиль'}</Button></div>
      </form>}
      {error && <p className="bp-error" role="alert">{error}</p>}
    </section>;
  }
  return <section className="pbc-surface bp-calendar-surface">
    <div className="bp-greeting"><h1>{state.candidateName.trim() ? `Привет, ${state.candidateName.trim().split(/\s+/)[0]}` : 'Выберите время'}</h1><p>Когда удобно поговорить?</p></div>
    {error && <p className="bp-error" role="alert">{error}</p>}
    {!state.slots.length ? <div className="bp-empty"><h2>Пока нет свободного времени</h2><p className="bp-muted">Вернитесь по этой ссылке позже.</p><Button variant="secondary" disabled={loading} onClick={() => void refresh()}>{loading ? 'Проверяем…' : 'Проверить расписание'}</Button></div> : <form onSubmit={event => { event.preventDefault(); void book(); }} aria-busy={busy || loading}>
      <PooledBookingCalendar slots={state.slots} selectedId={selectedId} onSelect={id => { setSelectedId(id); setError(''); }}
        timezone={timezone} onTimezone={setTimezone} disabled={busy || loading || uncertain} action={<div className="bp-inline-booking">
          {selected && <p className="bp-selection" aria-live="polite">{label(selected.start, timezone, { day: 'numeric', month: 'long' })}, {time(selected.start, timezone)} · 30 минут</p>}
          {contactFields}
          {uncertain && <p className="bp-muted" role="status">Проверим ту же запись — время и почта сохранены.</p>}
          <button className="pbc-primary bp-submit" type="submit" disabled={!selected || !email.trim() || !consent || busy || loading}>{busy ? 'Бронируем…' : uncertain ? 'Проверить запись' : 'Записаться'}</button>
        </div>}/>
    </form>}
  </section>;
}
