import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, CalendarDays, Check, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { bookingAttempt, restoreBookingAttempt, type BookingAttempt } from './booking-attempt';
import { PooledBookingCalendar } from './PooledBookingCalendar';
import type { BookingSlot } from './pooled-booking-model';
import './booking-portal.css';

type Slot = BookingSlot;
type Booking = Slot & { slotId: string; status: string; canOpenCall: boolean };
type BookingState = { status: 'available' | 'booked'; candidateName: string; profileReady: boolean; timezone: string; slots: Slot[]; booking: Booking | null };
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
  const [stage, setStage] = useState<'time' | 'details'>('time');
  const [timezone, setTimezone] = useState('Europe/Moscow');
  const detailsHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (stage === 'details') detailsHeading.current?.focus(); }, [stage]);
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
      if (keepError && !next.booking) setStage('time');
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
  const selected = state?.slots.find(slot => slot.id === selectedId);
  const booking = state?.booking;
  const contactFields = <div className="bp-booking-contact">
        <label htmlFor="bp-email">Ваша почта</label>
        <input id="bp-email" type="email" name="email" autoComplete="email" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={busy || loading || uncertain} aria-describedby="bp-email-help"/>
        <p className="bp-muted" id="bp-email-help">На созвоне рекрутер отправит сюда ссылку на ваш профиль, логин и пароль.</p>
        <label className="bp-consent"><input type="checkbox" name="consentProfile" required checked={consent} onChange={event => setConsent(event.target.checked)} disabled={busy || loading}/><span>Согласен на интервью и подготовку моего профиля HR Vision из резюме.</span></label>
      </div>;
  if (!state) return <section className="bp-surface" aria-busy={loading}>
    <span className="bp-symbol"><CalendarDays size={26}/></span>
    <h1>{loading ? 'Открываем приглашение…' : 'Приглашение недоступно'}</h1>
    {loading ? <p className="bp-muted" role="status">Проверяем свободное время рекрутеров.</p> : <><p className="bp-error" role="alert">{error}</p><Button variant="secondary" onClick={() => void refresh()}>Проверить ещё раз</Button></>}
  </section>;
  if (booking) return <section className="bp-surface bp-confirmation">
    <span className="bp-symbol bp-success"><Check size={27}/></span>
    <h1>{booking.status === 'completed' ? 'Спасибо за разговор' : 'Вы записаны на интервью'}</h1>
    {booking.status === 'completed' ? <p className="bp-muted">Рекрутер пришлёт ссылку на ваш готовый профиль.</p> : <>
      <div className="bp-booked-time"><strong>{label(booking.start, timezone, { day: 'numeric', month: 'long', weekday: 'long' })}</strong><span>{time(booking.start, timezone)}–{time(booking.end, timezone)}</span></div>
      <p className="bp-muted">{timezone === 'Europe/Moscow' ? 'Московское время' : timezone} · 30 минут</p>
      <p>{state.profileReady ? 'Профиль подготовлен из вашего резюме. На созвоне рекрутер отправит вам на почту ссылку и данные для входа.' : 'На созвоне рекрутер поможет получить доступ к вашему профилю.'}</p>
      {booking.canOpenCall && <Button disabled={entering} onClick={() => void openCall()}><Video size={18}/>{entering ? 'Открываем встречу…' : 'Перейти к встрече'}</Button>}
    </>}
    {!state.profileReady && booking.slotId && <form className="bp-profile-confirmation" onSubmit={event => { event.preventDefault(); void book(); }} aria-busy={busy}>
      <h2>Подготовим ваш профиль</h2>
      {contactFields}
      <div className="bp-actions"><Button type="submit" disabled={!email.trim() || !consent || busy}>{busy ? 'Сохраняем…' : uncertain ? 'Проверить подтверждение' : 'Подтвердить профиль'}</Button></div>
    </form>}
    {error && <p className="bp-error" role="alert">{error}</p>}
  </section>;
  return <section className={`pbc-surface bp-calendar-surface${stage === 'details' && selected ? ' bp-details' : ''}`}>
    {error && <p className="bp-error" role="alert">{error}</p>}
    {stage === 'details' && selected ? <form onSubmit={event => { event.preventDefault(); void book(); }} aria-busy={busy || loading}>
      <h1 className="pbc-sr-only" tabIndex={-1} ref={detailsHeading}>Подтвердите запись</h1>
      <button className="pbc-back" type="button" disabled={busy || loading || uncertain} onClick={() => setStage('time')}><ArrowLeft size={17}/>Изменить время</button>
      <div className="bp-selection"><CalendarDays size={22}/><div><strong>{label(selected.start, timezone, { day: 'numeric', month: 'long' })}, {time(selected.start, timezone)}</strong><span>30 минут · {timezone === 'Europe/Moscow' ? 'московское время' : timezone}</span></div></div>
      {contactFields}
      {uncertain && <p className="bp-muted" role="status">Проверим ту же запись — время и почта сохранены.</p>}
      <button className="pbc-primary bp-submit" type="submit" disabled={!email.trim() || !consent || busy || loading}>{busy ? 'Бронируем…' : uncertain ? 'Проверить запись' : 'Подтвердить запись'}</button>
    </form> : !state.slots.length ? <div className="bp-empty"><h2>Пока нет свободного времени</h2><p className="bp-muted">Вернитесь по этой ссылке позже.</p><Button variant="secondary" disabled={loading} onClick={() => void refresh()}>{loading ? 'Проверяем…' : 'Проверить расписание'}</Button></div> :
      <PooledBookingCalendar slots={state.slots} selectedId={selectedId} onSelect={id => { setSelectedId(id); setError(''); }}
        timezone={timezone} onTimezone={setTimezone} disabled={busy || loading || uncertain} onContinue={() => setStage('details')}/>}
  </section>;
}
