import { useRef, useState, type FormEvent } from 'react';
import { DayPicker, TZDate } from '@daypicker/react';
import { ru } from '@daypicker/react/locale';
import { Plus, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { moscowInput, type AvailabilityBlock, type AvailabilityDate, type DayInterval, type RecruiterSlot } from './api';
import '@daypicker/react/style.css';
import './date-availability.css';

const zone = 'Europe/Moscow';
const dateKey = (value: Date) => moscowInput(value).slice(0, 10);
const zonedDay = (value: string) => new TZDate(`${value}T12:00:00+03:00`, zone);
const dayLabel = (date: Date) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long', timeZone: zone }).format(date);

export function DateAvailability({ blocks, dates, slots, busy, run, onEditing }: {
  blocks: AvailabilityBlock[]; dates: AvailabilityDate[]; slots: RecruiterSlot[]; busy: boolean;
  run: (path: string, body: unknown) => Promise<boolean>; onEditing?: (value: boolean) => void;
}) {
  const today = zonedDay(dateKey(new Date()));
  const last = new TZDate(today.getTime() + 370 * 86400_000, zone);
  const [selected, setSelected] = useState<Date>(today);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DayInterval[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const pending = useRef(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const key = dateKey(selected);
  const override = dates.find(item => item.date === key);
  const weekday = (new TZDate(selected, zone).getDay() + 6) % 7;
  const weekly = blocks.filter(block => block.startDate <= key && block.weekdays.includes(weekday))
    .map(({ startTime, endTime }) => ({ startTime, endTime })).sort((a, b) => a.startTime.localeCompare(b.startTime));
  const intervals = override?.intervals ?? weekly;
  const appointments = slots.filter(slot => slot.booked && dateKey(new Date(slot.start)) === key);
  const locked = busy || submitting;
  function finishEditing() { setEditing(false); onEditing?.(false); requestAnimationFrame(() => editButton.current?.focus()); }
  function startEditing() {
    setDraft(intervals.length ? intervals.map(item => ({ ...item })) : [{ startTime: '', endTime: '' }]);
    setEditing(true); onEditing?.(true); setError(''); setNotice('');
  }
  async function save(value: DayInterval[] | null) {
    if (locked || pending.current) return;
    pending.current = true; setSubmitting(true); setError(''); setNotice('');
    try {
      if (await run('availability/date', { date: key, intervals: value })) {
        finishEditing(); setNotice(value === null ? 'Возвращено обычное расписание.' : 'Расписание на эту дату сохранено.');
      } else setError('Не удалось сохранить. Проверьте интервалы и попробуйте ещё раз.');
    } catch { setError('Не удалось сохранить. Попробуйте ещё раз.'); }
    finally { pending.current = false; setSubmitting(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    const sorted = [...draft].sort((a, b) => a.startTime.localeCompare(b.startTime));
    if (sorted.some((item, index) => !item.startTime || !item.endTime || item.startTime >= item.endTime ||
      (index > 0 && sorted[index - 1].endTime > item.startTime))) {
      setError('Проверьте время: интервалы не должны пересекаться.'); return;
    }
    void save(sorted);
  }
  return <div className="rp-date-schedule">
    <div className="rp-date-calendar">
      <DayPicker mode="single" required selected={selected} defaultMonth={selected} locale={ru} timeZone={zone}
        today={today} startMonth={today} endMonth={last} navLayout="around" fixedWeeks
        disabled={editing || locked ? true : [{ before: today }, { after: last }]} disableNavigation={editing || locked}
        onSelect={day => { setSelected(day); setError(''); setNotice(''); }}
        modifiers={{ custom: dates.filter(item => item.intervals.length).map(item => zonedDay(item.date)), closed: dates.filter(item => !item.intervals.length).map(item => zonedDay(item.date)) }}
        modifiersClassNames={{ custom: 'rp-date-custom', closed: 'rp-date-closed' }}
        labels={{ labelDayButton: day => `${dayLabel(day)}${dates.some(item => item.date === dateKey(day)) ? ', своё расписание' : ''}` }}/>
      <p className="rp-date-calendar-help">Время московское · интервью по 30 минут</p>
    </div>
    <div className="rp-date-detail" aria-busy={locked}>
      <h3>{dayLabel(selected)}</h3>
      {!editing ? <>
        <p className="rp-date-kind">{override ? 'Своё расписание на день' : weekly.length ? 'По недельному расписанию' : 'Время ещё не задано'}</p>
        <div className="rp-date-times">{intervals.length ? intervals.map(item => <span key={item.startTime}>{item.startTime}–{item.endTime}</span>) : <span className="rp-date-off">Без интервью</span>}</div>
        <div className="rp-date-actions"><Button ref={editButton} type="button" disabled={locked} onClick={startEditing}>{intervals.length || override ? 'Изменить время' : 'Добавить время'}</Button>
          {override && <Button type="button" variant="ghost" disabled={locked} onClick={() => void save(null)}>Вернуть обычное</Button>}</div>
      </> : <form onSubmit={submit} className="rp-date-form">
        <p className="rp-date-kind">Только на эту дату</p>
        {draft.map((item, index) => <div className="rp-date-range" key={index}>
          <label>С<input autoFocus={index === 0} aria-label={`Начало интервала ${index + 1}`} required type="time" step={1800} disabled={locked} value={item.startTime} onChange={event => { setError(''); setDraft(current => current.map((row, i) => i === index ? { ...row, startTime: event.target.value } : row)); }}/></label>
          <label>До<input aria-label={`Конец интервала ${index + 1}`} required type="time" step={1800} disabled={locked} value={item.endTime} onChange={event => { setError(''); setDraft(current => current.map((row, i) => i === index ? { ...row, endTime: event.target.value } : row)); }}/></label>
          <Button type="button" variant="ghost" size="icon" aria-label={`Убрать интервал ${index + 1}`} disabled={locked} onClick={() => { setDraft(current => current.filter((_, i) => i !== index)); setError(''); }}><X size={18}/></Button>
        </div>)}
        {!draft.length && <p className="rp-date-off">Новые интервью в этот день недоступны.</p>}
        <div className="rp-date-actions"><Button type="button" variant="ghost" disabled={locked || draft.length >= 24} onClick={() => setDraft(current => [...current, { startTime: '', endTime: '' }])}><Plus size={16}/>Добавить интервал</Button>
          {draft.length > 0 && <Button type="button" variant="ghost" disabled={locked} onClick={() => { setDraft([]); setError(''); }}>Закрыть день</Button>}</div>
        <div className="rp-date-actions rp-date-save"><Button type="submit" disabled={locked}>{submitting ? 'Сохраняем…' : 'Сохранить день'}</Button><Button type="button" variant="ghost" disabled={locked} onClick={() => { finishEditing(); setError(''); }}>Отмена</Button></div>
      </form>}
      {appointments.length > 0 && <p className="rp-date-bookings">Назначенные встречи сохраняются: {appointments.map(slot => moscowInput(new Date(slot.start)).slice(11)).join(', ')}.</p>}
      {error && <p className="rp-error" role="alert">{error}</p>}
      {notice && <p className="rp-schedule-status" role="status">{notice}</p>}
    </div>
  </div>;
}
