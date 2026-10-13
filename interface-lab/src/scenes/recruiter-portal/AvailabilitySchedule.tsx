import { useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { moscowInput, type AvailabilityBlock } from './api';

const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
export function AvailabilitySchedule({ blocks, busy, run, recruiterName }: { blocks: AvailabilityBlock[]; busy: boolean; run: (path: string, body: unknown) => Promise<boolean>; recruiterName?: string }) {
  const [name, setName] = useState('');
  const [date, setDate] = useState(() => moscowInput(new Date()).slice(0, 10));
  const [weekdays, setWeekdays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false);
  const personal = recruiterName !== undefined;
  const ownerName = (recruiterName ?? name).trim();
  const locked = busy || submitting;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || pending.current) return;
    setError(''); setSaved(false);
    if (!ownerName) { setError('Укажите имя рекрутера.'); return; }
    if (!weekdays.length || !start || !end || end <= start) { setError('Выберите дни недели и интервал для интервью.'); return; }
    pending.current = true; setSubmitting(true);
    try {
      if (await run('availability', { recruiterName: ownerName, startDate: date, weekdays: [...weekdays].sort(), startTime: start, endTime: end, timezone: 'Europe/Moscow' })) { setStart(''); setEnd(''); setSaved(true); }
    } catch { setError('Не удалось сохранить расписание. Попробуйте ещё раз.'); }
    finally { pending.current = false; setSubmitting(false); }
  }
  return <>
    <form className="rp-card rp-slot-form" onSubmit={submit} aria-busy={locked} onChange={() => { setSaved(false); setError(''); }}>
      <h2>{personal ? 'Моё расписание' : 'Время для интервью'}</h2>
      <div className={personal ? 'rp-schedule-date' : 'rp-fields'}>{!personal && <label>Рекрутер<input required disabled={locked} value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Имя и фамилия"/></label>}<label>Начиная с<input required disabled={locked} type="date" value={date} onChange={event => setDate(event.target.value)}/></label></div>
      <fieldset className="rp-weekdays" disabled={locked}><legend>Дни недели</legend>{days.map((day, index) => <button type="button" key={day} aria-pressed={weekdays.includes(index)} aria-label={['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'][index]} onClick={() => { setSaved(false); setError(''); setWeekdays(current => current.includes(index) ? current.filter(value => value !== index) : [...current, index]); }}>{day}</button>)}</fieldset>
      <div className="rp-fields"><label>С · МСК<input required disabled={locked} type="time" step={1800} value={start} onChange={event => setStart(event.target.value)}/></label><label>До · МСК<input required disabled={locked} type="time" step={1800} value={end} onChange={event => setEnd(event.target.value)}/></label></div>
      <div className="rp-section-heading"><p className="rp-muted rp-help">Интервью по 30 минут · московское время</p><Button type="submit" disabled={locked || !ownerName || !date || !start || !end || !weekdays.length}>{locked ? 'Сохраняем…' : 'Сохранить расписание'}</Button></div>
      {error && <p className="rp-error" role="alert">{error}</p>}
      {saved && <p className="rp-schedule-status" role="status">Расписание сохранено.</p>}
    </form>
    {blocks.length > 0 && <div className="rp-availability-blocks" aria-label="Постоянное расписание">{blocks.map(block => <div key={block.id}>{!personal && <strong>{block.recruiterName}</strong>}<span>{block.weekdays.map(day => days[day]).join(', ')} · {block.startTime}–{block.endTime} МСК</span></div>)}</div>}
  </>;
}
