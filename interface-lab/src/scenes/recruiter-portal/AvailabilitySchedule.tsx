import { useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { moscowInput, type AvailabilityBlock } from './api';

const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
export function AvailabilitySchedule({ blocks, busy, run }: { blocks: AvailabilityBlock[]; busy: boolean; run: (path: string, body: unknown) => Promise<boolean> }) {
  const [name, setName] = useState('');
  const [date, setDate] = useState(() => moscowInput(new Date()).slice(0, 10));
  const [weekdays, setWeekdays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!weekdays.length || !start || !end || end <= start) { setError('Выберите дни недели и интервал для интервью.'); return; }
    if (await run('availability', { recruiterName: name.trim(), startDate: date, weekdays: [...weekdays].sort(), startTime: start, endTime: end, timezone: 'Europe/Moscow' })) { setStart(''); setEnd(''); }
  }
  return <>
    <form className="rp-card rp-slot-form" onSubmit={submit}>
      <h2>Время для интервью</h2>
      <div className="rp-fields"><label>Рекрутер<input required value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Имя и фамилия"/></label><label>Начиная с<input required type="date" value={date} onChange={event => setDate(event.target.value)}/></label></div>
      <fieldset className="rp-weekdays"><legend>Дни недели</legend>{days.map((day, index) => <button type="button" key={day} aria-pressed={weekdays.includes(index)} aria-label={['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'][index]} onClick={() => setWeekdays(current => current.includes(index) ? current.filter(value => value !== index) : [...current, index])}>{day}</button>)}</fieldset>
      <div className="rp-fields"><label>С · МСК<input required type="time" step={1800} value={start} onChange={event => setStart(event.target.value)}/></label><label>До · МСК<input required type="time" step={1800} value={end} onChange={event => setEnd(event.target.value)}/></label></div>
      <div className="rp-section-heading"><p className="rp-muted rp-help">По 30 минут. Кандидат и рекрутер выбирают из одного расписания.</p><Button disabled={busy || !name.trim() || !start || !end || !weekdays.length}>Сохранить расписание</Button></div>
      {error && <p className="rp-error" role="alert">{error}</p>}
    </form>
    {blocks.length > 0 && <div className="rp-availability-blocks" aria-label="Постоянное расписание">{blocks.map(block => <div key={block.id}><strong>{block.recruiterName}</strong><span>{block.weekdays.map(day => days[day]).join(', ')} · {block.startTime}–{block.endTime} МСК</span></div>)}</div>}
  </>;
}
