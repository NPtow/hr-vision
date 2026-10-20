import { useMemo, useRef, useState, type FormEvent } from 'react';
import { DayPicker, TZDate } from '@daypicker/react';
import { ru } from '@daypicker/react/locale';
import { Plus, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { Availability, AvailabilitySchedule, DayAvailability, WeeklyAvailability } from './api';
import { availabilityForDate, validateDayIntervals } from './availability-model';
import { scheduleDateKey, scheduleLabel } from './schedule-time';
import '@daypicker/react/style.css';

const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const dayNames = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const empty: AvailabilitySchedule = { weekly: [], dates: [] };
const ruleDays = (rule: WeeklyAvailability) => rule.weekdays.length === 7 ? 'Каждый день'
  : [...rule.weekdays].sort().join(',') === '0,1,2,3,4' ? 'Понедельник — пятница' : rule.weekdays.map(i => days[i]).join(', ');

/** Uses the recruiter's calendar/date editor and weekly summary interaction. */
export function CandidateAvailability({ initial, schedule = empty, timezone, busy, onSave, onEditing }: {
  initial: Availability[]; schedule?: AvailabilitySchedule; timezone: string; busy: boolean;
  onSave: (schedule: AvailabilitySchedule) => Promise<boolean>; onEditing: (value: boolean) => void;
}) {
  const dayDate = (key: string) => { const [year, month, day] = key.split('-').map(Number); return new TZDate(year, month - 1, day, 12, 0, 0, timezone); };
  const today = dayDate(scheduleDateKey(Date.now(), timezone));
  const last = new TZDate(today.getTime() + 369 * 86400_000, timezone);
  const [mode, setMode] = useState<'dates' | 'weekly'>('dates');
  const [selected, setSelected] = useState<Date>(today);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DayAvailability[]>([]);
  const [rule, setRule] = useState<WeeklyAvailability | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const pending = useRef(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const key = scheduleDateKey(selected, timezone);
  const intervalsFor = useMemo(() => {
    const cache = new Map<string, DayAvailability[]>();
    return (day: string) => {
      if (!cache.has(day)) cache.set(day, availabilityForDate(initial, schedule, day, timezone));
      return cache.get(day)!;
    };
  }, [initial, schedule, timezone]);
  const intervals = intervalsFor(key);
  const override = schedule.dates.find(item => item.date === key && item.timezone === timezone);
  const locked = busy || submitting;
  const label = (date: Date) => scheduleLabel(date, timezone, { day: 'numeric', month: 'long', weekday: 'long' });
  function close() { setEditing(false); setRule(null); onEditing(false); requestAnimationFrame(() => trigger.current?.focus()); }
  function startEditing(button: HTMLButtonElement) {
    trigger.current = button; setDraft(intervals.length ? intervals : [{ startTime: '', endTime: '' }]);
    setError(''); setNotice(''); setEditing(true); onEditing(true);
  }
  function editRule(button: HTMLButtonElement, existing?: WeeklyAvailability) {
    trigger.current = button;
    setRule(existing ? { ...existing, weekdays: [...existing.weekdays] } : { id: crypto.randomUUID(), startDate: scheduleDateKey(Date.now(), timezone), weekdays: [0, 1, 2, 3, 4], startTime: '', endTime: '', timezone });
    setError(''); setNotice(''); setEditing(true); onEditing(true);
  }
  async function save(next: AvailabilitySchedule) {
    if (locked || pending.current) return;
    pending.current = true; setSubmitting(true); setError(''); setNotice('');
    try {
      if (await onSave(next)) { close(); setNotice('Расписание сохранено'); }
      else setError('Не удалось сохранить. Попробуйте ещё раз.');
    } catch { setError('Не удалось сохранить. Попробуйте ещё раз.'); }
    finally { pending.current = false; setSubmitting(false); }
  }
  function submitDate(event: FormEvent) {
    event.preventDefault();
    try {
      const intervals = validateDayIntervals(draft, key, timezone);
      void save({ ...schedule, dates: [...schedule.dates.filter(item => item !== override), { date: key, timezone, intervals }] });
    } catch (error) { setError(error instanceof Error ? error.message : 'Проверьте время.'); }
  }
  function submitRule(event: FormEvent) {
    event.preventDefault();
    if (!rule) return;
    if (!rule.weekdays.length) { setError('Выберите дни недели.'); return; }
    try {
      validateDayIntervals([{ startTime: rule.startTime, endTime: rule.endTime }], rule.startDate, rule.timezone);
      void save({ ...schedule, weekly: [...schedule.weekly.filter(item => item.id !== rule.id), { ...rule, weekdays: [...rule.weekdays].sort() }] });
    } catch (error) { setError(error instanceof Error ? error.message : 'Проверьте время.'); }
  }
  return <section className="cp-schedule-availability" aria-label="Моя доступность" onKeyDown={event => { if (event.key === 'Escape' && editing && !locked) { event.stopPropagation(); close(); } }}>
    <h2>Моя доступность</h2>
    <div className="cp-schedule-modes" role="group" aria-label="Настройка расписания"><button type="button" aria-pressed={mode === 'dates'} disabled={locked || editing} onClick={() => { setMode('dates'); setError(''); setNotice(''); }}>По датам</button><button type="button" aria-pressed={mode === 'weekly'} disabled={locked || editing} onClick={() => { setMode('weekly'); setError(''); setNotice(''); }}>Каждую неделю</button></div>
    {mode === 'dates' ? <div className="cp-date-schedule">
      <div className="cp-date-calendar">
        <DayPicker mode="single" required selected={selected} defaultMonth={selected} locale={ru} timeZone={timezone}
          today={today} startMonth={today} endMonth={last} navLayout="around" fixedWeeks weekStartsOn={1}
          disabled={editing || locked ? true : [{ before: today }, { after: last }]} disableNavigation={editing || locked}
          onSelect={day => { setSelected(day); setError(''); setNotice(''); }}
          modifiers={{ available: day => intervalsFor(scheduleDateKey(day, timezone)).length > 0,
            closed: schedule.dates.filter(item => item.timezone === timezone && !item.intervals.length).map(item => dayDate(item.date)) }}
          modifiersClassNames={{ available: 'cp-date-available', closed: 'cp-date-closed' }}
          labels={{ labelDayButton: label, labelNext: () => 'Следующий месяц', labelPrevious: () => 'Предыдущий месяц' }}/>
      </div>
      <div className="cp-date-detail" aria-busy={locked}>
        <h3>{label(selected)}</h3>
        {!editing ? <>
          <p className="cp-date-kind">{override ? 'Своё расписание на день' : intervals.length ? 'Доступное время' : 'Время ещё не задано'}</p>
          <div className="cp-date-times">{intervals.length ? intervals.map((item, i) => <span key={i}>{item.startTime}–{item.endTime}</span>) : <span className="cp-date-off">Без встреч</span>}</div>
          <div className="cp-date-actions"><Button type="button" disabled={locked} onClick={event => startEditing(event.currentTarget)}>{intervals.length || override ? 'Изменить время' : 'Добавить время'}</Button>{override && <Button type="button" variant="ghost" disabled={locked} onClick={() => void save({ ...schedule, dates: schedule.dates.filter(item => item !== override) })}>Вернуть обычное</Button>}</div>
        </> : <form className="cp-date-form" onSubmit={submitDate}>
          {draft.map((row, index) => <div className="cp-date-range" key={index}>
            <label>С<input autoFocus={index === 0} required type="time" step={60} disabled={locked} aria-label={`Начало интервала ${index + 1}`} value={row.startTime} onChange={event => { setDraft(rows => rows.map((item, i) => i === index ? { ...item, startTime: event.target.value } : item)); setError(''); }}/></label>
            <label>{row.endTime === '24:00' ? 'До следующего дня' : 'До'}<input required type="time" step={60} disabled={locked} aria-label={`Конец интервала ${index + 1}`} value={row.endTime === '24:00' ? '00:00' : row.endTime} onChange={event => { setDraft(rows => rows.map((item, i) => i === index ? { ...item, endTime: event.target.value === '00:00' ? '24:00' : event.target.value } : item)); setError(''); }}/></label>
            <Button type="button" variant="ghost" size="icon" disabled={locked} aria-label={`Убрать интервал ${index + 1}`} onClick={() => { setDraft(rows => rows.filter((_, i) => i !== index)); setError(''); }}><X size={18}/></Button>
          </div>)}
          {!draft.length && <p className="cp-date-off">В этот день вы недоступны</p>}
          <div className="cp-date-actions"><Button type="button" variant="ghost" disabled={locked || draft.length >= 24} onClick={() => setDraft(rows => [...rows, { startTime: '', endTime: '' }])}><Plus size={16}/>Добавить интервал</Button>{draft.length > 0 && <Button type="button" variant="ghost" disabled={locked} onClick={() => { setDraft([]); setError(''); }}>Закрыть день</Button>}</div>
          <div className="cp-date-actions"><Button type="submit" disabled={locked}>{submitting ? 'Сохраняем…' : 'Сохранить день'}</Button><Button type="button" variant="ghost" disabled={locked} onClick={close}>Отмена</Button></div>
        </form>}
      </div>
    </div> : rule ? <form className="cp-weekly-editor" onSubmit={submitRule}>
      <label>Начиная с<input autoFocus required type="date" value={rule.startDate} disabled={locked} onChange={event => setRule({ ...rule, startDate: event.target.value })}/></label>
      <fieldset className="cp-weekdays" disabled={locked}><legend>Дни недели</legend>{days.map((day, i) => <button type="button" key={day} aria-label={dayNames[i]} aria-pressed={rule.weekdays.includes(i)} onClick={() => setRule({ ...rule, weekdays: rule.weekdays.includes(i) ? rule.weekdays.filter(n => n !== i) : [...rule.weekdays, i] })}>{day}</button>)}</fieldset>
      <div className="cp-weekly-times"><label>С<input type="time" step={60} required disabled={locked} value={rule.startTime} onChange={event => setRule({ ...rule, startTime: event.target.value })}/></label><label>До<input type="time" step={60} required disabled={locked} value={rule.endTime === '24:00' ? '00:00' : rule.endTime} onChange={event => setRule({ ...rule, endTime: event.target.value === '00:00' ? '24:00' : event.target.value })}/></label></div>
      <p className="cp-date-kind">{rule.timezone}</p>
      <div className="cp-date-actions"><Button type="submit" disabled={locked}>{submitting ? 'Сохраняем…' : 'Сохранить расписание'}</Button><Button type="button" variant="ghost" disabled={locked} onClick={close}>Отмена</Button>{schedule.weekly.some(item => item.id === rule.id) && <Button type="button" variant="ghost" disabled={locked} onClick={() => void save({ ...schedule, weekly: schedule.weekly.filter(item => item.id !== rule.id) })}>Удалить интервал</Button>}</div>
    </form> : <div className="cp-weekly-blocks">
      {schedule.weekly.map(item => <div className="cp-weekly-block" key={item.id}><div className="cp-weekly-summary"><div><strong>{ruleDays(item)}</strong><span className="cp-weekly-time">{item.startTime}–{item.endTime}</span><span className="cp-date-kind">С {item.startDate.split('-').reverse().join('.')} · {item.timezone}</span></div><Button type="button" variant="secondary" disabled={locked} onClick={event => editRule(event.currentTarget, item)}>Изменить</Button></div><div className="cp-weekly-strip" aria-hidden="true">{days.map((day, i) => <span key={day} data-active={item.weekdays.includes(i)}>{day}</span>)}</div></div>)}
      <Button type="button" variant={schedule.weekly.length ? 'ghost' : 'default'} disabled={locked || schedule.weekly.length >= 20} onClick={event => editRule(event.currentTarget)}><Plus size={16}/>{schedule.weekly.length ? 'Добавить интервал' : 'Настроить расписание'}</Button>
    </div>}
    {error && <p className="cp-error" role="alert">{error}</p>}{notice && <p className="cp-date-notice" role="status">{notice}</p>}
  </section>;
}
