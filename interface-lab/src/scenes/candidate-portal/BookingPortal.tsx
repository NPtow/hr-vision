import { useCallback, useEffect, useRef, useState } from 'react';
import { CalendarDays, Check, Clock3, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import './booking-portal.css';

type Slot = { id: string; start: string; end: string; recruiterName: string };
type Booking = Slot & { status: string; canOpenCall: boolean };
type BookingState = { status: 'available' | 'booked'; candidateName: string; timezone: string; slots: Slot[]; booking: Booking | null };
type Attempt = { token: string; slotId: string; requestId: string };
const tokenKey = 'hr-vision-invitation:booking';
const attemptKey = 'hr-vision-invitation:booking-request';

function stored(key: string) { try { return sessionStorage.getItem(key) || ''; } catch { return ''; } }
function remember(key: string, value: string) { try { sessionStorage.setItem(key, value); } catch { /* This tab still works when storage is restricted. */ } }
function initialToken() { return new URLSearchParams(location.hash.slice(1)).get('booking') ?? stored(tokenKey); }
function previousAttempt(token: string): Attempt | null {
  try {
    const value = JSON.parse(stored(attemptKey)) as Attempt;
    return value?.token === token && typeof value.slotId === 'string' && typeof value.requestId === 'string' ? value : null;
  } catch { return null; }
}

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
      const message = response.status === 410 ? 'Ссылка больше не действует. Попросите рекрутера новое приглашение.'
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
function zone(value?: string) {
  try { new Intl.DateTimeFormat('ru', { timeZone: value || 'Europe/Moscow' }).format(); return value || 'Europe/Moscow'; }
  catch { return 'Europe/Moscow'; }
}
function label(value: string, timezone: string, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone: timezone }).format(new Date(value));
}
function time(value: string, timezone: string) { return label(value, timezone, { hour: '2-digit', minute: '2-digit' }); }
function days(slots: Slot[], timezone: string) {
  const groups = new Map<string, Slot[]>();
  for (const slot of [...slots].filter(slot => Number.isFinite(Date.parse(slot.start)) && Number.isFinite(Date.parse(slot.end))).sort((a, b) => Date.parse(a.start) - Date.parse(b.start) || a.id.localeCompare(b.id))) {
    const day = label(slot.start, timezone, { day: 'numeric', month: 'long', weekday: 'long', year: 'numeric' });
    groups.set(day, [...(groups.get(day) || []), slot]);
  }
  return [...groups];
}

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
  const attempt = useRef<Attempt | null>(previousAttempt(token));
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
    if (pending.current || !selectedId) return;
    pending.current = true; sequence.current++; setBusy(true); setError('');
    const signal = controller.current.signal;
    if (!attempt.current || attempt.current.slotId !== selectedId) {
      attempt.current = { token, slotId: selectedId, requestId: crypto.randomUUID() };
      remember(attemptKey, JSON.stringify(attempt.current));
    }
    try {
      const next = await request<BookingState>('book', attempt.current, signal);
      if (signal.aborted) return;
      if (next.status !== 'booked' || !next.booking) throw new Error('invalid_booking');
      setState(next); setSelectedId('');
    } catch (cause) {
      if (signal.aborted) return;
      if (unavailable(cause)) setState(null);
      if (cause instanceof BookingError && cause.status === 409) {
        attempt.current = null; remember(attemptKey, ''); setSelectedId('');
        setError('Это время уже занято. Выберите другой свободный слот.'); await refresh(true);
      } else setError(message(cause));
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
  const timezone = zone(state?.timezone);
  const groups = days(state?.slots || [], timezone);
  const selected = state?.slots.find(slot => slot.id === selectedId);
  const booking = state?.booking;
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
      <p className="bp-muted">{timezone === 'Europe/Moscow' ? 'Московское время' : timezone} · 30 минут<br/>Ваш рекрутер — {booking.recruiterName}.</p>
      <p>Здесь можно открыть встречу. Резюме уже у рекрутера — регистрироваться перед разговором не нужно.</p>
      {booking.canOpenCall && <Button disabled={entering} onClick={() => void openCall()}><Video size={18}/>{entering ? 'Открываем встречу…' : 'Перейти к встрече'}</Button>}
    </>}
    {error && <p className="bp-error" role="alert">{error}</p>}
  </section>;
  return <section className="bp-surface">
    <div className="bp-heading"><span className="bp-symbol"><CalendarDays size={26}/></span><h1>Выберите время интервью</h1><p className="bp-muted">{state.candidateName ? `${state.candidateName}, п` : 'П'}оговорим о вашем опыте и подходящей работе.</p><p className="bp-duration"><Clock3 size={16}/>30 минут · {timezone === 'Europe/Moscow' ? 'московское время' : timezone}</p></div>
    {error && <p className="bp-error" role="alert">{error}</p>}
    {!groups.length ? <div className="bp-empty"><h2>Пока нет свободного времени</h2><p className="bp-muted">Рекрутеры ещё не открыли новые слоты. Вернитесь по этой ссылке позже.</p><Button variant="secondary" disabled={loading} onClick={() => void refresh()}>{loading ? 'Проверяем…' : 'Проверить расписание'}</Button></div> : <form onSubmit={event => { event.preventDefault(); void book(); }} aria-busy={busy || loading}>
      <div className="bp-days">{groups.map(([day, slots]) => <fieldset className="bp-day" key={day} disabled={busy || loading}><legend>{day}</legend><div className="bp-slots">{slots.map(slot => <label className="bp-slot" key={slot.id} data-selected={selectedId === slot.id}>
        <input type="radio" name="interview-slot" value={slot.id} checked={selectedId === slot.id} onChange={() => { setSelectedId(slot.id); setError(''); }} aria-label={`${day}, ${time(slot.start, timezone)}–${time(slot.end, timezone)}, ${slot.recruiterName}`}/>
        <span className="bp-slot-time">{time(slot.start, timezone)}<span aria-hidden="true" className="bp-slot-check">{selectedId === slot.id && <Check size={15}/>}</span></span><span className="bp-slot-recruiter">{slot.recruiterName}</span>
      </label>)}</div></fieldset>)}</div>
      <div className="bp-actions"><p className="bp-muted" aria-live="polite">{selected ? `${label(selected.start, timezone, { day: 'numeric', month: 'long' })}, ${time(selected.start, timezone)} · ${selected.recruiterName}` : 'Выберите удобный день и время'}</p><Button type="submit" disabled={!selected || busy || loading}>{busy ? 'Бронируем…' : 'Записаться на интервью'}</Button></div>
    </form>}
  </section>;
}
