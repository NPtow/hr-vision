import { useEffect, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { CandidateId } from '../connected-journey/api';
import { candidates } from '../agency-product/model';
import { dateTime, type Journey, type Person } from '../connected-journey/api';
import { Avatar } from './PeoplePanel';

const moscowDay = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso));
const time = (iso: string) => new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
const dayLabel = (day: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long', timeZone: 'UTC' }).format(new Date(day + 'T12:00:00Z'));
const addDays = (day: string, count: number) => { const date = new Date(day + 'T12:00:00Z'); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10); };

export function MeetingScheduler({ journey, person, onSelect, onBack, onBooked }: { journey: Journey; person?: Person; onSelect: (id: CandidateId) => void; onBack: () => void; onBooked: () => void }) {
  const people = Object.values(journey.state!.candidates) as Person[];
  const today = moscowDay(new Date().toISOString());
  const [slot, setSlot] = useState('');
  const [day, setDay] = useState(today);
  const [week, setWeek] = useState(today);
  const available = person?.freeSlots || [];
  useEffect(() => {
    const first = person?.freeSlots[0];
    const next = first ? moscowDay(first) : moscowDay(new Date().toISOString());
    setWeek(next); setDay(next); setSlot('');
  }, [person?.id]);
  useEffect(() => { if (slot && !available.includes(slot)) setSlot(''); }, [available.join('|'), slot]);
  const otherMeetings = people.filter(p => p.id !== person?.id && p.meeting && ['pending', 'confirmed', 'live'].includes(p.meeting.status)).sort((a, b) => a.meeting!.start.localeCompare(b.meeting!.start));
  const dates = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const freeDays = new Set(available.map(moscowDay));
  const cannotSchedule = !person || !!journey.state!.closedBy || person.meeting?.status === 'live' || person.meeting?.status === 'completed';
  const profile = candidates.find(p => p.id === person?.id);
  const times = available.filter(s => moscowDay(s) === day);
  const shift = (delta: number) => { const next = addDays(week, delta); setWeek(next); setDay(dates.includes(day) ? next : day); setSlot(''); };
  return <section className="ew-book-page">
    <button className="ew-text-back" onClick={onBack}><ArrowLeft size={16}/>К кандидатам</button>
    <header className="ew-form-heading"><h1>{person?.meeting && ['pending', 'confirmed'].includes(person.meeting.status) ? 'Перенести встречу' : 'Назначить встречу'}</h1></header>
    <div className="ew-book-layout">
      <aside className="ew-book-person">
        {person && <><Avatar name={person.name} photo={person.photo}/><h2>{person.name}</h2><p>{profile?.role || journey.state!.vacancy.role}</p></>}
        <label><span className="sr-only">Кандидат</span><select value={person?.id || ''} onChange={e => onSelect(e.target.value as CandidateId)} disabled={journey.busy}><option value="" disabled>Выберите кандидата</option>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <div className="ew-book-meta"><strong>На нашей платформе</strong><span>30 минут</span><span>МСК · Москва, UTC+3</span></div>
        {person?.meeting && ['pending','confirmed'].includes(person.meeting.status) && <div className="ew-previous-slot"><span>Сейчас назначено</span><strong>{dateTime(person.meeting.start)}</strong></div>}
        {!!otherMeetings.length && <section className="ew-upcoming"><h3>Другие встречи</h3>{otherMeetings.map(p => <div key={p.id} className="ew-upcoming-row"><strong>{p.name}</strong><time>{dateTime(p.meeting!.start)}</time><span>{p.meeting!.status === 'pending' ? 'Ждём подтверждения' : p.meeting!.status === 'live' ? 'Идёт встреча' : 'Подтверждена'}</span></div>)}</section>}
      </aside>
      <section className="ew-calendar-card" aria-label="Выбор свободного времени">
        <header><div><h2>Свободное время</h2><p>{new Intl.DateTimeFormat('ru-RU', { year: 'numeric', month: 'long', timeZone: 'UTC' }).format(new Date(week + 'T12:00:00Z'))}</p></div><div className="ew-week-arrows"><button disabled={week <= today} onClick={() => shift(-7)} aria-label="Предыдущие семь дней"><ChevronLeft size={18}/></button><button onClick={() => shift(7)} aria-label="Следующие семь дней"><ChevronRight size={18}/></button></div></header>
        <div className="ew-days-strip" aria-label="Дни со свободным временем">{dates.map(d => <button key={d} disabled={d < today || !freeDays.has(d) || cannotSchedule} aria-label={dayLabel(d)} aria-pressed={day === d} onClick={() => { setDay(d); setSlot(''); }}>{new Intl.DateTimeFormat('ru-RU', { weekday: 'short', timeZone: 'UTC' }).format(new Date(d + 'T12:00:00Z'))} · {Number(d.slice(-2))}</button>)}</div>
        <div className="ew-time-picker"><header><h3>{dayLabel(day)}</h3><span className="ew-duration">30 мин</span></header><div className="ew-time-buttons" role="radiogroup" aria-label="Время встречи">{times.map(s => <button role="radio" aria-checked={s === slot} key={s} disabled={cannotSchedule || journey.busy} onClick={() => setSlot(s)}>{time(s)}</button>)}</div>{!times.length && <p className="ew-time-empty">{available.length ? 'Выберите день со свободным временем.' : 'Кандидат пока не указал свободное время.'}</p>}{cannotSchedule && person && <p className="ew-time-empty">{journey.state!.closedBy ? 'Подбор завершён.' : 'У кандидата уже есть начавшаяся или завершённая встреча.'}</p>}</div>
        <div className="ew-selected-slot"><span>{slot ? dateTime(slot) : 'Выберите удобное время'}</span><span>30 минут</span></div>
        <footer className="ew-book-footer"><p>Кандидат подтвердит время перед встречей.</p><Button disabled={!slot || cannotSchedule || journey.busy} onClick={async () => { if (person && await journey.act('meeting.propose', person.id, { start: slot, previousMeetingId: person.meeting?.id || null })) onBooked(); }}>{journey.busy ? 'Сохраняем…' : 'Предложить время'}</Button></footer>
      </section>
    </div>
  </section>;
}
