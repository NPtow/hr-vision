import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { CandidateId } from '../agency-product/model';
import { dateTime, type Journey, type Person } from '../connected-journey/api';
import { Avatar } from './PeoplePanel';

const moscowDay = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso));
const time = (iso: string) => new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
const monthDate = (day: string) => new Date(day.slice(0, 7) + '-01T12:00:00Z');
const dayLabel = (day: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long', timeZone: 'UTC' }).format(new Date(day + 'T12:00:00Z'));

export function MeetingScheduler({ journey, person, onSelect, onBack, onBooked }: { journey: Journey; person?: Person; onSelect: (id: CandidateId) => void; onBack: () => void; onBooked: () => void }) {
  const people = Object.values(journey.state!.candidates) as Person[];
  const [slot, setSlot] = useState('');
  const [day, setDay] = useState('');
  const [month, setMonth] = useState(() => monthDate(moscowDay(new Date().toISOString())));
  const available = person?.freeSlots || [];
  useEffect(() => {
    const first = person?.freeSlots[0];
    const next = first ? moscowDay(first) : moscowDay(new Date().toISOString());
    setMonth(monthDate(next)); setDay(next); setSlot('');
  }, [person?.id]);
  // A slot can be taken by another tab while the calendar is open.
  useEffect(() => { if (slot && !available.includes(slot)) setSlot(''); }, [available.join('|'), slot]);
  const activeMeetings = people.filter(p => p.meeting && ['pending', 'confirmed', 'live'].includes(p.meeting.status)).sort((a, b) => a.meeting!.start.localeCompare(b.meeting!.start));
  const first = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), 1, 12));
  const offset = (first.getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 0)).getUTCDate();
  const dates = Array.from({ length: Math.ceil((days + offset) / 7) * 7 }, (_, i) => {
    const d = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), i - offset + 1, 12));
    return { key: d.toISOString().slice(0, 10), date: d, current: d.getUTCMonth() === month.getUTCMonth() };
  });
  const freeDays = new Set(available.map(moscowDay));
  const currentMonth = moscowDay(new Date().toISOString()).slice(0, 7);
  const shift = (delta: number) => setMonth(new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + delta, 1, 12)));
  const cannotSchedule = !person || !!journey.state!.closedBy || person.meeting?.status === 'live' || person.meeting?.status === 'completed';
  return <section className="ew-book-page"><button className="ew-text-back" onClick={onBack}><ArrowLeft size={16}/>Кандидаты</button><header className="ew-form-heading"><h1>{person?.meeting && ['pending', 'confirmed'].includes(person.meeting.status) ? 'Перенести встречу' : 'Назначить встречу'}</h1><p>{journey.state!.vacancy.role}</p></header>
    <div className="ew-book-card"><aside className="ew-book-person"><CalendarDays size={25}/><h2>Знакомство с кандидатом</h2><label>Кандидат<select value={person?.id || ''} onChange={e => onSelect(e.target.value as CandidateId)} disabled={journey.busy}><option value="" disabled>Выберите кандидата</option>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{person && <div className="ew-book-avatar"><Avatar name={person.name}/><strong>{person.name}</strong></div>}<div className="ew-book-meta"><span><Clock3 size={17}/>30 минут</span><span><Video size={17}/>Видеовстреча в HR Vision</span><span>Московское время · UTC+3</span></div>{person?.meeting && ['pending','confirmed'].includes(person.meeting.status) && <div className="ew-previous-slot"><span>Сейчас назначено</span><strong>{dateTime(person.meeting.start)}</strong></div>}</aside>
      <div className="ew-calendar"><header><h2>{new Intl.DateTimeFormat('ru-RU', { year: 'numeric', month: 'long', timeZone: 'UTC' }).format(month)}</h2><div><button disabled={month.toISOString().slice(0, 7) <= currentMonth} onClick={() => shift(-1)} aria-label="Предыдущий месяц"><ChevronLeft size={18}/></button><button onClick={() => shift(1)} aria-label="Следующий месяц"><ChevronRight size={18}/></button></div></header><div className="ew-weekdays">{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(w => <span key={w}>{w}</span>)}</div><div className="ew-month-days" aria-label="Дни со свободным временем">{dates.map(d => <button key={d.key} disabled={!d.current || !freeDays.has(d.key) || cannotSchedule} aria-label={dayLabel(d.key)} aria-pressed={day === d.key} className={`${!d.current ? 'ew-outside' : ''} ${freeDays.has(d.key) ? 'ew-day-free' : ''}`} onClick={() => { setDay(d.key); setSlot(''); }}>{d.date.getUTCDate()}</button>)}</div><p className="ew-calendar-note"><span/>Доступное время кандидата</p></div>
      <div className="ew-time-picker"><h2>{day ? dayLabel(day) : 'Свободное время'}</h2><div className="ew-time-buttons" role="radiogroup" aria-label="Время встречи">{available.filter(s => moscowDay(s) === day).map(s => <button role="radio" aria-checked={s === slot} key={s} disabled={cannotSchedule || journey.busy} onClick={() => setSlot(s)}>{time(s)}{s === slot && <Check size={14}/>}</button>)}</div>{!available.some(s => moscowDay(s) === day) && <p className="ew-time-empty">{available.length ? 'Выберите выделенный день в календаре.' : 'Кандидат пока не указал свободное время.'}</p>}{cannotSchedule && person && <p className="ew-time-empty">{journey.state!.closedBy ? 'Подбор завершён.' : 'У кандидата уже есть начавшаяся или завершённая встреча.'}</p>}</div>
      <footer className="ew-book-footer"><div>{slot ? <strong>{dateTime(slot)}</strong> : <span>Выберите удобное время</span>}<p>После выбора кандидат подтвердит встречу.</p></div><Button disabled={!slot || cannotSchedule || journey.busy} onClick={async () => { if (person && await journey.act('meeting.propose', person.id, { start: slot, previousMeetingId: person.meeting?.id || null })) onBooked(); }}>{journey.busy ? 'Сохраняем…' : 'Предложить время'}<ArrowRight size={16}/></Button></footer>
    </div>
    {!!activeMeetings.length && <section className="ew-upcoming"><h2>Назначенные встречи <span>{activeMeetings.length}</span></h2>{activeMeetings.map(p => <div key={p.id} className="ew-upcoming-row"><Avatar name={p.name}/><strong>{p.name}</strong><time>{dateTime(p.meeting!.start)}</time><span className="ew-status">{p.meeting!.status === 'pending' ? 'Ждём подтверждения' : p.meeting!.status === 'live' ? 'Идёт встреча' : 'Подтверждена'}</span></div>)}</section>}
  </section>;
}
